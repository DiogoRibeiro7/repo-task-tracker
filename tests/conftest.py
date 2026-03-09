"""Shared pytest fixtures for repo-task-tracker tests."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Load the module under test without executing sync()
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "sync_tasks.py"

spec = importlib.util.spec_from_file_location("sync_tasks", MODULE_PATH)
assert spec and spec.loader
sync_tasks = importlib.util.module_from_spec(spec)
sys.modules["sync_tasks"] = sync_tasks
spec.loader.exec_module(sync_tasks)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Re-export the module so tests can import it without repeating this dance
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mod():
    """Return the sync_tasks module."""
    return sync_tasks


# ---------------------------------------------------------------------------
# Common task factories
# ---------------------------------------------------------------------------

def make_task(
    title: str = "Test task",
    description: str = "A test description.",
    status: str = "planned",
    priority: str = "medium",
    labels: Optional[List[str]] = None,
    depends_on: Optional[List[str]] = None,
    assignees: Optional[List[str]] = None,
    milestone: Optional[int] = None,
) -> Any:
    return sync_tasks.Task(
        title=title,
        description=description,
        status=status,
        priority=priority,
        labels=labels or [],
        depends_on=depends_on or [],
        assignees=assignees or [],
        milestone=milestone,
    )


def make_issue(
    number: int = 1,
    title: str = "",
    state: str = "open",
    body: str = "",
    node_id: str = "ISSUE_NODE_1",
) -> Dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": state,
        "body": body,
        "id": node_id,
    }


@pytest.fixture
def task_factory():
    return make_task


@pytest.fixture
def issue_factory():
    return make_issue
