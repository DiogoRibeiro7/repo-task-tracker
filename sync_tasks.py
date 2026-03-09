"""sync_tasks.py

Core engine for the repo-task-tracker GitHub Action.

Reads tracker.json from the repository root (or the path set in
TRACKER_PATH), then:

  1. Creates or updates one GitHub Issue per task in the current repo,
     labelled ``tracker``.
  2. Closes issues whose task status is ``done``, ``archived``, or
     ``cancelled``.
  3. Optionally adds every issue to a central GitHub Projects (v2) board
     and keeps the Status, Priority, Repo URL, and Next action fields
     in sync via the GraphQL API.

Environment variables
---------------------
GITHUB_TOKEN        Token with issues:write (and projects:write if using a board).
GITHUB_REPOSITORY   owner/repo of the current repository (set by Actions).
PROJECT_OWNER       GitHub login or org that owns the Project board.
PROJECT_NUMBER      Integer number of the Project board.
TRACKER_PATH        Path to tracker.json (default: tracker.json).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.error import HTTPError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_URL: str = os.environ.get("GITHUB_API_URL", "https://api.github.com")
REPOSITORY: str = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN: str = os.environ.get("GITHUB_TOKEN", "")
PROJECT_OWNER: str = os.environ.get("PROJECT_OWNER", "")
PROJECT_NUMBER_STR: str = os.environ.get("PROJECT_NUMBER", "0")
TRACKER_PATH: Path = Path(os.environ.get("TRACKER_PATH", "tracker.json"))

ISSUE_PREFIX: str = "[tracker]"
MANAGED_LABEL: str = "tracker"

OPEN_STATUSES: Set[str] = {"planned", "in_progress", "blocked", "review"}
CLOSED_STATUSES: Set[str] = {"done", "archived", "cancelled"}

VALID_PROJECT_STATUSES: Set[str] = {
    "Backlog", "Planned", "Reading", "Writing",
    "Experiments", "Revising", "Submitted",
    "Camera-ready", "Done", "Blocked", "Archived",
}

STATUS_MAP: Dict[str, str] = {
    "planned":     "Planned",
    "in_progress": "Writing",
    "review":      "Revising",
    "blocked":     "Blocked",
    "done":        "Done",
    "archived":    "Archived",
    "cancelled":   "Archived",
}

PRIORITY_MAP: Dict[str, str] = {
    "low": "Low", "medium": "Medium",
    "high": "High", "critical": "Critical",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Task:
    title: str
    description: str
    status: str
    priority: str
    labels: List[str] = field(default_factory=list)

    @property
    def issue_title(self) -> str:
        return f"{ISSUE_PREFIX} {self.title}"

    @property
    def slug(self) -> str:
        base = self.title.strip().lower()
        return re.sub(r"[^a-z0-9]+", "-", base).strip("-")

    @property
    def normalized_status(self) -> str:
        return self.status.strip().lower()

    @property
    def project_status(self) -> str:
        mapped = STATUS_MAP.get(self.normalized_status)
        if mapped:
            return mapped
        if self.status.strip() in VALID_PROJECT_STATUSES:
            return self.status.strip()
        return "Backlog"

    @property
    def project_priority(self) -> str:
        return PRIORITY_MAP.get(self.priority.strip().lower(), "Medium")

    def to_issue_body(self) -> str:
        lines: List[str] = [
            "This issue is managed automatically by the"
            " [repo-task-tracker](https://github.com/DiogoRibeiro7/repo-task-tracker)"
            " action. Edit `tracker.json` to change it.",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| **Task** | {self.title} |",
            f"| **Status** | `{self.status}` |",
            f"| **Priority** | `{self.priority}` |",
            f"| **Tracker key** | `{self.slug}` |",
            "",
        ]
        if self.description:
            lines += ["## Description", "", self.description, ""]
        return "\n".join(lines).strip() + "\n"


@dataclass
class TrackerConfig:
    tasks: List[Task]
    project_owner: str
    project_number: int


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(path: Path) -> TrackerConfig:
    """Load and validate tracker.json."""
    raw = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(raw, dict):
        _fail("tracker.json must be a JSON object.")

    tasks: List[Task] = []
    for item in raw.get("tasks", []):
        title = str(item.get("title", "")).strip()
        if not title:
            _fail(f"Task is missing a 'title': {item}")
        status = str(item.get("status", "planned")).strip().lower()
        known = OPEN_STATUSES | CLOSED_STATUSES
        if status not in known:
            print(
                f"  WARNING: unknown status '{status}' for task '{title}'."
                f" Known values: {sorted(known)}",
                file=sys.stderr,
            )
        tasks.append(Task(
            title=title,
            description=str(item.get("description", "")).strip(),
            status=status,
            priority=str(item.get("priority", "medium")).strip().lower(),
            labels=list(item.get("labels", [])),
        ))

    owner = str(raw.get("project_owner", PROJECT_OWNER)).strip()
    number_raw = raw.get("project_number", PROJECT_NUMBER_STR)
    try:
        number = int(number_raw)
    except (TypeError, ValueError):
        number = 0

    return TrackerConfig(tasks=tasks, project_owner=owner, project_number=number)


# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------

def _fail(msg: str) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(1)


def _rest(
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    expected_errors: Optional[Set[int]] = None,
) -> Any:
    if not TOKEN:
        _fail("GITHUB_TOKEN is not set.")
    if not REPOSITORY:
        _fail("GITHUB_REPOSITORY is not set.")

    url = f"{API_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers: Dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "repo-task-tracker",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if data:
        headers["Content-Type"] = "application/json"

    req = Request(url=url, data=data, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if expected_errors and exc.code in expected_errors:
            try:
                parsed: Any = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = {"message": body}
            return {"__error__": {"status": exc.code, "body": parsed}}
        _fail(f"GitHub REST {method} {path} → {exc.code}\n{body}")


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

def _graphql(query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errors") and "data" not in data:
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data["data"]


# ---------------------------------------------------------------------------
# Issue operations
# ---------------------------------------------------------------------------

def ensure_label() -> None:
    """Create the ``tracker`` label if it does not already exist."""
    resp = _rest(
        "POST",
        f"/repos/{REPOSITORY}/labels",
        {"name": MANAGED_LABEL, "color": "0075ca",
         "description": "Managed by repo-task-tracker"},
        expected_errors={422},
    )
    if isinstance(resp, dict) and "__error__" in resp:
        errors = resp["__error__"]["body"].get("errors", [])
        if any(e.get("code") == "already_exists" for e in errors):
            return
        _fail(f"Unexpected label error: {resp['__error__']['body']}")


def list_issues() -> List[Dict[str, Any]]:
    return _rest(
        "GET",
        f"/repos/{REPOSITORY}/issues"
        f"?state=all&labels={MANAGED_LABEL}&per_page=100",
    ) or []


def find_issue(
    issues: Iterable[Dict[str, Any]], task: Task
) -> Optional[Dict[str, Any]]:
    """Match by issue title or by the tracker key in the body."""
    key = f"`{task.slug}`"
    for issue in issues:
        if (
            issue.get("title") == task.issue_title
            or key in str(issue.get("body", ""))
        ):
            return issue
    return None


def create_issue(task: Task) -> Dict[str, Any]:
    issue_labels = [MANAGED_LABEL] + task.labels
    result = _rest("POST", f"/repos/{REPOSITORY}/issues", {
        "title": task.issue_title,
        "body": task.to_issue_body(),
        "labels": issue_labels,
    })
    print(f"  ✚ Created  #{result['number']}: {task.title}")
    return result


def update_issue(
    number: int, task: Task, state: Optional[str] = None
) -> None:
    payload: Dict[str, Any] = {
        "title": task.issue_title,
        "body": task.to_issue_body(),
    }
    if state:
        payload["state"] = state
    _rest("PATCH", f"/repos/{REPOSITORY}/issues/{number}", payload)
    tag = f"→ {state}" if state else "updated"
    print(f"  ↻ Updated  #{number} ({tag}): {task.title}")


# ---------------------------------------------------------------------------
# Project board operations
# ---------------------------------------------------------------------------

def get_project_meta(
    owner: str, number: int
) -> tuple[str, Dict[str, Any], Dict[str, str]]:
    query = """
    query($owner: String!, $number: Int!) {
      user(login: $owner) {
        projectV2(number: $number) { ...ProjectFields }
      }
      organization(login: $owner) {
        projectV2(number: $number) { ...ProjectFields }
      }
    }
    fragment ProjectFields on ProjectV2 {
      id
      fields(first: 50) {
        nodes {
          ... on ProjectV2FieldCommon { id name }
          ... on ProjectV2SingleSelectField {
            id name options { id name }
          }
        }
      }
    }
    """
    data = _graphql(query, {"owner": owner, "number": number})
    project = (
        (data.get("user") or {}).get("projectV2")
        or (data.get("organization") or {}).get("projectV2")
    )
    if not project:
        raise RuntimeError(
            f"Project #{number} not found for owner '{owner}'. "
            "Check PROJECT_OWNER and PROJECT_NUMBER."
        )

    fields: Dict[str, Any] = {}
    options: Dict[str, str] = {}
    for node in project["fields"]["nodes"]:
        name = node.get("name")
        if not name:
            continue
        fields[name] = node
        for opt in node.get("options", []):
            options[f"{name}:{opt['name']}"] = opt["id"]

    return project["id"], fields, options


def get_project_items(project_id: str) -> Dict[str, str]:
    """Return a mapping of issue node-id → project item id."""
    query = """
    query($id: ID!) {
      node(id: $id) {
        ... on ProjectV2 {
          items(first: 100) {
            nodes {
              id
              content { ... on Issue { id } }
            }
          }
        }
      }
    }
    """
    data = _graphql(query, {"id": project_id})
    return {
        item["content"]["id"]: item["id"]
        for item in data["node"]["items"]["nodes"]
        if (item.get("content") or {}).get("id")
    }


def add_to_project(project_id: str, issue_id: str) -> str:
    mutation = """
    mutation($pid: ID!, $cid: ID!) {
      addProjectV2ItemById(input: {projectId: $pid, contentId: $cid}) {
        item { id }
      }
    }
    """
    data = _graphql(mutation, {"pid": project_id, "cid": issue_id})
    return data["addProjectV2ItemById"]["item"]["id"]


def _set_single_select(
    project_id: str, item_id: str, field_id: str, option_id: str
) -> None:
    mutation = """
    mutation($pid: ID!, $iid: ID!, $fid: ID!, $oid: String!) {
      updateProjectV2ItemFieldValue(
        input: {projectId: $pid, itemId: $iid, fieldId: $fid,
                value: {singleSelectOptionId: $oid}}
      ) { projectV2Item { id } }
    }
    """
    _graphql(mutation, {
        "pid": project_id, "iid": item_id,
        "fid": field_id, "oid": option_id,
    })


def _set_text(
    project_id: str, item_id: str, field_id: str, value: str
) -> None:
    mutation = """
    mutation($pid: ID!, $iid: ID!, $fid: ID!, $v: String!) {
      updateProjectV2ItemFieldValue(
        input: {projectId: $pid, itemId: $iid, fieldId: $fid,
                value: {text: $v}}
      ) { projectV2Item { id } }
    }
    """
    _graphql(mutation, {
        "pid": project_id, "iid": item_id,
        "fid": field_id, "v": value,
    })


def sync_to_project(
    issue: Dict[str, Any],
    task: Task,
    project_id: str,
    project_items: Dict[str, str],
    fields: Dict[str, Any],
    options: Dict[str, str],
) -> None:
    issue_id = issue["id"]
    item_id = project_items.get(issue_id)
    if item_id is None:
        item_id = add_to_project(project_id, issue_id)
        project_items[issue_id] = item_id          # keep local cache current
        print("    ↗ Added to project board")

    status_opt = options.get(f"Status:{task.project_status}")
    priority_opt = options.get(f"Priority:{task.project_priority}")

    if status_opt and "Status" in fields:
        _set_single_select(project_id, item_id, fields["Status"]["id"], status_opt)
    if priority_opt and "Priority" in fields:
        _set_single_select(project_id, item_id, fields["Priority"]["id"], priority_opt)
    if "Repo URL" in fields:
        _set_text(project_id, item_id, fields["Repo URL"]["id"], REPOSITORY)
    if "Next action" in fields:
        _set_text(project_id, item_id, fields["Next action"]["id"], task.description)


# ---------------------------------------------------------------------------
# Main sync loop
# ---------------------------------------------------------------------------

def sync() -> None:
    missing_env = [
        v for v in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")
        if not os.environ.get(v)
    ]
    if missing_env:
        _fail(f"Missing required environment variables: {', '.join(missing_env)}")

    if not TRACKER_PATH.exists():
        _fail(f"tracker.json not found at '{TRACKER_PATH}'.")

    config = load_config(TRACKER_PATH)

    use_project = bool(config.project_owner and config.project_number)
    project_id = fields = options = project_items = None

    if use_project:
        try:
            project_id, fields, options = get_project_meta(
                config.project_owner, config.project_number
            )
            project_items = get_project_items(project_id)
            print(
                f"Project board: #{config.project_number} "
                f"({config.project_owner})\n"
            )
        except Exception as exc:
            print(
                f"WARNING: could not connect to project board: {exc}\n"
                "Continuing without project sync.",
                file=sys.stderr,
            )
            use_project = False

    ensure_label()
    existing_issues = list_issues()
    print(f"Syncing {len(config.tasks)} tasks → {REPOSITORY}\n")

    for task in config.tasks:
        existing = find_issue(existing_issues, task)
        status = task.normalized_status

        if status in OPEN_STATUSES:
            if existing is None:
                existing = create_issue(task)
            else:
                num = int(existing["number"])
                if existing.get("state") == "closed":
                    update_issue(num, task, state="open")
                else:
                    update_issue(num, task)

        elif status in CLOSED_STATUSES:
            if existing is None:
                existing = create_issue(task)
                _rest("PATCH",
                      f"/repos/{REPOSITORY}/issues/{existing['number']}",
                      {"state": "closed"})
                print("    ✕ Closed immediately")
            elif existing.get("state") == "open":
                update_issue(int(existing["number"]), task, state="closed")

        else:
            print(f"  ⚠ Skipping unknown status '{task.status}': {task.title}")
            continue

        if use_project and existing:
            sync_to_project(
                existing, task,
                project_id, project_items, fields, options,  # type: ignore[arg-type]
            )

    print("\nDone.")


if __name__ == "__main__":
    sync()
