"""What a sync records and what it reports — GitHub replayed from recordings."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from releaseradar import db as _db
from releaseradar import github as gh
from releaseradar.sync import NotTracked, sync_all, sync_repo
from tests.conftest import API, empty_page, recorded, replay, route_repo


def track(db_path: Path, owner: str, name: str) -> _db._DB:
    database = _db.open_db(db_path)
    database.add_repo(owner, name)
    return database


def after_the_last_sync() -> str:
    """A GitHub timestamp later than any `last_synced_at` this test run can write.

    A derived transition has to carry an `updated_at` after the previous sync,
    or the pull-request walk cuts it as already seen — which is correct, and is
    how a fixed "12:00:00Z" turned a test red the moment the clock passed noon.
    """
    return (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def an_hour_later(items: list[dict]) -> list[dict]:
    """The same items with `updated_at` moved forward one hour and nothing else
    touched — what a comment or a label does to an issue."""
    later = []
    for item in items:
        stamp = item["updated_at"]
        hour = (int(stamp[11:13]) + 1) % 24
        later.append({**item, "updated_at": f"{stamp[:11]}{hour:02d}{stamp[13:]}"})
    return later


class TestFirstSync:
    def test_it_seeds_the_state_and_reports_nothing(self, db_path, github):
        """17 releases of cookieradar, published before anyone tracked the repo: the
        first sync stores them and emits no event, because it saw no transition."""
        route_repo(github, "maksimtech", "cookieradar",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_cookieradar_releases.json")
        database = track(db_path, "maksimtech", "cookieradar")

        events = sync_repo("maksimtech", "cookieradar", db_path=db_path)

        assert events == []
        summary = database.repo_summary("maksimtech", "cookieradar")
        assert summary["latest_release"]["tag_name"] == "v2026.44"
        assert summary["last_synced_at"] is not None
        assert database.recent_events() == []

    def test_it_asks_for_everything(self, db_path, github):
        routes = route_repo(github, "psf", "requests", "gh_requests_issues_open_5.json",
                            "gh_requests_pulls_open_5.json", "gh_codecov-mcp_releases.json")
        track(db_path, "psf", "requests")
        sync_repo("psf", "requests", db_path=db_path)
        assert "since" not in routes["issues"].calls.last.request.url.params

    def test_an_empty_repository_syncs_to_nothing(self, db_path, github):
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        database = track(db_path, "maksimtech", "codecov-mcp")
        assert sync_repo("maksimtech", "codecov-mcp", db_path=db_path) == []
        summary = database.repo_summary("maksimtech", "codecov-mcp")
        assert (summary["open_issues"], summary["open_prs"], summary["latest_release"]) == (0, 0, None)


class TestLaterSyncs:
    def test_the_second_sync_asks_only_for_what_changed(self, db_path, github):
        routes = route_repo(github, "psf", "requests", "gh_requests_issues_open_5.json",
                            "gh_requests_pulls_open_5.json", "gh_codecov-mcp_releases.json")
        database = track(db_path, "psf", "requests")
        sync_repo("psf", "requests", db_path=db_path)
        first = database.repo_summary("psf", "requests")["last_synced_at"]

        sync_repo("psf", "requests", db_path=db_path)

        assert routes["issues"].calls.last.request.url.params["since"] == first

    def test_an_update_without_a_state_change_is_not_an_event(self, db_path, github):
        first = recorded("gh_requests_issues_open_5.json")["body"]
        base = f"{API}/repos/psf/requests"
        issues = github.get(f"{base}/issues").mock(return_value=httpx.Response(200, json=first))
        github.get(f"{base}/pulls").mock(return_value=empty_page())
        github.get(f"{base}/releases").mock(return_value=empty_page())
        track(db_path, "psf", "requests")
        sync_repo("psf", "requests", db_path=db_path)

        issues.mock(return_value=httpx.Response(200, json=an_hour_later(first)))
        assert sync_repo("psf", "requests", db_path=db_path) == []

    def test_closing_an_issue_is_an_event(self, db_path, github):
        first = recorded("gh_requests_issues_open_5.json")["body"]
        base = f"{API}/repos/psf/requests"
        issues = github.get(f"{base}/issues").mock(return_value=httpx.Response(200, json=first))
        github.get(f"{base}/pulls").mock(return_value=empty_page())
        github.get(f"{base}/releases").mock(return_value=empty_page())
        database = track(db_path, "psf", "requests")
        sync_repo("psf", "requests", db_path=db_path)

        when = after_the_last_sync()
        closed = [{**i, "state": "closed", "closed_at": when, "updated_at": when}
                  if i["number"] == 7630 else i for i in first]
        issues.mock(return_value=httpx.Response(200, json=closed))
        events = sync_repo("psf", "requests", db_path=db_path)

        assert events == [{"kind": "issue_closed", "number": 7630, "title": "Incorrect type annotation for iter_lines"}]
        pending = database.pending_events()
        assert [(e["kind"], e["ref_number"]) for e in pending] == [("issue_closed", 7630)]

    def test_a_pull_request_that_appears_merged_is_one_event(self, db_path, github):
        """Opened and merged between two syncs: the tracker saw it arrive merged, and
        says `pr_merged`, not `pr_opened` followed by nothing."""
        prs = recorded("gh_requests_pulls_open_5.json")["body"]
        base = f"{API}/repos/psf/requests"
        github.get(f"{base}/issues").mock(return_value=empty_page())
        pulls = github.get(f"{base}/pulls").mock(return_value=httpx.Response(200, json=prs[1:]))
        github.get(f"{base}/releases").mock(return_value=empty_page())
        track(db_path, "psf", "requests")
        sync_repo("psf", "requests", db_path=db_path)

        when = after_the_last_sync()
        merged = {**prs[0], "state": "closed", "merged_at": when, "updated_at": when}
        pulls.mock(return_value=httpx.Response(200, json=[merged, *prs[1:]]))
        events = sync_repo("psf", "requests", db_path=db_path)

        assert [(e["kind"], e["number"]) for e in events] == [("pr_merged", prs[0]["number"])]

    def test_a_new_release_is_an_event_and_a_known_one_is_not(self, db_path, github):
        releases = recorded("gh_cookieradar_releases.json")["body"]
        base = f"{API}/repos/maksimtech/cookieradar"
        github.get(f"{base}/issues").mock(return_value=empty_page())
        github.get(f"{base}/pulls").mock(return_value=empty_page())
        route = github.get(f"{base}/releases").mock(return_value=httpx.Response(200, json=releases[1:]))
        track(db_path, "maksimtech", "cookieradar")
        sync_repo("maksimtech", "cookieradar", db_path=db_path)

        route.mock(return_value=httpx.Response(200, json=releases))
        events = sync_repo("maksimtech", "cookieradar", db_path=db_path)

        assert events == [{"kind": "release_published", "tag": "v2026.44", "name": "CookieRadar 2026.44"}]
        assert sync_repo("maksimtech", "cookieradar", db_path=db_path) == []

    def test_a_draft_release_is_neither_stored_nor_an_event(self, db_path, github):
        """Derived from the recorded v2026.44: a draft has `published_at: null`."""
        real = recorded("gh_cookieradar_releases.json")["body"][0]
        draft = {**real, "id": real["id"] + 1, "tag_name": "v2026.45-draft", "name": "draft",
                 "draft": True, "published_at": None}
        base = f"{API}/repos/maksimtech/cookieradar"
        github.get(f"{base}/issues").mock(return_value=empty_page())
        github.get(f"{base}/pulls").mock(return_value=empty_page())
        route = github.get(f"{base}/releases").mock(return_value=httpx.Response(200, json=[real]))
        database = track(db_path, "maksimtech", "cookieradar")
        sync_repo("maksimtech", "cookieradar", db_path=db_path)

        route.mock(return_value=httpx.Response(200, json=[draft, real]))
        try:
            events = sync_repo("maksimtech", "cookieradar", db_path=db_path)
        except sqlite3.IntegrityError as exc:
            pytest.fail(f"draft release crashed the sync: {exc}")

        assert events == []
        assert database.repo_summary("maksimtech", "cookieradar")["latest_release"]["tag_name"] == "v2026.44"


class TestRefusals:
    def test_a_repository_nobody_tracked_is_refused(self, db_path, github):
        with pytest.raises(NotTracked, match="releaseradar track psf/requests"):
            sync_repo("psf", "requests", db_path=db_path)
        assert _db.open_db(db_path).list_repos() == []

    def test_a_github_error_leaves_the_state_as_it_was(self, db_path, github):
        """The 404 arrives on the first request; nothing was written, and the repo
        is still waiting for its first sync."""
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        database = track(db_path, "maksimtech", "does-not-exist-xyz")
        with pytest.raises(gh.GitHubError, match="not found"):
            sync_repo("maksimtech", "does-not-exist-xyz", db_path=db_path)
        assert database.repo_summary("maksimtech", "does-not-exist-xyz")["last_synced_at"] is None


class TestSyncAll:
    def test_every_tracked_repository_is_synced_and_one_failure_does_not_stop_the_rest(self, db_path, github):
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        database = track(db_path, "maksimtech", "codecov-mcp")
        database.add_repo("maksimtech", "does-not-exist-xyz")

        results, errors = sync_all(db_path=db_path)

        assert results == {"maksimtech/codecov-mcp": []}
        assert list(errors) == ["maksimtech/does-not-exist-xyz"]
        assert "not found" in errors["maksimtech/does-not-exist-xyz"]


class TestRepositoriesWithoutPullRequests:
    """torvalds/linux, the README's own first example: GitHub serves the repository,
    an empty issue list (`has_issues: false`) and an empty release list, and
    answers 404 to every `/pulls` query — pull requests are switched off there
    entirely (the search API finds none). Recorded on 2026-10-10, after the
    first user of 2026.1 hit it: `track` succeeded and `sync` reported the
    repository as not found."""

    def route_linux(self, github):
        base = f"{API}/repos/torvalds/linux"
        github.get(base).mock(return_value=replay("gh_linux_repo.json"))
        return route_repo(github, "torvalds", "linux",
                          "gh_linux_issues_all.json", "gh_linux_pulls_404.json", "gh_linux_releases.json")

    def test_a_404_on_pulls_alone_is_not_a_missing_repository(self, db_path, github):
        self.route_linux(github)
        database = track(db_path, "torvalds", "linux")
        warnings: list[str] = []

        events = sync_repo("torvalds", "linux", db_path=db_path, on_warning=warnings.append)

        assert events == []
        assert database.repo_summary("torvalds", "linux")["last_synced_at"] is not None
        assert warnings and "pull requests" in warnings[0] and "torvalds/linux" in warnings[0]

    def test_the_repository_is_asked_about_once_and_only_on_the_404(self, db_path, github):
        routes = self.route_linux(github)
        track(db_path, "torvalds", "linux")
        sync_repo("torvalds", "linux", db_path=db_path)
        repo_calls = [c for c in github.calls if c.request.url.path == "/repos/torvalds/linux"]
        assert len(repo_calls) == 1
        assert routes["issues"].called and routes["releases"].called

    def test_a_repository_that_is_really_gone_is_still_not_found(self, db_path, github):
        """The same 404 on /pulls with the repository itself answering 404: gone."""
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(return_value=replay("gh_missing_repo_404.json"))
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        track(db_path, "maksimtech", "does-not-exist-xyz")
        with pytest.raises(gh.GitHubError, match="not found"):
            sync_repo("maksimtech", "does-not-exist-xyz", db_path=db_path)
