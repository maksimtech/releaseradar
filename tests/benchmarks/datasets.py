"""Datasets for the benchmarks, the size of a real sync.

Derived from the recordings under tests/fixtures, the same ones the suite
replays: five issues and five pull requests of psf/requests and seventeen
releases of maksimtech/cookieradar, repeated with new numbers until they are
large enough to measure. Nothing is hand-written, and nothing reaches the
network.
"""

from __future__ import annotations

import httpx

from tests.conftest import API, recorded


def scaled(name: str, count: int) -> list[dict]:
    """`count` items derived from one recording, renumbered so none collide."""
    body = recorded(name)["body"]
    items = []
    for index in range(count):
        item = dict(body[index % len(body)])
        number = 100_000 + index
        item["number"] = number
        item["html_url"] = item["html_url"].rsplit("/", 1)[0] + f"/{number}"
        items.append(item)
    return items


def scaled_releases(count: int) -> list[dict]:
    body = recorded("gh_cookieradar_releases.json")["body"]
    releases = []
    for index in range(count):
        release = dict(body[index % len(body)])
        release["tag_name"] = f"v{index // len(body)}.{release['tag_name']}"
        releases.append(release)
    return releases


def paged(items: list[dict], url: str, per_page: int = 100):
    """A respx side effect serving `items` as GitHub does: `per_page` at a time,
    each page naming the next one in its `Link` header."""

    def respond(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        chunk = items[(page - 1) * per_page: page * per_page]
        headers = {}
        if page * per_page < len(items):
            headers["link"] = f'<{url}?per_page={per_page}&page={page + 1}>; rel="next"'
        return httpx.Response(200, json=chunk, headers=headers)

    return respond


def route_scaled_repo(mock, owner: str, name: str, issues: list[dict], pulls: list[dict],
                      releases: list[dict]) -> None:
    base = f"{API}/repos/{owner}/{name}"
    for endpoint, items in (("issues", issues), ("pulls", pulls), ("releases", releases)):
        url = f"{base}/{endpoint}"
        mock.get(url).mock(side_effect=paged(items, url))
