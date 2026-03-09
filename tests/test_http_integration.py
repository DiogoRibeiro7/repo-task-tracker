"""Integration-style tests around REST request flow using fixture payloads."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from conftest import sync_tasks as st


FIXTURES = Path(__file__).parent / "fixtures"


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self) -> bytes:
        return self._payload


class TestRestIntegration:
    def test_sync_flow_uses_rest_with_fixture_payloads(self, monkeypatch, tmp_path):
        tracker = tmp_path / "tracker.json"
        tracker.write_text(
            json.dumps({"tasks": [{"title": "Task A", "status": "planned"}]}),
            encoding="utf-8",
        )

        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("ON_ORPHAN", "ignore")
        monkeypatch.setattr(st, "TOKEN", "token")
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")
        monkeypatch.setattr(st, "TRACKER_PATH", tracker)

        calls = []

        label_payload = (FIXTURES / "label_created.json").read_bytes()
        empty_issues = (FIXTURES / "issues_empty.json").read_bytes()
        created_issue = (FIXTURES / "issue_created.json").read_bytes()

        def fake_urlopen(req):
            calls.append((req.get_method(), req.full_url))
            if req.get_method() == "POST" and req.full_url.endswith("/labels"):
                return _Resp(label_payload)
            if req.get_method() == "GET" and "/issues?state=all&labels=tracker" in req.full_url:
                return _Resp(empty_issues)
            if req.get_method() == "POST" and req.full_url.endswith("/issues"):
                return _Resp(created_issue)
            raise AssertionError(f"Unexpected request: {req.get_method()} {req.full_url}")

        monkeypatch.setattr(st, "urlopen", fake_urlopen)
        st.sync_one(tracker)

        assert ("POST", f"{st.API_URL}/repos/owner/repo/labels") in calls
        assert any(method == "GET" and "/issues?state=all&labels=tracker" in url for method, url in calls)
        assert ("POST", f"{st.API_URL}/repos/owner/repo/issues") in calls

    def test_expected_error_body_non_json_is_preserved(self, monkeypatch):
        monkeypatch.setattr(st, "TOKEN", "token")
        monkeypatch.setattr(st, "REPOSITORY", "owner/repo")

        def fake_urlopen(_req):
            raise HTTPError(
                url="https://api.github.com/repos/owner/repo/labels",
                code=422,
                msg="unprocessable",
                hdrs=None,
                fp=BytesIO(b"plain text error"),
            )

        monkeypatch.setattr(st, "urlopen", fake_urlopen)
        result = st._rest("POST", "/repos/owner/repo/labels", expected_errors={422})
        assert result["__error__"]["status"] == 422
        assert "message" in result["__error__"]["body"]
