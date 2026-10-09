"""Tests for sync.py — mocked GitHub API calls."""

from pathlib import Path
from unittest.mock import patch

import pytest

from releaseradar import db as _db
from releaseradar.sync import sync_repo, sync_all


_ISSUE_RAW = {
    "number": 1,
    "title": "Bug report",
    "state": "open",
    "html_url": "https://github.com/t/l/issues/1",
    "created_at": "2026-01-01T00:00:00Z",
    "closed_at": None,
    "updated_at": "2026-01-01T00:00:00Z",
}

_PR_RAW = {
    "number": 1,
    "title": "Fix the bug",
    "state": "open",
    "html_url": "https://github.com/t/l/pull/1",
    "created_at": "2026-01-02T00:00:00Z",
    "merged_at": None,
    "updated_at": "2026-01-02T00:00:00Z",
    "body": "Fixes #1",
}

_RELEASE_RAW = {
    "tag_name": "v1.0.0",
    "name": "First release",
    "html_url": "https://github.com/t/l/releases/tag/v1.0.0",
    "published_at": "2026-01-03T00:00:00Z",
    "prerelease": False,
    "body": None,
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


class TestSyncRepo:
    def test_sync_emits_events(self, db_path):
        with (
            patch("releaseradar.sync.gh.fetch_issues", return_value=[_ISSUE_RAW]),
            patch("releaseradar.sync.gh.fetch_pull_requests", return_value=[_PR_RAW]),
            patch("releaseradar.sync.gh.fetch_releases", return_value=[_RELEASE_RAW]),
        ):
            events = sync_repo("t", "l", token=None, db_path=db_path)

        kinds = {e["kind"] for e in events}
        assert "issue_opened" in kinds
        assert "pr_opened" in kinds
        assert "release_published" in kinds

    def test_sync_idempotent(self, db_path):
        """Second sync of the same data should emit no new events."""
        with (
            patch("releaseradar.sync.gh.fetch_issues", return_value=[_ISSUE_RAW]),
            patch("releaseradar.sync.gh.fetch_pull_requests", return_value=[_PR_RAW]),
            patch("releaseradar.sync.gh.fetch_releases", return_value=[_RELEASE_RAW]),
        ):
            sync_repo("t", "l", token=None, db_path=db_path)
            events2 = sync_repo("t", "l", token=None, db_path=db_path)

        assert events2 == []

    def test_sync_state_change(self, db_path):
        """Closing an issue emits issue_closed event."""
        with (
            patch("releaseradar.sync.gh.fetch_issues", return_value=[_ISSUE_RAW]),
            patch("releaseradar.sync.gh.fetch_pull_requests", return_value=[]),
            patch("releaseradar.sync.gh.fetch_releases", return_value=[]),
        ):
            sync_repo("t", "l", token=None, db_path=db_path)

        closed_issue = {
            **_ISSUE_RAW,
            "state": "closed",
            "closed_at": "2026-01-05T00:00:00Z",
            "updated_at": "2026-01-05T00:00:00Z",
        }
        with (
            patch("releaseradar.sync.gh.fetch_issues", return_value=[closed_issue]),
            patch("releaseradar.sync.gh.fetch_pull_requests", return_value=[]),
            patch("releaseradar.sync.gh.fetch_releases", return_value=[]),
        ):
            events = sync_repo("t", "l", token=None, db_path=db_path)

        assert any(e["kind"] == "issue_closed" for e in events)


class TestSyncAll:
    def test_sync_all_multiple_repos(self, db_path):
        database = _db.open_db(db_path)
        database.add_repo("t", "l")
        database.add_repo("psf", "requests")

        with (
            patch("releaseradar.sync.gh.fetch_issues", return_value=[]),
            patch("releaseradar.sync.gh.fetch_pull_requests", return_value=[]),
            patch("releaseradar.sync.gh.fetch_releases", return_value=[]),
        ):
            results = sync_all(token=None, db_path=db_path)

        assert "t/l" in results
        assert "psf/requests" in results
