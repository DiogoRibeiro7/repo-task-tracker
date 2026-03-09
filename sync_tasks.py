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

import argparse
import json
import os
import re
import sys
import time
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
    depends_on: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    milestone: Optional[int] = None

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

    def to_issue_body(
        self,
        issue_numbers_by_title: Optional[Dict[str, int]] = None,
    ) -> str:
        issue_numbers_by_title = issue_numbers_by_title or {}
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
        ]
        if self.assignees:
            lines.append(f"| **Assignees** | `{', '.join(self.assignees)}` |")
        if self.milestone is not None:
            lines.append(f"| **Milestone** | `{self.milestone}` |")
        lines.append("")
        if self.description:
            lines += ["## Description", "", self.description, ""]
        if self.depends_on:
            lines += ["## Dependencies", ""]
            for dep in self.depends_on:
                if dep in issue_numbers_by_title:
                    lines.append(f"- [ ] #{issue_numbers_by_title[dep]} {dep}")
                else:
                    lines.append(f"- [ ] {dep}")
            lines.append("")
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
        milestone_raw = item.get("milestone")
        milestone: Optional[int]
        if milestone_raw is None or milestone_raw == "":
            milestone = None
        else:
            try:
                milestone = int(milestone_raw)
            except (TypeError, ValueError):
                milestone = None

        tasks.append(Task(
            title=title,
            description=str(item.get("description", "")).strip(),
            status=status,
            priority=str(item.get("priority", "medium")).strip().lower(),
            labels=list(item.get("labels", [])),
            depends_on=list(item.get("depends_on", [])),
            assignees=list(item.get("assignees", [])),
            milestone=milestone,
        ))

    known_titles = {task.title for task in tasks}
    for task in tasks:
        for dep in task.depends_on:
            if dep not in known_titles:
                print(
                    f"WARNING: task '{task.title}' depends on unknown task '{dep}'.",
                    file=sys.stderr,
                )

    detect_cycles(tasks)

    owner = str(raw.get("project_owner", PROJECT_OWNER)).strip()
    number_raw = raw.get("project_number", PROJECT_NUMBER_STR)
    try:
        number = int(number_raw)
    except (TypeError, ValueError):
        number = 0

    return TrackerConfig(tasks=tasks, project_owner=owner, project_number=number)


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_dry_run() -> bool:
    return _is_truthy(os.environ.get("DRY_RUN", "false"))


def _find_cycles(tasks: List[Task]) -> List[List[str]]:
    graph: Dict[str, List[str]] = {task.title: list(task.depends_on) for task in tasks}
    visited: Set[str] = set()
    active: Set[str] = set()
    path: List[str] = []
    cycles: List[List[str]] = []

    def dfs(node: str) -> None:
        if node in active:
            if node in path:
                idx = path.index(node)
                cycles.append(path[idx:] + [node])
            return
        if node in visited:
            return

        visited.add(node)
        active.add(node)
        path.append(node)
        for dep in graph.get(node, []):
            if dep in graph:
                dfs(dep)
        path.pop()
        active.remove(node)

    for node in graph:
        dfs(node)
    return cycles


def detect_cycles(tasks: List[Task]) -> None:
    cycles = _find_cycles(tasks)
    if cycles:
        cycle_text = "; ".join(" -> ".join(cycle) for cycle in cycles)
        _fail(f"Circular dependencies detected: {cycle_text}")


