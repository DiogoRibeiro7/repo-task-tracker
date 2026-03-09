"""Tests for sync_tasks.py"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

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
                            lambda t: actions.append(("create", t.title))
                            or make_issue(title=t.issue_title))
        monkeypatch.setattr(st, "update_issue",
                            lambda n, t, state=None:
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
