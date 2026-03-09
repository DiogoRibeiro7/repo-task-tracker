# Contributing

Thanks for helping improve **repo-task-tracker**.

## Development setup

```bash
git clone https://github.com/DiogoRibeiro7/repo-task-tracker
cd repo-task-tracker
pip install -e ".[dev]"
```

## Running the tests

```bash
pytest                          # all tests
pytest --cov=. --cov-report=term-missing   # with coverage
```

The test suite must stay green on Python 3.10, 3.11, and 3.12. Coverage must
not fall below 90 %.

## Project structure

```
repo-task-tracker/
  action.yml           GitHub Action definition (inputs, composite run steps)
  sync_tasks.py        All sync logic — the only runtime file bundled by the action
  pyproject.toml       Dev dependencies and pytest config
  tests/
    conftest.py        Module loader and shared fixtures
    test_sync_tasks.py Full test suite
  .github/
    workflows/
      ci.yml           Runs tests on every push / PR
      release.yml      Tags a release and updates the floating major tag (v1)
```

## Adding a new status

1. Add the status string to `OPEN_STATUSES` or `CLOSED_STATUSES` in `sync_tasks.py`.
2. Add a mapping entry in `STATUS_MAP`.
3. Update the status table in `README.md`.
4. Add a parametrize case in `TestTaskProjectStatus`.

## Submitting a pull request

- Keep `sync_tasks.py` dependency-free (stdlib only). The action runs on
  a plain `python3` without `pip install`.
- Write or update tests for any logic change.
- Update `README.md` if you change any input name, default, or status value.
- Squash to one commit per logical change before requesting review.

## Releasing a new version

1. Bump the version in `pyproject.toml`.
2. Push a tag: `git tag v1.2.3 && git push origin v1.2.3`.
3. The release workflow runs the tests, creates a GitHub Release, and moves
   the floating `v1` tag to the new commit automatically.
