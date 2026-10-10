"""The GitHub client: what it parses, and what it says when GitHub says no."""

from __future__ import annotations

import httpx
import pytest

from releaseradar import github as gh
from tests.conftest import API, empty_page, recorded, replay

# ── extract_linked_issues ─────────────────────────────────────────────────────


class TestExtractLinkedIssues:
    def test_empty_body(self):
        assert gh.extract_linked_issues(None) == []

    @pytest.mark.parametrize("text, expected", [
        ("Fixes #42", [42]),
        ("Closes #10", [10]),
        ("Resolves #7", [7]),
        ("Fixes #1, closes #2, resolves #3", [1, 2, 3]),
        ("FIXES #99", [99]),
        ("fixed: #5", [5]),
        ("Just a regular PR description", []),
    ])
    def test_github_keywords(self, text, expected):
        assert gh.extract_linked_issues(text) == expected

    def test_title_too(self):
        assert gh.extract_linked_issues("body", "Fix #5") == [5]

    @pytest.mark.parametrize("text", ["prefixes #12", "suffixed #3", "unresolved #7", "disclosed #9"])
    def test_a_keyword_inside_another_word_links_nothing(self, text):
        """`prefixes #12` is not `fixes #12`; GitHub matches whole words and so does this."""
        assert gh.extract_linked_issues(text) == []

    def test_real_pull_request_bodies(self):
        """Recorded from psf/requests: #7068 says "Closes #5000", #7499 says
        "Fixes #6255.", the other three use no closing keyword — and titles that
        start with "Fix:" or "fix:" followed by words, not a number, link nothing."""
        links = {pr["number"]: gh.extract_linked_issues(pr.get("body"), pr.get("title"))
                 for pr in recorded("gh_requests_pulls_open_5.json")["body"]}
        assert links == {7586: [], 7413: [], 7068: [5000], 7619: [], 7499: [6255]}


# ── normalisation of recorded payloads ───────────────────────────────────────


class TestNormalise:
    def test_issue_from_a_recording(self):
        raw = next(i for i in recorded("gh_requests_issues_open_5.json")["body"] if "pull_request" not in i)
        norm = gh.normalise_issue(raw)
        assert norm["number"] == raw["number"]
        assert norm["state"] == "open"
        assert norm["opened_at"] == raw["created_at"]
        assert norm["closed_at"] is None

    def test_open_pr_from_a_recording(self):
        raw = recorded("gh_requests_pulls_open_5.json")["body"][0]
        norm = gh.normalise_pr(raw)
        assert norm["state"] == "open"
        assert norm["merged_at"] is None

    def test_merged_pr_is_merged_not_closed(self):
        """GitHub reports a merged PR as `state: closed` with `merged_at` set."""
        raw = {**recorded("gh_requests_pulls_open_5.json")["body"][0],
               "state": "closed", "merged_at": "2026-10-10T10:00:00Z", "body": "Fixes #10"}
        norm = gh.normalise_pr(raw)
        assert norm["state"] == "merged"
        assert norm["linked_issues"] == "10"

    def test_release_from_a_recording(self):
        raw = recorded("gh_cookieradar_releases.json")["body"][0]
        norm = gh.normalise_release(raw)
        assert norm["tag_name"] == "v2026.44"
        assert norm["prerelease"] == 0
        assert norm["draft"] is False
        assert norm["published_at"] == raw["published_at"]

    def test_a_release_without_a_name_is_named_after_its_tag(self):
        raw = {**recorded("gh_cookieradar_releases.json")["body"][0], "name": None}
        assert gh.normalise_release(raw)["name"] == raw["tag_name"]

    def test_a_draft_is_marked_as_such(self):
        """Derived from the recorded v2026.44: GitHub serves a draft with
        `draft: true` and `published_at: null`."""
        raw = {**recorded("gh_cookieradar_releases.json")["body"][0], "draft": True, "published_at": None}
        assert gh.normalise_release(raw)["draft"] is True


# ── repo_exists ───────────────────────────────────────────────────────────────


class TestRepoExists:
    def test_exists(self, github):
        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        assert gh.repo_exists("maksimtech", "codecov-mcp") is True

    def test_not_found(self, github):
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(
            return_value=replay("gh_missing_repo_404.json"))
        assert gh.repo_exists("maksimtech", "does-not-exist-xyz") is False

    def test_a_rate_limit_is_not_a_missing_repository(self, github):
        """403 with the quota at zero answers nothing about the repository; saying
        "not found" would be a lie. Derived from GitHub's documented refusal."""
        github.get(f"{API}/repos/psf/requests").mock(return_value=RATE_LIMITED)
        with pytest.raises(gh.GitHubError, match="rate limit"):
            gh.repo_exists("psf", "requests")

    def test_the_token_goes_in_the_authorization_header(self, github):
        route = github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(
            return_value=replay("gh_codecov-mcp_repo.json"))
        gh.repo_exists("maksimtech", "codecov-mcp", token="ghp_example")
        assert route.calls.last.request.headers["Authorization"] == "Bearer ghp_example"


# ── errors with a sentence in them ────────────────────────────────────────────