def validate_config(config: TrackerConfig, source: str = "tracker.json") -> List[str]:
    errors: List[str] = []
    known_statuses = OPEN_STATUSES | CLOSED_STATUSES
    known_priorities = set(PRIORITY_MAP.keys())
    seen_titles: Set[str] = set()
    known_titles = {task.title for task in config.tasks}

    for task in config.tasks:
        if task.title in seen_titles:
            errors.append(f"[{source}] Duplicate task title: '{task.title}'.")
        seen_titles.add(task.title)

        status = task.normalized_status
        if status not in known_statuses:
            errors.append(
                f"[{source}] Task '{task.title}' has invalid status '{task.status}'. "
                f"Known values: {sorted(known_statuses)}."
            )

        priority = task.priority.strip().lower()
        if priority not in known_priorities:
            errors.append(
                f"[{source}] Task '{task.title}' has invalid priority '{task.priority}'. "
                f"Known values: {sorted(known_priorities)}."
            )

        for dep in task.depends_on:
            if not isinstance(dep, str):
                errors.append(
                    f"[{source}] Task '{task.title}' has a non-string dependency: {dep!r}."
                )
                continue
            if dep not in known_titles:
                errors.append(
                    f"[{source}] Task '{task.title}' depends on unknown task '{dep}'."
                )

        for label in task.labels:
            if not isinstance(label, str):
                errors.append(
                    f"[{source}] Task '{task.title}' has a non-string label: {label!r}."
                )

        for assignee in task.assignees:
            if not isinstance(assignee, str):
                errors.append(
                    f"[{source}] Task '{task.title}' has a non-string assignee: {assignee!r}."
                )

    for cycle in _find_cycles(config.tasks):
        errors.append(f"[{source}] Dependency cycle detected: {' -> '.join(cycle)}.")

    return errors


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
    retries_left = 1
    while True:
        try:
            with urlopen(req) as resp:
                _check_rate_limit(resp.headers)
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else None
        except HTTPError as exc:
            retry_after_raw: Optional[str] = None
            if exc.headers is not None:
                retry_after_raw = exc.headers.get("retry-after")
            if exc.code == 403 and retry_after_raw is not None and retries_left > 0:
                retries_left -= 1
                try:
                    retry_seconds = float(retry_after_raw)
                except (TypeError, ValueError):
                    retry_seconds = 1.0
                time.sleep(max(0.0, retry_seconds))
                continue

            body = exc.read().decode("utf-8", errors="replace")
            if expected_errors and exc.code in expected_errors:
                try:
                    parsed: Any = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    parsed = {"message": body}
                return {"__error__": {"status": exc.code, "body": parsed}}
            _fail(f"GitHub REST {method} {path} → {exc.code}\n{body}")


def _check_rate_limit(headers: Any) -> None:
    remaining_raw = headers.get("x-ratelimit-remaining")
    reset_raw = headers.get("x-ratelimit-reset")
    if remaining_raw is None or reset_raw is None:
        return

    try:
        remaining = int(remaining_raw)
        reset_ts = int(reset_raw)
    except (TypeError, ValueError):
        return

    try:
        buffer = int(os.environ.get("RATELIMIT_BUFFER", "10"))
    except (TypeError, ValueError):
        buffer = 10

    if remaining > buffer:
        return

    wait_seconds = (reset_ts + 1) - time.time()
    if wait_seconds > 0:
        time.sleep(wait_seconds)


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


