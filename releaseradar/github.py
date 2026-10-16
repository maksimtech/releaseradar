"""GitHub REST API client — read-only, no auth required for public repos.

Everything that can go wrong on the wire comes back as one `GitHubError` with a
sentence a person can act on: the CLI prints it, the MCP server returns it, and
neither shows a traceback for a repository that does not exist or a rate limit
that ran out.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import httpx

_BASE = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
_PAGE = 100

# "Fixes #N", "Closes #N", "Resolves #N" in a PR body or title. GitHub's own list
# of keywords, matched as whole words: `prefixes #12` is not `fixes #12`, and
# `unresolved #7` does not resolve anything.
_CLOSES_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+#(\d+)\b",
    re.IGNORECASE,
)


class GitHubError(Exception):
    """A GitHub request that did not give an answer worth parsing.

    `status` is the HTTP status when there was one, None for a network failure.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _client(token: str | None = None) -> httpx.Client:
    headers = dict(_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(headers=headers, timeout=30)


def _rate_limit_message(resp: httpx.Response) -> str:
    limit = resp.headers.get("x-ratelimit-limit", "?")
    reset = resp.headers.get("x-ratelimit-reset")
    when = ""
    if reset and reset.isdigit():
        at = datetime.fromtimestamp(int(reset), tz=UTC)
        when = f", resets at {at:%H:%M} UTC"
    hint = ("set GITHUB_TOKEN to raise it from 60 to 5000 requests per hour"
            if limit == "60" or "Authorization" not in resp.request.headers
            else "wait for the reset")
    return f"GitHub API rate limit exceeded ({limit} requests per hour{when}); {hint}."


def _explain(resp: httpx.Response, subject: str) -> GitHubError:
    status = resp.status_code
    if status in (403, 429) and resp.headers.get("x-ratelimit-remaining") == "0":
        return GitHubError(_rate_limit_message(resp), status)
    if status == 404:
        return GitHubError(f"{subject} not found or not accessible (GitHub answered 404).", status)
    if status == 401:
        return GitHubError("GitHub rejected the token in GITHUB_TOKEN (401).", status)
    detail = ""
    with suppress(ValueError):
        detail = resp.json().get("message", "")
    return GitHubError(f"GitHub answered {status} for {subject}" + (f": {detail}" if detail else "."), status)


_REDIRECTS = (301, 302, 307, 308)
_MAX_REDIRECTS = 5


def _follow(client: httpx.Client, url: str, subject: str, params: dict | None = None) -> httpx.Response:
    """GET, following GitHub's redirects within https://api.github.com and nowhere else.

    A transferred or renamed repository answers its old name with 301 to
    /repositories/<id>/..., and the body says to follow it. Read unfollowed, that
    body was walked as a page of issues. The client itself follows nothing: the
    token is for api.github.com, so a redirect anywhere else is refused unsent.
    """
    resp = client.get(url, params=params)
    for _ in range(_MAX_REDIRECTS):
        if resp.status_code not in _REDIRECTS:
            return resp
        target = resp.url.join(resp.headers.get("location", ""))
        if target.scheme != "https" or target.host != "api.github.com":
            raise GitHubError(
                f"GitHub redirected {subject} to {target.scheme}://{target.host}: not followed, "
                "the token goes to https://api.github.com only.", resp.status_code)
        resp = client.get(target)
    if resp.status_code in _REDIRECTS:
        raise GitHubError(f"GitHub answered {subject} with too many redirects.", resp.status_code)
    return resp


def _get(client: httpx.Client, url: str, subject: str, params: dict | None = None) -> httpx.Response:
    try:
        resp = _follow(client, url, subject, params)
    except httpx.HTTPError as exc:
        raise GitHubError(f"cannot reach api.github.com: {exc}") from exc
    if resp.status_code >= 400:
        raise _explain(resp, subject)
    return resp


# Called after every page with (items fetched so far, pages fetched so far).
OnPage = Callable[[int, int], None]


def _paginate(client: httpx.Client, url: str, subject: str, params: dict | None = None,
              stop_when: Callable[[dict], bool] | None = None,
              on_page: OnPage | None = None, keep: Callable[[dict], bool] | None = None) -> list[dict]:
    """Every page of a list endpoint, in GitHub's ordering.

    The next page is the one GitHub names in the `Link` header, not a `page`
    number counted here: on large datasets GitHub answers 422 to `page=100`
    ("please use cursor based pagination") and puts an `after=` cursor in the
    link from the first page on. python/cpython, with 156 000 issues and pull
    requests, was the first tracked repository to say so, on 2026-10-10.

    `stop_when(item)` ends the walk at the first item it accepts (that item and
    the rest of its page are dropped): with `sort=updated&direction=desc` it is
    how a sync stops at the first thing it has already seen. `keep(item)`
    filters each page before it is counted or returned. `on_page` is told how
    far the walk has got after every page, which is what a spinner that stays
    silent for two minutes is missing.
    """
    results: list[dict] = []
    pages = 0
    resp = _get(client, url, subject, params={**(params or {}), "per_page": _PAGE})
    while True:
        batch = resp.json()
        if not batch:
            break
        pages += 1
        if stop_when is not None:
            for index, item in enumerate(batch):
                if stop_when(item):
                    results.extend(i for i in batch[:index] if keep is None or keep(i))
                    if on_page is not None:
                        on_page(len(results), pages)
                    return results
        results.extend(i for i in batch if keep is None or keep(i))
        if on_page is not None:
            on_page(len(results), pages)
        next_page = resp.links.get("next", {}).get("url")
        if not next_page:
            break
        resp = _get(client, next_page, subject)
    return results


def _updated_before(since: str | None) -> Callable[[dict], bool] | None:
    if not since:
        return None
    return lambda item: str(item.get("updated_at", "")) < since


# ── public API ───────────────────────────────────────────────────────────────

def fetch_issues(owner: str, repo: str, token: str | None = None,
                 since: str | None = None, state: str = "all",
                 on_page: OnPage | None = None) -> list[dict[str, Any]]:
    """Issues (not PRs), most recently updated first.

    `since` is an ISO-8601 instant: only issues updated at or after it come back,
    which is GitHub's own `since` filter on this endpoint. `state` is `all`,
    `open` or `closed`; a first sync asks for `open` only, because what was
    closed before anyone watched is not a transition anyone will see.
    """
    params: dict = {"state": state, "sort": "updated", "direction": "desc"}
    if since:
        params["since"] = since
    with _client(token) as c:
        # GitHub /issues returns PRs too; they are not counted and not returned.
        return _paginate(c, f"{_BASE}/repos/{owner}/{repo}/issues", f"{owner}/{repo}", params,
                         on_page=on_page, keep=lambda i: "pull_request" not in i)


def fetch_pull_requests(owner: str, repo: str, token: str | None = None,
                        since: str | None = None, state: str = "all",
                        on_page: OnPage | None = None) -> list[dict[str, Any]]:
    """Pull requests, most recently updated first.

    `/pulls` has no `since` parameter (GitHub ignores it), so the walk is cut at
    the first PR updated before `since` instead.
    """
    params: dict = {"state": state, "sort": "updated", "direction": "desc"}
    with _client(token) as c:
        return _paginate(c, f"{_BASE}/repos/{owner}/{repo}/pulls", f"{owner}/{repo}", params,
                         stop_when=_updated_before(since), on_page=on_page)


def fetch_releases(owner: str, repo: str, token: str | None = None,
                   on_page: OnPage | None = None) -> list[dict[str, Any]]:
    with _client(token) as c:
        return _paginate(c, f"{_BASE}/repos/{owner}/{repo}/releases", f"{owner}/{repo}", on_page=on_page)


def open_items(owner: str, repo: str, token: str | None = None) -> int | None:
    """How many issues and pull requests are open, from the repository record —
    the size of a first sync, known before it starts. None for a repository
    GitHub will not describe (the sync itself will say why)."""
    with _client(token) as c:
        try:
            resp = _follow(c, f"{_BASE}/repos/{owner}/{repo}", f"{owner}/{repo}")
        except (httpx.HTTPError, GitHubError):
            return None
    if resp.status_code != 200:
        return None
    count = resp.json().get("open_issues_count")
    return int(count) if isinstance(count, int) else None


def repo_exists(owner: str, repo: str, token: str | None = None) -> bool:
    """True for a repository the caller can see, False for a 404.

    Anything else — a rate limit, a bad token, GitHub down — is raised, because
    "not found" would be a lie about a repository that may well exist.
    """
    with _client(token) as c:
        try:
            resp = _follow(c, f"{_BASE}/repos/{owner}/{repo}", f"{owner}/{repo}")
        except httpx.HTTPError as exc:
            raise GitHubError(f"cannot reach api.github.com: {exc}") from exc
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    raise _explain(resp, f"{owner}/{repo}")


def extract_linked_issues(pr_body: str | None, pr_title: str | None = None) -> list[int]:
    """Parse 'Fixes #N' references from PR body and title."""
    text = " ".join(filter(None, [pr_body, pr_title]))
    return [int(m) for m in _CLOSES_RE.findall(text)]


# ── normalise GitHub payloads to our DB schema ───────────────────────────────

def normalise_issue(raw: dict) -> dict:
    return {
        "number": raw["number"],
        "title": raw["title"],
        "state": raw["state"],
        "html_url": raw["html_url"],
        "opened_at": raw["created_at"],
        "closed_at": raw.get("closed_at"),
        "updated_at": raw["updated_at"],
    }


def normalise_pr(raw: dict) -> dict:
    linked = extract_linked_issues(raw.get("body"), raw.get("title"))
    state = "merged" if raw.get("merged_at") else raw["state"]
    return {
        "number": raw["number"],
        "title": raw["title"],
        "state": state,
        "html_url": raw["html_url"],
        "opened_at": raw["created_at"],
        "merged_at": raw.get("merged_at"),
        "updated_at": raw["updated_at"],
        "linked_issues": ",".join(str(n) for n in linked) if linked else None,
    }


def normalise_release(raw: dict) -> dict:
    """A release as stored. A draft has no `published_at`; `draft` says so and the
    sync leaves drafts out, because nothing was published."""
    return {
        "tag_name": raw["tag_name"],
        "name": raw.get("name") or raw["tag_name"],
        "html_url": raw["html_url"],
        "published_at": raw.get("published_at"),
        "prerelease": 1 if raw.get("prerelease") else 0,
        "draft": bool(raw.get("draft")) or raw.get("published_at") is None,
        "body": raw.get("body"),
    }
