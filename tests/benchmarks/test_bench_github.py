"""What a sync spends on GitHub's answers before it touches the database:
walking the pages, and turning each payload into the row that is stored."""

from __future__ import annotations

import pytest
import respx

from releaseradar import github as gh
from tests.benchmarks.datasets import paged, scaled, scaled_releases
from tests.conftest import API

ISSUES = scaled("gh_requests_issues_open_5.json", 500)
PULLS = scaled("gh_requests_pulls_open_5.json", 500)
RELEASES = scaled_releases(170)


def test_normalise_issues(benchmark):
    # The recording mixes issues and pull requests, as /issues does; the walk
    # drops the latter before anything is normalised.
    issues = [i for i in ISSUES if "pull_request" not in i]

    result = benchmark(lambda: [gh.normalise_issue(raw) for raw in issues])

    assert len(result) == len(issues)


def test_normalise_pull_requests(benchmark):
    """Every pull request body goes through the `Fixes #N` expression."""
    result = benchmark(lambda: [gh.normalise_pr(raw) for raw in PULLS])

    assert len(result) == len(PULLS)
    assert any(pr["linked_issues"] for pr in result)


def test_normalise_releases(benchmark):
    result = benchmark(lambda: [gh.normalise_release(raw) for raw in RELEASES])

    assert len(result) == len(RELEASES)


def test_extract_linked_issues_from_long_bodies(benchmark):
    bodies = [(raw.get("body") or "", raw["title"]) for raw in PULLS]

    result = benchmark(lambda: [gh.extract_linked_issues(body, title) for body, title in bodies])

    assert len(result) == len(bodies)


@pytest.mark.parametrize("pages", [1, 5])
def test_fetch_issues_follows_the_link_header(benchmark, pages):
    """The issues walk: one request per page, the pull requests GitHub mixes in
    filtered out, the next page taken from `Link`."""
    items = ISSUES[: pages * 100]
    url = f"{API}/repos/psf/requests/issues"
    with respx.mock(assert_all_mocked=True) as mock:
        mock.get(url).mock(side_effect=paged(items, url))

        result = benchmark(gh.fetch_issues, "psf", "requests", state="open")

    assert len(result) == sum("pull_request" not in i for i in items)