def create_issue(
    task: Task,
    issue_numbers_by_title: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    issue_labels = [MANAGED_LABEL] + task.labels
    payload: Dict[str, Any] = {
        "title": task.issue_title,
        "body": task.to_issue_body(issue_numbers_by_title),
        "labels": issue_labels,
    }
    if task.assignees:
        payload["assignees"] = task.assignees
    if task.milestone is not None:
        payload["milestone"] = task.milestone

    if _is_dry_run():
        print(
            f"[DRY RUN] Would create issue for task '{task.title}' "
            f"with labels {issue_labels}."
        )
        return {
            "number": 0,
            "title": task.issue_title,
            "state": "open",
            "body": task.to_issue_body(issue_numbers_by_title),
            "id": f"DRYRUN_{task.slug}",
        }
    result = _rest("POST", f"/repos/{REPOSITORY}/issues", payload)
    print(f"  ✚ Created  #{result['number']}: {task.title}")
    return result


def update_issue(
    number: int,
    task: Task,
    state: Optional[str] = None,
    issue_numbers_by_title: Optional[Dict[str, int]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "title": task.issue_title,
        "body": task.to_issue_body(issue_numbers_by_title),
    }
    if task.assignees:
        payload["assignees"] = task.assignees
    if task.milestone is not None:
        payload["milestone"] = task.milestone
    if state:
        payload["state"] = state
    if _is_dry_run():
        if state:
            print(
                f"[DRY RUN] Would update issue #{number} "
                f"for task '{task.title}' and set state='{state}'."
            )
        else:
            print(f"[DRY RUN] Would update issue #{number} for task '{task.title}'.")
        return
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
    status_opt = options.get(f"Status:{task.project_status}")
    priority_opt = options.get(f"Priority:{task.project_priority}")

    if _is_dry_run():
        item_id = project_items.get(issue_id)
        if item_id is None:
            print(f"[DRY RUN] Would add issue '{task.title}' to project board.")
            item_id = f"DRYRUN_ITEM_{task.slug}"
            project_items[issue_id] = item_id

        if status_opt and "Status" in fields:
            print(
                f"[DRY RUN] Would set project Status for '{task.title}' "
                f"to '{task.project_status}'."
            )
        if priority_opt and "Priority" in fields:
            print(
                f"[DRY RUN] Would set project Priority for '{task.title}' "
                f"to '{task.project_priority}'."
            )
        if "Repo URL" in fields:
            print(f"[DRY RUN] Would set project Repo URL to '{REPOSITORY}'.")
        if "Next action" in fields:
            print(
                f"[DRY RUN] Would set project Next action for '{task.title}'."
            )
        return

    item_id = project_items.get(issue_id)
    if item_id is None:
        item_id = add_to_project(project_id, issue_id)
        project_items[issue_id] = item_id          # keep local cache current
        print("    ↗ Added to project board")

    if status_opt and "Status" in fields:
        _set_single_select(project_id, item_id, fields["Status"]["id"], status_opt)
    if priority_opt and "Priority" in fields:
        _set_single_select(project_id, item_id, fields["Priority"]["id"], priority_opt)
    if "Repo URL" in fields:
        _set_text(project_id, item_id, fields["Repo URL"]["id"], REPOSITORY)
    if "Next action" in fields:
        _set_text(project_id, item_id, fields["Next action"]["id"], task.description)


def find_orphan_issues(
    issues: Iterable[Dict[str, Any]],
    tasks: Iterable[Task],
) -> List[Dict[str, Any]]:
    known_titles = {task.issue_title for task in tasks}
    known_keys = {f"`{task.slug}`" for task in tasks}
    orphans: List[Dict[str, Any]] = []

    for issue in issues:
        title = str(issue.get("title", ""))
        body = str(issue.get("body", ""))
        title_match = title in known_titles
        body_match = any(key in body for key in known_keys)
        if not title_match and not body_match:
            orphans.append(issue)
    return orphans


def handle_orphans(
    issues: Iterable[Dict[str, Any]],
    tasks: Iterable[Task],
    mode: str,
) -> None:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "ignore":
        return

    if normalized_mode not in {"warn", "close"}:
        print(
            f"WARNING: unknown on-orphan mode '{mode}', falling back to 'warn'.",
            file=sys.stderr,
        )
        normalized_mode = "warn"

    for issue in find_orphan_issues(issues, tasks):
        number = issue.get("number")
        title = issue.get("title", "<unknown>")
        if normalized_mode == "warn":
            print(
                f"WARNING: orphan tracker issue #{number}: {title}",
                file=sys.stderr,
            )
            continue

        if issue.get("state") == "closed":
            continue
        if _is_dry_run():
            print(f"[DRY RUN] Would close orphan issue #{number}: {title}")
            continue

        _rest("PATCH", f"/repos/{REPOSITORY}/issues/{number}", {"state": "closed"})
        print(f"  ✕ Closed orphan #{number}: {title}")


def write_step_summary(path: Path, rows: List[Dict[str, str]]) -> None:
    counts = {"created": 0, "updated": 0, "reopened": 0, "closed": 0}
    for row in rows:
        action = row["action"]
        if action in counts:
            counts[action] += 1

    lines = [
        "## repo-task-tracker summary",
        "",
        "| Action | Count |",
        "|---|---:|",
        f"| created | {counts['created']} |",
        f"| updated | {counts['updated']} |",
        f"| reopened | {counts['reopened']} |",
        f"| closed | {counts['closed']} |",
        "",
        "| Issue | Title | Result |",
        "|---:|---|---|",
    ]
    for row in rows:
        lines.append(f"| #{row['number']} | {row['title']} | {row['action']} |")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main sync loop
# ---------------------------------------------------------------------------

def _resolve_tracker_paths() -> List[Path]:
    tracker_glob = os.environ.get("TRACKER_GLOB", "").strip()
    if tracker_glob:
        return sorted(Path(".").glob(tracker_glob))
    return [TRACKER_PATH]


def sync_one(path: Path) -> None:
    if not path.exists():
        _fail(f"tracker.json not found at '{path}'.")

    config = load_config(path)
    validate_only = _is_truthy(os.environ.get("VALIDATE_ONLY", "false"))

    if validate_only:
        errors = validate_config(config, source=str(path))
        if errors:
            print("Validation failed:", file=sys.stderr)
            for i, msg in enumerate(errors, start=1):
                print(f"{i}. {msg}", file=sys.stderr)
            raise SystemExit(1)
        print("Validation passed.")
        return

    missing_env = [
        v for v in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")
        if not os.environ.get(v)
    ]
    if missing_env:
        _fail(f"Missing required environment variables: {', '.join(missing_env)}")

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
    issue_numbers_by_title: Dict[str, int] = {}
    for issue in existing_issues:
        title = str(issue.get("title", ""))
        if title.startswith(f"{ISSUE_PREFIX} "):
            task_title = title[len(f"{ISSUE_PREFIX} "):]
            number = issue.get("number")
            if isinstance(number, int):
                issue_numbers_by_title[task_title] = number
    print(f"Syncing {len(config.tasks)} tasks → {REPOSITORY}\n")
    summary_rows: List[Dict[str, str]] = []

    for task in config.tasks:
        existing = find_issue(existing_issues, task)
        status = task.normalized_status

        if status in OPEN_STATUSES:
            if existing is None:
                existing = create_issue(task, issue_numbers_by_title)
                created_number = existing.get("number")
                if isinstance(created_number, int) and created_number > 0:
                    issue_numbers_by_title[task.title] = created_number
                summary_rows.append({
                    "number": str(existing.get("number", "?")),
                    "title": str(existing.get("title", task.issue_title)),
                    "action": "created",
                })
            else:
                num = int(existing["number"])
                if existing.get("state") == "closed":
                    update_issue(
                        num,
                        task,
                        state="open",
                        issue_numbers_by_title=issue_numbers_by_title,
                    )
                    summary_rows.append({
                        "number": str(existing.get("number", num)),
                        "title": str(existing.get("title", task.issue_title)),
                        "action": "reopened",
                    })
                else:
                    update_issue(
                        num,
                        task,
                        issue_numbers_by_title=issue_numbers_by_title,
                    )
                    summary_rows.append({
                        "number": str(existing.get("number", num)),
                        "title": str(existing.get("title", task.issue_title)),
                        "action": "updated",
                    })

        elif status in CLOSED_STATUSES:
            if existing is None:
                existing = create_issue(task, issue_numbers_by_title)
                created_number = existing.get("number")
                if isinstance(created_number, int) and created_number > 0:
                    issue_numbers_by_title[task.title] = created_number
                summary_rows.append({
                    "number": str(existing.get("number", "?")),
                    "title": str(existing.get("title", task.issue_title)),
                    "action": "created",
                })
                if _is_dry_run():
                    print(
                        f"[DRY RUN] Would immediately close issue "
                        f"for task '{task.title}'."
                    )
                else:
                    _rest("PATCH",
                          f"/repos/{REPOSITORY}/issues/{existing['number']}",
                          {"state": "closed"})
                    print("    ✕ Closed immediately")
                summary_rows.append({
                    "number": str(existing.get("number", "?")),
                    "title": str(existing.get("title", task.issue_title)),
                    "action": "closed",
                })
            elif existing.get("state") == "open":
                update_issue(
                    int(existing["number"]),
                    task,
                    state="closed",
                    issue_numbers_by_title=issue_numbers_by_title,
                )
                summary_rows.append({
                    "number": str(existing.get("number", "?")),
                    "title": str(existing.get("title", task.issue_title)),
                    "action": "closed",
                })

        else:
            print(f"  ⚠ Skipping unknown status '{task.status}': {task.title}")
            continue

        if use_project and existing:
            sync_to_project(
                existing, task,
                project_id, project_items, fields, options,  # type: ignore[arg-type]
            )

    handle_orphans(
        existing_issues,
        config.tasks,
        os.environ.get("ON_ORPHAN", "warn"),
    )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        write_step_summary(Path(summary_path), summary_rows)

    print("\nDone.")


def sync() -> None:
    paths = _resolve_tracker_paths()
    if not paths:
        _fail("No tracker files found for TRACKER_GLOB pattern.")

    failed = False
    for path in paths:
        print(f"\n=== Processing tracker file: {path} ===")
        try:
            sync_one(path)
        except SystemExit as exc:
            failed = True
            code = exc.code if isinstance(exc.code, int) else 1
            print(
                f"ERROR: failed to process '{path}' (exit code {code}).",
                file=sys.stderr,
            )
        except Exception as exc:
            failed = True
            print(f"ERROR: failed to process '{path}': {exc}", file=sys.stderr)

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser(description="Sync tracker.json to GitHub.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned writes without creating/updating issues or project items.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate tracker.json and exit without making API calls.",
    )
    args = parser.parse_args()
    if args.dry_run:
        os.environ["DRY_RUN"] = "true"
    if args.validate:
        os.environ["VALIDATE_ONLY"] = "true"
    sync()
