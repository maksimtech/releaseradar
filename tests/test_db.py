"""The SQLite DAO (db.py), on a real database file."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

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
        assert tmp_db.add_repo("torvalds", "linux") == tmp_db.add_repo("torvalds", "linux")

    def test_list_repos_empty(self, tmp_db):
        assert tmp_db.list_repos() == []

    def test_list_repos(self, tmp_db):
        tmp_db.add_repo("torvalds", "linux")
        tmp_db.add_repo("psf", "requests")
        rows = tmp_db.list_repos()
        assert {(r["owner"], r["name"]) for r in rows} == {("torvalds", "linux"), ("psf", "requests")}
        assert all(r["last_synced_at"] is None for r in rows)

    def test_remove_repo_existing(self, tmp_db):
        tmp_db.add_repo("torvalds", "linux")
        assert tmp_db.remove_repo("torvalds", "linux") is True

    def test_remove_repo_nonexistent(self, tmp_db):
        assert tmp_db.remove_repo("nobody", "nothing") is False

    def test_remove_repo_cascades(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_issue(repo_id, _ISSUE)
        tmp_db.add_event(repo_id, "issue_opened", ref_number=1)
        tmp_db.remove_repo("t", "l")
        with sqlite3.connect(tmp_db.path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

    def test_get_repo_id(self, tmp_db):
        repo_id = tmp_db.add_repo("torvalds", "linux")
        assert tmp_db.get_repo_id("torvalds", "linux") == repo_id

    def test_get_repo_id_missing(self, tmp_db):
        assert tmp_db.get_repo_id("nobody", "nothing") is None

    def test_last_synced_is_recorded(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.set_last_synced(repo_id, "2026-10-10T12:00:00Z")
        assert tmp_db.get_repo("t", "l")["last_synced_at"] == "2026-10-10T12:00:00Z"

    def test_a_database_from_before_last_synced_at_is_upgraded(self, tmp_path):
        """A state.db written by the scaffold has no `last_synced_at`; opening it adds the column."""
        path = tmp_path / "old.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(_db.DDL.replace("    last_synced_at  TEXT,\n", ""))
            conn.execute("INSERT INTO repos(owner, name) VALUES ('t', 'l')")
        upgraded = _db.open_db(path)
        assert upgraded.get_repo("t", "l")["last_synced_at"] is None


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
    def test_a_new_issue_has_no_previous_state(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        assert tmp_db.upsert_issue(repo_id, _ISSUE) is None

    def test_an_unchanged_issue_reports_its_state(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_issue(repo_id, _ISSUE)
        assert tmp_db.upsert_issue(repo_id, _ISSUE) == "open"

    def test_a_closed_issue_reports_the_state_it_had(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_issue(repo_id, _ISSUE)
        updated = {**_ISSUE, "state": "closed", "updated_at": "2026-01-02T00:00:00Z"}
        assert tmp_db.upsert_issue(repo_id, updated) == "open"
        assert tmp_db.upsert_issue(repo_id, updated) == "closed"


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
    def test_a_new_pr_has_no_previous_state(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        assert tmp_db.upsert_pr(repo_id, _PR) is None

    def test_an_unchanged_pr_reports_its_state(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_pr(repo_id, _PR)
        assert tmp_db.upsert_pr(repo_id, _PR) == "open"

    def test_a_merged_pr_reports_open_once(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_pr(repo_id, _PR)
        merged = {**_PR, "state": "merged", "merged_at": "2026-01-02T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}
        assert tmp_db.upsert_pr(repo_id, merged) == "open"
        assert tmp_db.upsert_pr(repo_id, merged) == "merged"


# ── releases ──────────────────────────────────────────────────────────────────

_RELEASE = {
    "tag_name": "v1.0.0",
    "name": "First release",
    "html_url": "https://github.com/t/l/releases/tag/v1.0.0",
    "published_at": "2026-01-01T00:00:00Z",
    "prerelease": 0,
    "draft": False,
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

    def test_the_draft_flag_is_not_a_column(self, tmp_db):
        """`draft` is for the sync to decide on; the table stores published releases."""
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_release(repo_id, _RELEASE)
        with sqlite3.connect(tmp_db.path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(releases)")}
        assert "draft" not in columns


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
        tmp_db.mark_notified([pending[0]["id"]])
        assert tmp_db.pending_events() == []

    def test_mark_notified_with_nothing_is_a_no_op(self, tmp_db):
        tmp_db.mark_notified([])

    def test_recent_events_are_newest_first_by_insertion(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        for n in (1, 2, 3):
            tmp_db.add_event(repo_id, "issue_opened", ref_number=n)
        assert [e["ref_number"] for e in tmp_db.recent_events()] == [3, 2, 1]
        assert [e["ref_number"] for e in tmp_db.recent_events(limit=2)] == [3, 2]

    def test_pending_events_are_oldest_first(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        for n in (1, 2, 3):
            tmp_db.add_event(repo_id, "issue_opened", ref_number=n)
        assert [e["ref_number"] for e in tmp_db.pending_events()] == [1, 2, 3]


# ── one transaction for a whole sync ─────────────────────────────────────────

class TestTransaction:
    def test_rows_inside_a_transaction_are_committed_together(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        with tmp_db.transaction():
            for n in range(50):
                tmp_db.upsert_issue(repo_id, {**_ISSUE, "number": n})
            with sqlite3.connect(tmp_db.path) as other:
                assert other.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0, "visible before commit"
        with sqlite3.connect(tmp_db.path) as other:
            assert other.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 50

    def test_an_error_inside_a_transaction_rolls_everything_back(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        with pytest.raises(RuntimeError), tmp_db.transaction():
            tmp_db.upsert_issue(repo_id, _ISSUE)
            raise RuntimeError("GitHub went away mid-sync")
        assert tmp_db.repo_summary("t", "l")["open_issues"] == 0

    def test_a_thousand_rows_take_well_under_a_second_in_one_transaction(self, tmp_db):
        """Measured on 2026-10-10: one connection and one commit per row cost ~24 ms
        each on Windows, so 7 299 rows of psf/requests took three minutes with no
        network involved. The budget here is generous for CI and far below that."""
        repo_id = tmp_db.add_repo("t", "l")
        rows = [{**_ISSUE, "number": n} for n in range(1000)]
        started = time.perf_counter()
        with tmp_db.transaction():
            for row in rows:
                tmp_db.upsert_issue(repo_id, row)
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0, f"{elapsed:.1f}s for 1000 upserts"
        assert tmp_db.repo_summary("t", "l")["open_issues"] == 1000


# ── repo_summary ──────────────────────────────────────────────────────────────

class TestRepoSummary:
    def test_summary_missing_repo(self, tmp_db):
        assert tmp_db.repo_summary("nobody", "nothing") == {}

    def test_summary_empty_repo(self, tmp_db):
        tmp_db.add_repo("t", "l")
        s = tmp_db.repo_summary("t", "l")
        assert (s["open_issues"], s["open_prs"], s["latest_release"], s["last_synced_at"]) == (0, 0, None, None)

    def test_summary_with_data(self, tmp_db):
        repo_id = tmp_db.add_repo("t", "l")
        tmp_db.upsert_issue(repo_id, _ISSUE)
        tmp_db.upsert_pr(repo_id, _PR)
        tmp_db.upsert_release(repo_id, _RELEASE)
        s = tmp_db.repo_summary("t", "l")
        assert s["open_issues"] == 1
        assert s["open_prs"] == 1
        assert s["latest_release"]["tag_name"] == "v1.0.0"
