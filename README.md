# repo-task-tracker

[![CI](https://github.com/DiogoRibeiro7/repo-task-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/DiogoRibeiro7/repo-task-tracker/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/DiogoRibeiro7/repo-task-tracker/graph/badge.svg?branch=main)](https://codecov.io/gh/DiogoRibeiro7/repo-task-tracker)
[![Release](https://img.shields.io/github/v/release/DiogoRibeiro7/repo-task-tracker)](https://github.com/DiogoRibeiro7/repo-task-tracker/releases)

A GitHub Action that turns a `tracker.json` file in your repository into
GitHub Issues, then syncs those issues to a central GitHub Projects (v2) board.

Add it to any package repo with four lines of workflow YAML. All repos feed
into the same board so you can track every package, library, and research
codebase from one place.

---

## How it works

```
survipw/tracker.json  ──┐
gen_surv/tracker.json ──┤──► Issues in each repo ──► Central Project board
curvknn/tracker.json  ──┘
```

1. You maintain a `tracker.json` in each repo listing that repo's tasks.
2. On every push to `tracker.json` (and on a weekly schedule), the action
   creates or updates one GitHub Issue per task.
3. Issues are labelled `tracker` and their state (open/closed) follows the
   task status automatically.
4. If you configure a Project board, the action adds each issue to the board
   and keeps the Status, Priority, Repo URL, and Next action fields up to date
   via the GitHub GraphQL API.

---

## Quickstart

### 1. Add `tracker.json` to your repo

```json
{
  "project_owner": "DiogoRibeiro7",
  "project_number": 1,
  "tasks": [
    {
      "title": "Repo scaffold",
      "description": "Set up pyproject.toml, AGENTS.md, and CI skeleton.",
      "status": "done",
      "priority": "high"
    },
    {
      "title": "Core implementation",
      "description": "Implement main package modules and public API.",
      "status": "in_progress",
      "priority": "high"
    },
    {
      "title": "Tests and CI",
      "description": "Achieve >90 % coverage, green CI on Python 3.10–3.12.",
      "status": "planned",
      "priority": "high"
    },
    {
      "title": "Documentation",
      "description": "Write full README, API docs, and usage examples.",
      "status": "planned",
      "priority": "medium"
    },
    {
      "title": "PyPI / Zenodo release",
      "description": "Publish to PyPI, mint Zenodo DOI, tag v1.0.0.",
      "status": "planned",
      "priority": "medium"
    },
    {
      "title": "JOSS paper",
      "description": "Write paper.md and submit to JOSS.",
      "status": "planned",
      "priority": "low"
    }
  ]
}
```

### 2. Add the workflow

Create `.github/workflows/tracker.yml`:

```yaml
name: Task tracker

on:
  push:
    paths:
      - 'tracker.json'
  schedule:
    - cron: '0 9 * * 1'   # every Monday 09:00 UTC
  workflow_dispatch:

permissions:
  contents: read
  issues: write

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: DiogoRibeiro7/repo-task-tracker@v1
        with:
          github-token: ${{ secrets.PROJECT_TOKEN }}
          # Remove the two lines below if you don't use a Project board
          project-owner: DiogoRibeiro7
          project-number: 1
```

### 3. Add the secret

Go to **Settings → Secrets → Actions** and add `PROJECT_TOKEN`: a
[fine-grained PAT](https://github.com/settings/tokens?type=beta) with:

- **Repository permissions:** Issues → Read and write
- **Account permissions:** Projects → Read and write

The default `GITHUB_TOKEN` cannot write to user-owned Projects, which is why
a PAT is needed for project board sync. If you skip the board, the default
token is sufficient.

---

## `tracker.json` reference

| Field | Type | Required | Description |
|---|---|---|---|
| `tasks` | array | ✓ | List of task objects (see below) |
| `project_owner` | string | | GitHub login or org that owns the Project board |
| `project_number` | integer | | Number of the Project board |

### Task object

| Field | Type | Default | Description |
|---|---|---|---|
| `title` | string | — | Short task name. Used as the issue title. |
| `description` | string | `""` | Longer description shown in the issue body. |
| `status` | string | `planned` | See status values below. |
| `priority` | string | `medium` | `low`, `medium`, `high`, `critical` |
| `labels` | array | `[]` | Extra labels to add to the issue alongside `tracker`. |
| `assignees` | array | `[]` | GitHub login names to assign on issue create/update. |
| `milestone` | integer | `null` | Milestone number to set on issue create/update. |

### Status values

| `tracker.json` status | Issue state | Project board Status |
|---|---|---|
| `planned` | open | Planned |
| `in_progress` | open | Writing |
| `review` | open | Revising |
| `blocked` | open | Blocked |
| `done` | **closed** | Done |
| `archived` | **closed** | Archived |
| `cancelled` | **closed** | Archived |

---

## Action inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `github-token` | ✓ | — | Token for issues:write and projects:write |
| `project-owner` | | `""` | Owner of the Project board. Empty = skip board sync. |
| `project-number` | | `0` | Project board number. `0` = skip board sync. |
| `tracker-path` | | `tracker.json` | Path to the config file relative to repo root. |
| `tracker-glob` | | `""` | Glob pattern for multiple tracker files. Overrides `tracker-path`. |
| `validate` | | `false` | Validate config and exit without any API writes. |
| `dry-run` | | `false` | Show what would change without mutating issues or project fields. |
| `on-orphan` | | `warn` | Behavior for orphan `tracker` issues: `warn`, `close`, `ignore`. |

### Advanced modes

#### Validate-only

```yaml
- uses: DiogoRibeiro7/repo-task-tracker@v1
  with:
    github-token: ${{ secrets.PROJECT_TOKEN }}
    validate: 'true'
```

#### Dry-run preview

```yaml
- uses: DiogoRibeiro7/repo-task-tracker@v1
  with:
    github-token: ${{ secrets.PROJECT_TOKEN }}
    dry-run: 'true'
```

#### Multi-file tracker sync

```yaml
- uses: DiogoRibeiro7/repo-task-tracker@v1
  with:
    github-token: ${{ secrets.PROJECT_TOKEN }}
    tracker-glob: 'trackers/*.json'
```

#### Orphan policy

```yaml
- uses: DiogoRibeiro7/repo-task-tracker@v1
  with:
    github-token: ${{ secrets.PROJECT_TOKEN }}
    on-orphan: 'close'
```

Environment tuning:
- `RATELIMIT_BUFFER` (default `10`): when remaining REST quota drops to this value or lower, the action waits for reset before continuing.

---

## Project board setup

Create a GitHub Project (v2) named **Research Tracker** and add these fields:

| Field name | Type | Options |
|---|---|---|
| Status | Single select | Backlog, Planned, Reading, Writing, Experiments, Revising, Submitted, Camera-ready, Done, Blocked, Archived |
| Priority | Single select | Low, Medium, High, Critical |
| Repo URL | Text | |
| Next action | Text | |

The action uses the field names exactly as written above. If a field is
missing, the action skips that field with a warning rather than failing.

---

## Development

```bash
git clone https://github.com/DiogoRibeiro7/repo-task-tracker
cd repo-task-tracker
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
Release process and signed tag requirements are documented in [RELEASE_POLICY.md](RELEASE_POLICY.md).