RATE_LIMITED = httpx.Response(
    403,
    json={"message": "API rate limit exceeded for 93.0.0.1. (But here's the good news: Authenticated "
                     "requests get a higher rate limit. Check out the documentation for more details.)",
          "documentation_url": "https://docs.github.com/rest/overview/rate-limits-for-the-rest-api"},
    headers={"x-ratelimit-limit": "60", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1791651600"},
)


class TestErrors:
    def test_a_404_names_the_repository(self, github):
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        with pytest.raises(gh.GitHubError) as exc:
            gh.fetch_issues("maksimtech", "does-not-exist-xyz")
        assert exc.value.status == 404
        assert "maksimtech/does-not-exist-xyz" in str(exc.value)
        assert "not found" in str(exc.value)

    def test_a_rate_limit_says_when_and_how(self, github):
        github.get(f"{API}/repos/psf/requests/issues").mock(return_value=RATE_LIMITED)
        with pytest.raises(gh.GitHubError) as exc:
            gh.fetch_issues("psf", "requests")
        message = str(exc.value)
        assert "rate limit" in message
        assert "60" in message
        assert "GITHUB_TOKEN" in message
        assert "UTC" in message

    def test_a_network_failure_is_one_sentence(self, github):
        github.get(f"{API}/repos/psf/requests/releases").mock(side_effect=httpx.ConnectError("name resolution failed"))
        with pytest.raises(gh.GitHubError, match="cannot reach api.github.com"):
            gh.fetch_releases("psf", "requests")

    def test_a_bad_token_is_named(self, github):
        github.get(f"{API}/repos/psf/requests/releases").mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"}))
        with pytest.raises(gh.GitHubError, match="GITHUB_TOKEN"):
            gh.fetch_releases("psf", "requests", token="ghp_expired")


# ── pagination and `since` ───────────────────────────────────────────────────


class TestPagination:
    def test_issues_pass_since_to_github(self, github):
        route = github.get(f"{API}/repos/psf/requests/issues").mock(
            return_value=replay("gh_requests_issues_open_5.json"))
        gh.fetch_issues("psf", "requests", since="2026-10-01T00:00:00Z")
        assert route.calls.last.request.url.params["since"] == "2026-10-01T00:00:00Z"

    def test_pull_requests_are_filtered_out_of_the_issues_endpoint(self, github):
        """GitHub's /issues lists PRs too; two of the five recorded items are PRs."""
        github.get(f"{API}/repos/psf/requests/issues").mock(return_value=replay("gh_requests_issues_open_5.json"))
        issues = gh.fetch_issues("psf", "requests")
        assert len(issues) == 3
        assert all("pull_request" not in i for i in issues)

    def test_pull_requests_stop_at_the_first_one_updated_before_since(self, github):
        """/pulls ignores `since`, so the walk is cut by `updated_at`: the five recorded
        PRs are sorted newest-updated first, and a `since` between the second and the
        third keeps exactly two."""
        prs = recorded("gh_requests_pulls_open_5.json")["body"]
        route = github.get(f"{API}/repos/psf/requests/pulls").mock(
            return_value=replay("gh_requests_pulls_open_5.json"))
        since = prs[2]["updated_at"]
        kept = gh.fetch_pull_requests("psf", "requests", since=since[:-1] + "Z")
        assert [p["number"] for p in kept] == [p["number"] for p in prs if p["updated_at"] >= since]
        assert "since" not in route.calls.last.request.url.params

    def test_the_next_page_is_the_one_github_names_in_the_link_header(self, github):
        """Recorded from python/cpython: page 1 of 2-per-page comes with
        `Link: <.../repositories/81598961/issues?...&page=2&after=Y3Vyc...>; rel="next"`.
        The walk follows that URL - cursor included, on the /repositories/<id>
        path GitHub chose - and stops when a page carries no `next`."""
        first = replay("gh_cpython_issues_page1_of2.json", last_page=False)
        next_url = first.links["next"]["url"]
        github.get(f"{API}/repos/python/cpython/issues").mock(return_value=first)
        second = github.get(next_url).mock(return_value=httpx.Response(200, json=[{"number": 1, "updated_at": "x"}]))

        items = gh.fetch_issues("python", "cpython")

        assert second.called
        assert "after=" in str(second.calls.last.request.url)
        assert len(items) == len([i for i in first.json() if "pull_request" not in i]) + 1
        assert github.calls.call_count == 2

    def test_a_full_page_without_a_next_link_is_the_last_page(self, github):
        """Counting pages is what GitHub refuses on large datasets (422 at
        page=100, recorded); a page is the last one when no `next` is offered,
        however full it is."""
        one = recorded("gh_cookieradar_releases.json")["body"]
        full = [{**r, "id": r["id"] + n, "tag_name": f"{r['tag_name']}-{n}"} for n in range(6) for r in one][:100]
        route = github.get(f"{API}/repos/maksimtech/cookieradar/releases").mock(
            return_value=httpx.Response(200, json=full))
        assert len(gh.fetch_releases("maksimtech", "cookieradar")) == 100
        assert route.call_count == 1

    def test_the_refusal_of_page_based_pagination_is_quoted(self, github):
        github.get(f"{API}/repos/python/cpython/issues").mock(
            return_value=replay("gh_cpython_issues_page100_422.json"))
        with pytest.raises(gh.GitHubError) as exc:
            gh.fetch_issues("python", "cpython")
        assert exc.value.status == 422
        assert "cursor based pagination" in str(exc.value)

    def test_state_is_passed_through(self, github):
        issues = github.get(f"{API}/repos/psf/requests/issues").mock(return_value=empty_page())
        pulls = github.get(f"{API}/repos/psf/requests/pulls").mock(return_value=empty_page())
        gh.fetch_issues("psf", "requests", state="open")
        gh.fetch_pull_requests("psf", "requests", state="open")
        assert issues.calls.last.request.url.params["state"] == "open"
        assert pulls.calls.last.request.url.params["state"] == "open"
