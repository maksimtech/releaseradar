"""Shared fixtures: an isolated database, and GitHub replayed from recordings.

No mocks. The GitHub API is replayed at the HTTP boundary from responses
recorded from api.github.com (tests/fixtures/gh_*.json, each with a `_recorded`
block saying when and from which URL). A test that needs a payload nobody can
record on demand — a comment-only update, a rate-limit refusal, a draft release
— derives it from a recorded one and says so in its docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

FIXTURES = Path(__file__).parent / "fixtures"
API = "https://api.github.com"


def recorded(name: str) -> dict:
    doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert "_recorded" in doc, f"{name} carries no provenance"
    return doc


def replay(name: str) -> httpx.Response:
    """The recorded response, status, rate-limit headers and body as GitHub sent them."""
    doc = recorded(name)
    headers = {k: v for k, v in doc["headers"].items() if k != "content-type"}
    return httpx.Response(doc["status"], json=doc["body"], headers=headers)


def empty_page() -> httpx.Response:
    return httpx.Response(200, json=[])


def route_repo(mock: respx.MockRouter, owner: str, name: str,
               issues: str | httpx.Response, pulls: str | httpx.Response, releases: str | httpx.Response) -> dict:
    """Route the three list endpoints of one repository to recordings (or responses)."""
    base = f"{API}/repos/{owner}/{name}"

    def as_response(what: str | httpx.Response) -> httpx.Response:
        return replay(what) if isinstance(what, str) else what

    return {
        "issues": mock.get(f"{base}/issues").mock(return_value=as_response(issues)),
        "pulls": mock.get(f"{base}/pulls").mock(return_value=as_response(pulls)),
        "releases": mock.get(f"{base}/releases").mock(return_value=as_response(releases)),
    }


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh database for this test, and no token from the developer's shell."""
    path = tmp_path / "state.db"
    monkeypatch.setenv("RELEASERADAR_DB", str(path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return path


@pytest.fixture
def github():
    """A replay router that rejects any request nothing recorded."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock
