"""Tests for sync_tasks.py"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any, List
from urllib.error import HTTPError

import pytest

from conftest import make_issue, make_task, sync_tasks as st

Task = st.Task
TrackerConfig = st.TrackerConfig


# ===========================================================================
# Task dataclass
# ===========================================================================

class TestTaskSlug:
    def test_simple_title(self):
        t = make_task(title="Core implementation")
        assert t.slug == "core-implementation"

    def test_special_characters_collapsed(self):
        t = make_task(title="Tests & CI/CD: passing!")
        assert t.slug == "tests-ci-cd-passing"

    def test_leading_trailing_hyphens_stripped(self):
        t = make_task(title="  !! My Task !!  ")
        assert not t.slug.startswith("-")
        assert not t.slug.endswith("-")

    def test_unicode_lowercased(self):
        t = make_task(title="UPPER CASE TASK")
        assert t.slug == "upper-case-task"


class TestTaskIssueTitle:
    def test_prefix_prepended(self):
        t = make_task(title="Docs")
        assert t.issue_title == "[tracker] Docs"


class TestTaskProjectStatus:
    @pytest.mark.parametrize("status,expected", [
        ("planned",     "Planned"),
        ("in_progress", "Writing"),
        ("review",      "Revising"),
        ("blocked",     "Blocked"),
        ("done",        "Done"),
        ("archived",    "Archived"),
        ("cancelled",   "Archived"),
        ("unknown_xyz", "Backlog"),        # falls back to Backlog
        ("Writing",     "Writing"),        # valid project status passes through
    ])
    def test_mapping(self, status, expected):
        t = make_task(status=status)
        assert t.project_status == expected


class TestTaskProjectPriority:
    @pytest.mark.parametrize("priority,expected", [
        ("low",      "Low"),
        ("medium",   "Medium"),
        ("high",     "High"),
        ("critical", "Critical"),
        ("MEDIUM",   "Medium"),   # case-insensitive
        ("bogus",    "Medium"),   # defaults to Medium
    ])
    def test_mapping(self, priority, expected):
        t = make_task(priority=priority)
        assert t.project_priority == expected


class TestTaskNormalizedStatus:
    def test_strips_and_lowercases(self):
        t = make_task(status="  In_Progress  ")
        assert t.normalized_status == "in_progress"


class TestTaskToIssueBody:
    def test_contains_tracker_key(self):
        t = make_task(title="Core implementation", status="planned")
        body = t.to_issue_body()
        assert f"`{t.slug}`" in body

    def test_contains_status(self):
        t = make_task(status="in_progress")
        assert "`in_progress`" in t.to_issue_body()

    def test_contains_description_when_set(self):
        t = make_task(description="Do the hard stuff.")
        assert "Do the hard stuff." in t.to_issue_body()

    def test_description_section_absent_when_empty(self):
        t = make_task(description="")
        assert "## Description" not in t.to_issue_body()

    def test_ends_with_newline(self):
        t = make_task()
        assert t.to_issue_body().endswith("\n")

    def test_action_link_present(self):
        t = make_task()
        assert "repo-task-tracker" in t.to_issue_body()

    def test_includes_assignees_and_milestone_when_set(self):
        t = make_task(assignees=["alice", "bob"], milestone=3)
        body = t.to_issue_body()
        assert "`alice, bob`" in body
        assert "`3`" in body

    def test_dependencies_section_with_issue_numbers(self):
        t = make_task(title="Task A", depends_on=["Task B", "Task C"])
        body = t.to_issue_body({"Task B": 12})
        assert "## Dependencies" in body
        assert "- [ ] #12 Task B" in body
        assert "- [ ] Task C" in body


# ===========================================================================
# load_config
# ===========================================================================

class TestLoadConfig:
    def _write(self, tmp_path: Path, data: Any) -> Path:
        p = tmp_path / "tracker.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_loads_tasks(self, tmp_path):
        p = self._write(tmp_path, {
            "project_owner": "DiogoRibeiro7",
            "project_number": 3,
            "tasks": [
                {"title": "Task A", "status": "planned", "priority": "high"},
                {"title": "Task B", "status": "done",    "priority": "low"},
            ],
        })
        config = st.load_config(p)
        assert len(config.tasks) == 2
        assert config.tasks[0].title == "Task A"
        assert config.tasks[1].status == "done"

    def test_project_owner_and_number(self, tmp_path):
        p = self._write(tmp_path, {
            "project_owner": "DiogoRibeiro7",
            "project_number": 7,
            "tasks": [],
        })
        config = st.load_config(p)
        assert config.project_owner == "DiogoRibeiro7"
        assert config.project_number == 7

    def test_task_without_title_fails(self, tmp_path):
        p = self._write(tmp_path, {"tasks": [{"status": "planned"}]})
        with pytest.raises(SystemExit):
            st.load_config(p)

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "tracker.json"
        p.write_text("{ not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            st.load_config(p)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            st.load_config(tmp_path / "does_not_exist.json")

    def test_default_status_is_planned(self, tmp_path):
        p = self._write(tmp_path, {"tasks": [{"title": "X"}]})
        config = st.load_config(p)
        assert config.tasks[0].status == "planned"

    def test_default_priority_is_medium(self, tmp_path):
        p = self._write(tmp_path, {"tasks": [{"title": "X"}]})
        config = st.load_config(p)
        assert config.tasks[0].priority == "medium"

    def test_project_number_as_string(self, tmp_path):
        p = self._write(tmp_path, {
            "project_number": "5",
            "tasks": [],
        })
        config = st.load_config(p)
        assert config.project_number == 5

    def test_extra_labels_preserved(self, tmp_path):
        p = self._write(tmp_path, {
            "tasks": [{"title": "X", "labels": ["bug", "help wanted"]}]
        })
        config = st.load_config(p)
        assert config.tasks[0].labels == ["bug", "help wanted"]

    def test_non_dict_root_fails(self, tmp_path):
        p = tmp_path / "tracker.json"
        p.write_text('["not", "a", "dict"]', encoding="utf-8")
        with pytest.raises(SystemExit):
            st.load_config(p)

    def test_unknown_status_warns_but_loads(self, tmp_path, capsys):
        p = self._write(tmp_path, {
            "tasks": [{"title": "X", "status": "whatever"}]
        })
        config = st.load_config(p)
        assert config.tasks[0].status == "whatever"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_assignees_and_milestone_loaded(self, tmp_path):
        p = self._write(tmp_path, {
            "tasks": [{
                "title": "X",
                "assignees": ["alice", "bob"],
                "milestone": 4,
            }]
        })
        config = st.load_config(p)
        assert config.tasks[0].assignees == ["alice", "bob"]
        assert config.tasks[0].milestone == 4

    def test_unknown_dependency_warns(self, tmp_path, capsys):
        p = self._write(tmp_path, {
            "tasks": [{"title": "A", "depends_on": ["Missing"]}]
        })
        st.load_config(p)
        err = capsys.readouterr().err
        assert "depends on unknown task 'Missing'" in err

    def test_cycle_detection_direct(self, tmp_path):
        p = self._write(tmp_path, {
            "tasks": [
                {"title": "A", "depends_on": ["B"]},
                {"title": "B", "depends_on": ["A"]},
            ]
        })
        with pytest.raises(SystemExit):
            st.load_config(p)

    def test_cycle_detection_transitive(self, tmp_path):
        p = self._write(tmp_path, {
            "tasks": [
                {"title": "A", "depends_on": ["B"]},
                {"title": "B", "depends_on": ["C"]},
                {"title": "C", "depends_on": ["A"]},
            ]
        })
        with pytest.raises(SystemExit):
            st.load_config(p)


# ===========================================================================
# find_issue
# ===========================================================================

class TestFindIssue:
    def test_matches_by_title(self):
        task = make_task(title="Core implementation")
        issue = make_issue(title=task.issue_title)
        assert st.find_issue([issue], task) is issue

    def test_matches_by_tracker_key_in_body(self):
        task = make_task(title="Core implementation")
        issue = make_issue(title="[tracker] Something else",
                           body=f"`{task.slug}`")
        assert st.find_issue([issue], task) is issue

    def test_returns_none_when_not_found(self):
        task = make_task(title="Missing task")
        issue = make_issue(title="[tracker] Different task")
        assert st.find_issue([issue], task) is None

    def test_empty_list_returns_none(self):
        assert st.find_issue([], make_task()) is None

    def test_first_match_returned(self):
        task = make_task(title="Task")
        i1 = make_issue(number=1, title=task.issue_title)
        i2 = make_issue(number=2, title=task.issue_title)
        assert st.find_issue([i1, i2], task)["number"] == 1


# ===========================================================================
# ensure_label
# ===========================================================================

class TestEnsureLabel:
    def test_creates_label_successfully(self, monkeypatch):
        calls = []
        monkeypatch.setattr(st, "_rest",
                            lambda *a, **kw: calls.append(a) or {"id": 1})
        st.ensure_label()
        assert any("labels" in str(c) for c in calls)

    def test_ignores_already_exists_422(self, monkeypatch):
        def fake_rest(*a, **kw):
            return {"__error__": {"status": 422, "body": {"errors": [
                {"resource": "Label", "code": "already_exists"}
            ]}}}
        monkeypatch.setattr(st, "_rest", fake_rest)
        st.ensure_label()   # must not raise

    def test_fails_on_unexpected_422(self, monkeypatch):
        def fake_rest(*a, **kw):
            return {"__error__": {"status": 422, "body": {"errors": [
                {"resource": "Label", "code": "invalid"}
            ]}}}
        monkeypatch.setattr(st, "_rest", fake_rest)
        with pytest.raises(SystemExit):
            st.ensure_label()


# ===========================================================================
# sync() — full integration (all I/O mocked)
# ===========================================================================

class TestSync:
    """Tests for the main sync() loop with all external calls patched."""

    def _run(self, monkeypatch, tmp_path, tasks_data, existing_issues=None):
        """
        Patch the module, write a tracker.json, run sync(), and return
        the list of recorded actions.
        """
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "project_owner": "",
            "project_number": 0,
            "tasks": tasks_data,
        }), encoding="utf-8")

        actions: List[Any] = []

        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "DiogoRibeiro7/test-repo")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label",
                            lambda: actions.append("ensure_label"))
        monkeypatch.setattr(st, "list_issues",
                            lambda: existing_issues or [])
        monkeypatch.setattr(st, "create_issue",
                            lambda t, *args, **kwargs: actions.append(("create", t.title))
                            or make_issue(title=t.issue_title))
        monkeypatch.setattr(st, "update_issue",
                            lambda n, t, state=None, **kwargs:
                            actions.append(("update", n, t.title, state)))
        monkeypatch.setattr(st, "_rest", lambda *a, **kw: None)

        st.sync()
        return actions

    # --- planned task, no existing issue → create ---
    def test_creates_issue_for_new_planned_task(self, monkeypatch, tmp_path):
        actions = self._run(monkeypatch, tmp_path,
                            [{"title": "New task", "status": "planned"}])
        assert ("create", "New task") in actions

    # --- in_progress task, open issue → update ---
    def test_updates_existing_open_issue(self, monkeypatch, tmp_path):
        task = make_task(title="Existing", status="in_progress")
        issue = make_issue(number=7, title=task.issue_title, state="open")
        actions = self._run(
            monkeypatch, tmp_path,
            [{"title": "Existing", "status": "in_progress"}],
            existing_issues=[issue],
        )
        assert ("update", 7, "Existing", None) in actions

    # --- draft re-opens a closed issue ---
    def test_reopens_closed_issue(self, monkeypatch, tmp_path):
        task = make_task(title="Reopened", status="in_progress")
        issue = make_issue(number=3, title=task.issue_title, state="closed")
        actions = self._run(
            monkeypatch, tmp_path,
            [{"title": "Reopened", "status": "in_progress"}],
            existing_issues=[issue],
        )
        assert ("update", 3, "Reopened", "open") in actions

    # --- done task with open issue → close ---
    def test_closes_open_issue_for_done_task(self, monkeypatch, tmp_path):
        task = make_task(title="Done task", status="done")
        issue = make_issue(number=9, title=task.issue_title, state="open")
        actions = self._run(
            monkeypatch, tmp_path,
            [{"title": "Done task", "status": "done"}],
            existing_issues=[issue],
        )
        assert ("update", 9, "Done task", "closed") in actions

    # --- done task with no issue → create then close ---
    def test_creates_and_closes_for_done_task_with_no_issue(
        self, monkeypatch, tmp_path
    ):
        actions = self._run(
            monkeypatch, tmp_path,
            [{"title": "Done already", "status": "done"}],
        )
        assert ("create", "Done already") in actions

    # --- already closed done task → no action ---
    def test_skips_already_closed_done_task(self, monkeypatch, tmp_path):
        task = make_task(title="Already closed", status="done")
        issue = make_issue(number=5, title=task.issue_title, state="closed")
        actions = self._run(
            monkeypatch, tmp_path,
            [{"title": "Already closed", "status": "done"}],
            existing_issues=[issue],
        )
        # no update should be called for an already-closed done issue
        update_calls = [a for a in actions if isinstance(a, tuple) and a[0] == "update"]
        assert not update_calls

    # --- ensure_label always called ---
    def test_ensure_label_always_called(self, monkeypatch, tmp_path):
        actions = self._run(monkeypatch, tmp_path, [])
        assert "ensure_label" in actions

    # --- unknown status → skip ---
    def test_skips_unknown_status(self, monkeypatch, tmp_path, capsys):
        actions = self._run(
            monkeypatch, tmp_path,
            [{"title": "Weird", "status": "nonexistent"}],
        )
        assert ("create", "Weird") not in actions

    # --- multiple tasks processed independently ---
    def test_multiple_tasks_all_processed(self, monkeypatch, tmp_path):
        actions = self._run(monkeypatch, tmp_path, [
            {"title": "Task A", "status": "planned"},
            {"title": "Task B", "status": "planned"},
            {"title": "Task C", "status": "planned"},
        ])
        created = {a[1] for a in actions if isinstance(a, tuple) and a[0] == "create"}
        assert created == {"Task A", "Task B", "Task C"}

    # --- missing env var → fail ---
    def test_missing_github_token_fails(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({"tasks": []}), encoding="utf-8")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        with pytest.raises(SystemExit):
            st.sync()

    # --- missing tracker.json → fail ---
    def test_missing_tracker_json_fails(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr(st, "TRACKER_PATH", tmp_path / "nope.json")
        with pytest.raises(SystemExit):
            st.sync()


class TestRestHelpers:
    def test_load_config_invalid_project_number_defaults_to_zero(self, tmp_path):
        p = tmp_path / "tracker.json"
        p.write_text(json.dumps({"project_number": "oops", "tasks": []}), encoding="utf-8")
        config = st.load_config(p)
        assert config.project_number == 0

    def test_rest_fails_without_token(self, monkeypatch):
        monkeypatch.setattr(st, "TOKEN", "")
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")
        with pytest.raises(SystemExit):
            st._rest("GET", "/repos/owner/repo/issues")

    def test_rest_fails_without_repository(self, monkeypatch):
        monkeypatch.setattr(st, "TOKEN", "token")
        monkeypatch.setattr(st, "REPOSITORY", "")
        with pytest.raises(SystemExit):
            st._rest("GET", "/repos/owner/repo/issues")

    def test_rest_success_and_empty_body(self, monkeypatch):
        monkeypatch.setattr(st, "TOKEN", "token")
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")

        class Resp:
            def __init__(self, payload):
                self.payload = payload
                self.headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return self.payload

        payloads = [b'{"ok": true}', b""]

        def fake_urlopen(_req):
            return Resp(payloads.pop(0))

        monkeypatch.setattr(st, "urlopen", fake_urlopen)
        assert st._rest("GET", "/x") == {"ok": True}
        assert st._rest("GET", "/x") is None

    def test_rest_expected_http_error_parses_json(self, monkeypatch):
        monkeypatch.setattr(st, "TOKEN", "token")
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")

        def fake_urlopen(_req):
            raise HTTPError(
                url="https://api.github.com/x",
                code=422,
                msg="unprocessable",
                hdrs=None,
                fp=BytesIO(b'{"errors":[{"code":"already_exists"}]}'),
            )

        monkeypatch.setattr(st, "urlopen", fake_urlopen)
        result = st._rest("POST", "/x", expected_errors={422})
        assert result["__error__"]["status"] == 422
        assert result["__error__"]["body"]["errors"][0]["code"] == "already_exists"

    def test_rest_expected_http_error_non_json_body(self, monkeypatch):
        monkeypatch.setattr(st, "TOKEN", "token")
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")

        def fake_urlopen(_req):
            raise HTTPError(
                url="https://api.github.com/x",
                code=404,
                msg="not found",
                hdrs=None,
                fp=BytesIO(b"plain error body"),
            )

        monkeypatch.setattr(st, "urlopen", fake_urlopen)
        result = st._rest("GET", "/x", expected_errors={404})
        assert result["__error__"]["body"]["message"] == "plain error body"

    def test_list_issues_returns_empty_when_rest_none(self, monkeypatch):
        monkeypatch.setattr(st, "_rest", lambda *a, **k: None)
        assert st.list_issues() == []

    def test_create_issue_calls_rest_with_labels(self, monkeypatch):
        calls = []

        def fake_rest(method, path, payload=None, expected_errors=None):
            calls.append((method, path, payload, expected_errors))
            return {"number": 12}

        monkeypatch.setattr(st, "_rest", fake_rest)
        task = make_task(title="Feature", labels=["bug"])
        issue = st.create_issue(task)
        assert issue["number"] == 12
        assert calls[0][0] == "POST"
        assert calls[0][2]["labels"] == ["tracker", "bug"]

    def test_create_issue_includes_assignees_and_milestone(self, monkeypatch):
        calls = []

        def fake_rest(method, path, payload=None, expected_errors=None):
            calls.append((method, path, payload, expected_errors))
            return {"number": 33}

        monkeypatch.setattr(st, "_rest", fake_rest)
        task = make_task(title="Feature", assignees=["alice"], milestone=2)
        st.create_issue(task)
        payload = calls[0][2]
        assert payload["assignees"] == ["alice"]
        assert payload["milestone"] == 2

    def test_create_issue_missing_assignees_and_milestone_is_safe(self, monkeypatch):
        calls = []

        def fake_rest(method, path, payload=None, expected_errors=None):
            calls.append((method, path, payload, expected_errors))
            return {"number": 34}

        monkeypatch.setattr(st, "_rest", fake_rest)
        st.create_issue(make_task(title="No extras"))
        payload = calls[0][2]
        assert "assignees" not in payload
        assert "milestone" not in payload

    def test_update_issue_sets_state_when_provided(self, monkeypatch):
        calls = []
        monkeypatch.setattr(st, "_rest", lambda *a, **k: calls.append((a, k)))
        st.update_issue(3, make_task(title="X"), state="closed")
        payload = calls[0][0][2]
        assert payload["state"] == "closed"

    def test_update_issue_includes_assignees_and_milestone(self, monkeypatch):
        calls = []
        monkeypatch.setattr(st, "_rest", lambda *a, **k: calls.append((a, k)))
        st.update_issue(7, make_task(title="X", assignees=["alice"], milestone=8))
        payload = calls[0][0][2]
        assert payload["assignees"] == ["alice"]
        assert payload["milestone"] == 8

    def test_check_rate_limit_sleeps_at_threshold(self, monkeypatch):
        sleeps = []
        monkeypatch.setenv("RATELIMIT_BUFFER", "10")
        monkeypatch.setattr(st.time, "time", lambda: 100.0)
        monkeypatch.setattr(st.time, "sleep", lambda seconds: sleeps.append(seconds))
        st._check_rate_limit({
            "x-ratelimit-remaining": "10",
            "x-ratelimit-reset": "105",
        })
        assert sleeps == [6.0]

    def test_check_rate_limit_no_sleep_above_threshold(self, monkeypatch):
        sleeps = []
        monkeypatch.setenv("RATELIMIT_BUFFER", "10")
        monkeypatch.setattr(st.time, "time", lambda: 100.0)
        monkeypatch.setattr(st.time, "sleep", lambda seconds: sleeps.append(seconds))
        st._check_rate_limit({
            "x-ratelimit-remaining": "11",
            "x-ratelimit-reset": "105",
        })
        assert sleeps == []

    def test_rest_retries_once_on_403_retry_after(self, monkeypatch):
        monkeypatch.setattr(st, "TOKEN", "token")
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")
        sleeps = []
        monkeypatch.setattr(st.time, "sleep", lambda seconds: sleeps.append(seconds))

        class Resp:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return b'{"ok": true}'

        calls = {"n": 0}

        def fake_urlopen(_req):
            calls["n"] += 1
            if calls["n"] == 1:
                raise HTTPError(
                    url="https://api.github.com/x",
                    code=403,
                    msg="secondary rate limit",
                    hdrs={"retry-after": "2"},
                    fp=BytesIO(b'{"message":"rate limited"}'),
                )
            return Resp()

        monkeypatch.setattr(st, "urlopen", fake_urlopen)
        result = st._rest("GET", "/x")
        assert result == {"ok": True}
        assert sleeps == [2.0]


class TestGraphQLHelpers:
    def test_graphql_returns_data(self, monkeypatch):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return b'{"data":{"ok":1}}'

        monkeypatch.setattr(st.urllib.request, "urlopen", lambda _req: Resp())
        assert st._graphql("query {}", {}) == {"ok": 1}

    def test_graphql_raises_when_only_errors(self, monkeypatch):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return b'{"errors":[{"message":"boom"}]}'

        monkeypatch.setattr(st.urllib.request, "urlopen", lambda _req: Resp())
        with pytest.raises(RuntimeError):
            st._graphql("query {}", {})

    def test_get_project_meta_prefers_user_project(self, monkeypatch):
        monkeypatch.setattr(st, "_graphql", lambda *_: {
            "user": {"projectV2": {
                "id": "P1",
                "fields": {"nodes": [
                    {"id": "F1", "name": "Status", "options": [{"id": "O1", "name": "Planned"}]},
                    {"id": "F2", "name": "Priority", "options": []},
                    {"id": "F3"},
                ]}
            }},
            "organization": {"projectV2": None},
        })
        pid, fields, options = st.get_project_meta("owner", 1)
        assert pid == "P1"
        assert "Status" in fields
        assert options["Status:Planned"] == "O1"

    def test_get_project_meta_falls_back_to_org(self, monkeypatch):
        monkeypatch.setattr(st, "_graphql", lambda *_: {
            "user": {"projectV2": None},
            "organization": {"projectV2": {"id": "P2", "fields": {"nodes": []}}},
        })
        pid, fields, options = st.get_project_meta("org", 1)
        assert pid == "P2"
        assert fields == {}
        assert options == {}

    def test_get_project_meta_raises_when_missing(self, monkeypatch):
        monkeypatch.setattr(st, "_graphql", lambda *_: {
            "user": {"projectV2": None},
            "organization": {"projectV2": None},
        })
        with pytest.raises(RuntimeError):
            st.get_project_meta("owner", 123)

    def test_get_project_items_maps_issue_ids(self, monkeypatch):
        monkeypatch.setattr(st, "_graphql", lambda *_: {
            "node": {"items": {"nodes": [
                {"id": "ITEM1", "content": {"id": "ISS1"}},
                {"id": "ITEM2", "content": None},
                {"id": "ITEM3", "content": {"id": "ISS2"}},
            ]}}
        })
        assert st.get_project_items("P1") == {"ISS1": "ITEM1", "ISS2": "ITEM3"}

    def test_add_to_project_returns_item_id(self, monkeypatch):
        monkeypatch.setattr(st, "_graphql", lambda *_: {"addProjectV2ItemById": {"item": {"id": "ITEM9"}}})
        assert st.add_to_project("P1", "ISS1") == "ITEM9"

    def test_set_single_select_and_text_call_graphql(self, monkeypatch):
        calls = []
        monkeypatch.setattr(st, "_graphql", lambda q, v: calls.append((q, v)) or {})
        st._set_single_select("P1", "I1", "F1", "O1")
        st._set_text("P1", "I1", "F2", "hello")
        assert calls[0][1]["oid"] == "O1"
        assert calls[1][1]["v"] == "hello"


class TestProjectSync:
    def test_sync_to_project_adds_item_and_sets_all_fields(self, monkeypatch):
        calls = []
        monkeypatch.setattr(st, "add_to_project", lambda pid, iid: "ITEM_NEW")
        monkeypatch.setattr(st, "_set_single_select", lambda *a: calls.append(("select", a)))
        monkeypatch.setattr(st, "_set_text", lambda *a: calls.append(("text", a)))
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")

        issue = make_issue(node_id="ISS100")
        task = make_task(description="next step", status="planned", priority="high")
        items = {}
        fields = {
            "Status": {"id": "F_STATUS"},
            "Priority": {"id": "F_PRIORITY"},
            "Repo URL": {"id": "F_URL"},
            "Next action": {"id": "F_NEXT"},
        }
        options = {
            "Status:Planned": "O_PLAN",
            "Priority:High": "O_HIGH",
        }
        st.sync_to_project(issue, task, "P1", items, fields, options)
        assert items["ISS100"] == "ITEM_NEW"
        assert len(calls) == 4

    def test_sync_to_project_reuses_existing_item_and_skips_missing_fields(self, monkeypatch):
        calls = []
        monkeypatch.setattr(st, "_set_single_select", lambda *a: calls.append(("select", a)))
        monkeypatch.setattr(st, "_set_text", lambda *a: calls.append(("text", a)))
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")

        issue = make_issue(node_id="ISS100")
        task = make_task(status="done", priority="critical")
        st.sync_to_project(
            issue,
            task,
            "P1",
            {"ISS100": "ITEM1"},
            {"Status": {"id": "F_STATUS"}},
            {"Status:Done": "O_DONE"},
        )
        assert len(calls) == 1
        assert calls[0][0] == "select"


class TestSyncProjectMode:
    def test_sync_with_project_calls_project_sync(self, monkeypatch, tmp_path):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "project_owner": "owner",
            "project_number": 1,
            "tasks": [{"title": "Task P", "status": "planned"}],
        }), encoding="utf-8")

        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "create_issue", lambda _t, *a, **k: make_issue(node_id="ISS1"))
        monkeypatch.setattr(st, "get_project_meta", lambda *_: ("P1", {"Status": {"id": "F1"}}, {"Status:Planned": "O1"}))
        monkeypatch.setattr(st, "get_project_items", lambda _pid: {})
        calls = []
        monkeypatch.setattr(st, "sync_to_project", lambda *a, **k: calls.append(a))

        st.sync()
        assert len(calls) == 1

    def test_sync_project_failure_continues_without_project(self, monkeypatch, tmp_path):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "project_owner": "owner",
            "project_number": 1,
            "tasks": [{"title": "Task P", "status": "planned"}],
        }), encoding="utf-8")

        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "create_issue", lambda _t, *a, **k: make_issue(node_id="ISS1"))
        monkeypatch.setattr(st, "get_project_meta", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(st, "sync_to_project", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))

        st.sync()


class TestValidateConfig:
    def test_duplicate_titles(self):
        cfg = TrackerConfig(
            tasks=[
                make_task(title="A"),
                make_task(title="A"),
            ],
            project_owner="",
            project_number=0,
        )
        errors = st.validate_config(cfg)
        assert any("Duplicate task title" in e for e in errors)

    def test_invalid_status(self):
        cfg = TrackerConfig(
            tasks=[make_task(title="A", status="unknown_status")],
            project_owner="",
            project_number=0,
        )
        errors = st.validate_config(cfg)
        assert any("invalid status" in e for e in errors)

    def test_invalid_priority(self):
        cfg = TrackerConfig(
            tasks=[make_task(title="A", priority="urgent")],
            project_owner="",
            project_number=0,
        )
        errors = st.validate_config(cfg)
        assert any("invalid priority" in e for e in errors)

    def test_unknown_depends_on(self):
        cfg = TrackerConfig(
            tasks=[make_task(title="A", depends_on=["Missing"])],
            project_owner="",
            project_number=0,
        )
        errors = st.validate_config(cfg)
        assert any("depends on unknown task" in e for e in errors)

    def test_dependency_cycle(self):
        cfg = TrackerConfig(
            tasks=[
                make_task(title="A", depends_on=["B"]),
                make_task(title="B", depends_on=["A"]),
            ],
            project_owner="",
            project_number=0,
        )
        errors = st.validate_config(cfg)
        assert any("Dependency cycle detected" in e for e in errors)

    def test_non_string_labels(self):
        bad_labels: Any = ["ok", 123]
        cfg = TrackerConfig(
            tasks=[make_task(title="A", labels=bad_labels)],
            project_owner="",
            project_number=0,
        )
        errors = st.validate_config(cfg)
        assert any("non-string label" in e for e in errors)


class TestValidateOnlyMode:
    def test_validate_only_success_exits_without_api_calls(self, monkeypatch, tmp_path, capsys):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "tasks": [{"title": "Valid task", "status": "planned", "priority": "medium"}]
        }), encoding="utf-8")

        monkeypatch.setenv("VALIDATE_ONLY", "true")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: (_ for _ in ()).throw(AssertionError("no API calls")))
        monkeypatch.setattr(st, "list_issues", lambda: (_ for _ in ()).throw(AssertionError("no API calls")))

        st.sync()
        out = capsys.readouterr().out
        assert "Validation passed." in out

    def test_validate_only_failure_prints_numbered_errors(self, monkeypatch, tmp_path, capsys):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "tasks": [
                {"title": "Dup", "status": "planned", "priority": "medium"},
                {"title": "Dup", "status": "bad", "priority": "urgent", "labels": [1]},
            ]
        }), encoding="utf-8")

        monkeypatch.setenv("VALIDATE_ONLY", "true")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: (_ for _ in ()).throw(AssertionError("no API calls")))

        with pytest.raises(SystemExit):
            st.sync()
        err = capsys.readouterr().err
        assert "Validation failed:" in err
        assert "1." in err


class TestDryRunMode:
    def test_create_issue_dry_run_skips_rest(self, monkeypatch, capsys):
        monkeypatch.setenv("DRY_RUN", "true")
        monkeypatch.setattr(st, "_rest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no REST writes")))
        issue = st.create_issue(make_task(title="Dry create", labels=["x"]))
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out


class TestOrphanHandling:
    def test_on_orphan_ignore_does_nothing(self, monkeypatch, capsys):
        issue = make_issue(number=10, title="[tracker] orphan", body="none")
        monkeypatch.setattr(st, "_rest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no close expected")))
        st.handle_orphans([issue], [make_task(title="Known task")], mode="ignore")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_on_orphan_warn_prints_stderr(self, monkeypatch, capsys):
        issue = make_issue(number=10, title="[tracker] orphan", body="none")
        monkeypatch.setattr(st, "_rest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no close expected")))
        st.handle_orphans([issue], [make_task(title="Known task")], mode="warn")
        err = capsys.readouterr().err
        assert "WARNING: orphan tracker issue #10" in err

    def test_on_orphan_close_closes_issue(self, monkeypatch, capsys):
        issue = make_issue(number=10, title="[tracker] orphan", body="none", state="open")
        calls = []
        monkeypatch.setattr(st, "_rest", lambda *a, **k: calls.append((a, k)))
        monkeypatch.setenv("DRY_RUN", "false")
        st.handle_orphans([issue], [make_task(title="Known task")], mode="close")
        assert len(calls) == 1
        args = calls[0][0]
        assert args[0] == "PATCH"
        assert "/issues/10" in args[1]
        assert args[2] == {"state": "closed"}
        out = capsys.readouterr().out
        assert "Closed orphan #10" in out

    def test_update_issue_dry_run_skips_rest(self, monkeypatch, capsys):
        monkeypatch.setenv("DRY_RUN", "true")
        monkeypatch.setattr(st, "_rest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no REST writes")))
        st.update_issue(123, make_task(title="Dry update"), state="closed")
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out

    def test_sync_closed_task_dry_run_no_mutating_calls(self, monkeypatch, tmp_path, capsys):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "tasks": [{"title": "Done task", "status": "done"}]
        }), encoding="utf-8")

        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("DRY_RUN", "true")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "_rest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no REST writes")))

        st.sync()
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out

    def test_sync_to_project_dry_run_skips_project_writes(self, monkeypatch, capsys):
        monkeypatch.setenv("DRY_RUN", "true")
        monkeypatch.setattr(st, "add_to_project", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no project writes")))
        monkeypatch.setattr(st, "_set_single_select", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no project writes")))
        monkeypatch.setattr(st, "_set_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no project writes")))
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")

        st.sync_to_project(
            make_issue(node_id="ISS1"),
            make_task(title="Dry project", status="planned", priority="high"),
            "P1",
            {},
            {
                "Status": {"id": "F1"},
                "Priority": {"id": "F2"},
                "Repo URL": {"id": "F3"},
                "Next action": {"id": "F4"},
            },
            {
                "Status:Planned": "S1",
                "Priority:High": "P1",
            },
        )
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out


class TestStepSummary:
    def test_write_step_summary_format(self, tmp_path):
        summary = tmp_path / "step_summary.md"
        st.write_step_summary(summary, [
            {"number": "1", "title": "[tracker] Task A", "action": "created"},
            {"number": "1", "title": "[tracker] Task A", "action": "closed"},
            {"number": "2", "title": "[tracker] Task B", "action": "updated"},
        ])
        text = summary.read_text(encoding="utf-8")
        assert "| Action | Count |" in text
        assert "| created | 1 |" in text
        assert "| updated | 1 |" in text
        assert "| reopened | 0 |" in text
        assert "| closed | 1 |" in text
        assert "| Issue | Title | Result |" in text
        assert "| #1 | [tracker] Task A | created |" in text

    def test_sync_without_step_summary_env_no_crash(self, monkeypatch, tmp_path):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "tasks": [{"title": "Task A", "status": "planned"}]
        }), encoding="utf-8")

        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "create_issue", lambda _t, *a, **k: make_issue(number=21, title="[tracker] Task A"))

        st.sync()

    def test_sync_writes_step_summary_when_env_set(self, monkeypatch, tmp_path):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(json.dumps({
            "tasks": [{"title": "Task A", "status": "planned"}]
        }), encoding="utf-8")
        summary = tmp_path / "summary.md"

        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "create_issue", lambda _t, *a, **k: make_issue(number=42, title="[tracker] Task A"))

        st.sync()
        text = summary.read_text(encoding="utf-8")
        assert "| Action | Count |" in text
        assert "| created | 1 |" in text
        assert "| #42 | [tracker] Task A | created |" in text


class TestMultiFileSync:
    def test_tracker_glob_processes_multiple_files(self, monkeypatch, tmp_path):
        (tmp_path / "a.json").write_text(
            json.dumps({"tasks": [{"title": "Task A", "status": "planned"}]}),
            encoding="utf-8",
        )
        (tmp_path / "b.json").write_text(
            json.dumps({"tasks": [{"title": "Task B", "status": "planned"}]}),
            encoding="utf-8",
        )

        created = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TRACKER_GLOB", "*.json")
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("ON_ORPHAN", "ignore")
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "create_issue", lambda t, *a, **k: created.append(t.title) or make_issue(title=t.issue_title))

        st.sync()
        assert set(created) == {"Task A", "Task B"}

    def test_tracker_glob_continues_when_one_file_is_bad(self, monkeypatch, tmp_path):
        (tmp_path / "good.json").write_text(
            json.dumps({"tasks": [{"title": "Good task", "status": "planned"}]}),
            encoding="utf-8",
        )
        (tmp_path / "bad.json").write_text("{ invalid json", encoding="utf-8")

        created = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TRACKER_GLOB", "*.json")
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("ON_ORPHAN", "ignore")
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "create_issue", lambda t, *a, **k: created.append(t.title) or make_issue(title=t.issue_title))

        with pytest.raises(SystemExit):
            st.sync()
        assert "Good task" in created

    def test_single_file_mode_still_works(self, monkeypatch, tmp_path):
        tracker = tmp_path / "single.json"
        tracker.write_text(
            json.dumps({"tasks": [{"title": "Single task", "status": "planned"}]}),
            encoding="utf-8",
        )

        created = []
        monkeypatch.delenv("TRACKER_GLOB", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("ON_ORPHAN", "ignore")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)
        monkeypatch.setattr(st, "ensure_label", lambda: None)
        monkeypatch.setattr(st, "list_issues", lambda: [])
        monkeypatch.setattr(st, "create_issue", lambda t, *a, **k: created.append(t.title) or make_issue(title=t.issue_title))

        st.sync()
        assert created == ["Single task"]
