"""Tests for the SQLite DAO (db.py)."""

import pytest
from pathlib import Path

from releaseradar import db as _db


@pytest.fixture
def tmp_db(tmp_path: Path) -> _db._DB:
    return _db.open_db(tmp_path / "test.db")


# ── repos ─────────────────────────────────────────────────────────────────────

class TestRepos:
    def test_add_repo_returns_id(self, tmp_db):
        repo_id = tmp_db.add_repo("torvalds", "linux")
        assert isinstance(repo_id, int)
        assert repo_id > 0

    def test_add_repo_idempotent(self, tmp_db):
        id1 = tmp_db.add_repo("torvalds", "linux")
        id2 = tmp_db.add_repo("torvalds", "linux")
        assert id1 == id2

    def test_list_repos_empty(self, tmp_db):
        assert tmp_db.list_repos() == []

    def test_list_repos(self, tmp_db):
        tmp_db.add_repo("torvalds", "linux")
        tmp_db.add_repo("psf", "requests")
        rows = tmp_db.list_repos()
        assert len(rows) == 2
        names = {(r["owner"], r["name"]) for r in rows}
        assert ("torvalds", "linux") in names
        assert ("psf", "requests") in names

    def test_remove_repo_existing(self, tmp_db):
        tmp_db.add_repo("torvalds", "linux")
        assert tmp_db.remove_repo("torvalds", "linux") is True

    def test_remove_repo_nonexistent(self, tmp_db):
        assert tmp_db.remove_repo("nobody", "nothing") is False

    def test_get_repo_id(self, tmp_db):
        repo_id = tmp_db.add_repo("torvalds", "linux")
        assert tmp_db.get_repo_id("torvalds", "linux") == repo_id

    def test_get_repo_id_missing(self, tmp_db):
        assert tmp_db.get_repo_id("nobody", "nothing") is None


# ── issues ────────────────────────────────────────────────────────────────────

_ISSUE = {
    "number": 1,
    "title": "First issue",
    "state": "open",
    "html_url": "https://github.com/t/l/issues/1",
    "opened_at": "2026-01-01T00:00:00Z",
    "closed_at": None,
    "updated_at": "2026-01-01T00:00:00Z",
}


class TestIssues:
    def test_upsert_new_issue_returns_true(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        assert tmp_db.upsert_issue(repo_id, _ISSUE) is True

    def test_upsert_same_issue_returns_false(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_issue(repo_id, _ISSUE)
        assert tmp_db.upsert_issue(repo_id, _ISSUE) is False

    def test_upsert_updated_issue_returns_true(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_issue(repo_id, _ISSUE)
        updated = {**_ISSUE, "state": "closed", "updated_at": "2026-01-02T00:00:00Z"}
        assert tmp_db.upsert_issue(repo_id, updated) is True


# ── pull requests ─────────────────────────────────────────────────────────────

_PR = {
    "number": 1,
    "title": "First PR",
    "state": "open",
    "html_url": "https://github.com/t/l/pull/1",
    "opened_at": "2026-01-01T00:00:00Z",
    "merged_at": None,
    "updated_at": "2026-01-01T00:00:00Z",
    "linked_issues": None,
}


class TestPullRequests:
    def test_upsert_new_pr_returns_true(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        assert tmp_db.upsert_pr(repo_id, _PR) is True

    def test_upsert_same_pr_returns_false(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_pr(repo_id, _PR)
        assert tmp_db.upsert_pr(repo_id, _PR) is False

    def test_upsert_merged_pr(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_pr(repo_id, _PR)
        merged = {**_PR, "state": "merged", "merged_at": "2026-01-02T00:00:00Z",
                  "updated_at": "2026-01-02T00:00:00Z"}
        assert tmp_db.upsert_pr(repo_id, merged) is True


# ── releases ──────────────────────────────────────────────────────────────────

_RELEASE = {
    "tag_name": "v1.0.0",
    "name": "First release",
    "html_url": "https://github.com/t/l/releases/tag/v1.0.0",
    "published_at": "2026-01-01T00:00:00Z",
    "prerelease": 0,
    "body": None,
}


class TestReleases:
    def test_upsert_new_release_returns_true(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        assert tmp_db.upsert_release(repo_id, _RELEASE) is True

    def test_upsert_same_release_returns_false(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_release(repo_id, _RELEASE)
        assert tmp_db.upsert_release(repo_id, _RELEASE) is False


# ── events ────────────────────────────────────────────────────────────────────

class TestEvents:
    def test_add_event_and_pending(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.add_event(repo_id, "issue_opened", ref_number=1)
        pending = tmp_db.pending_events()
        assert len(pending) == 1
        assert pending[0]["kind"] == "issue_opened"

    def test_mark_notified(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.add_event(repo_id, "release_published", ref_tag="v1.0.0")
        pending = tmp_db.pending_events()
        assert len(pending) == 1
        tmp_db.mark_notified([pending[0]["id"]])
        assert tmp_db.pending_events() == []


# ── repo_summary ──────────────────────────────────────────────────────────────

class TestRepoSummary:
    def test_summary_missing_repo(self, tmp_db):
        assert tmp_db.repo_summary("nobody", "nothing") == {}

    def test_summary_empty_repo(self, tmp_db):
        tmp_db.add_repo("t", "l")
        s = tmp_db.repo_summary("t", "l")
        assert s["open_issues"] == 0
        assert s["open_prs"] == 0
        assert s["latest_release"] is None

    def test_summary_with_data(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_issue(repo_id, _ISSUE)
        tmp_db.upsert_pr(repo_id, _PR)
        tmp_db.upsert_release(repo_id, _RELEASE)
        s = tmp_db.repo_summary("t", "l")
        assert s["open_issues"] == 1
        assert s["open_prs"] == 1
        assert s["latest_release"]["tag_name"] == "v1.0.0"
