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


def _get(client: httpx.Client, url: str, subject: str, params: dict | None = None) -> httpx.Response:
    try:
        resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise GitHubError(f"cannot reach api.github.com: {exc}") from exc
    if resp.status_code >= 400:
        raise _explain(resp, subject)
    return resp


def _paginate(client: httpx.Client, url: str, subject: str, params: dict | None = None,
              stop_when: Callable[[dict], bool] | None = None) -> list[dict]:
    """Every page of a list endpoint, oldest-first within GitHub's ordering.

    `stop_when(item)` ends the walk at the first item it accepts (that item and
    the rest of its page are dropped): with `sort=updated&direction=desc` it is
    how a sync stops at the first thing it has already seen.
    """
    results: list[dict] = []
    page = 1
    while True:
        resp = _get(client, url, subject, params={**(params or {}), "per_page": _PAGE, "page": page})
        batch = resp.json()
        if not batch:
            break
        if stop_when is not None:
            for index, item in enumerate(batch):
                if stop_when(item):
                    results.extend(batch[:index])
                    return results
        results.extend(batch)
        if len(batch) < _PAGE:
            break
        page += 1
    return results


def _updated_before(since: str | None) -> Callable[[dict], bool] | None:
    if not since:
        return None
    return lambda item: str(item.get("updated_at", "")) < since


# ── public API ───────────────────────────────────────────────────────────────

def fetch_issues(owner: str, repo: str, token: str | None = None,
                 since: str | None = None) -> list[dict[str, Any]]:
    """Issues (not PRs), all states, most recently updated first.

    `since` is an ISO-8601 instant: only issues updated at or after it come back,
    which is GitHub's own `since` filter on this endpoint.
    """
    params: dict = {"state": "all", "sort": "updated", "direction": "desc"}
    if since:
        params["since"] = since
    with _client(token) as c:
        raw = _paginate(c, f"{_BASE}/repos/{owner}/{repo}/issues", f"{owner}/{repo}", params)
    # GitHub /issues returns PRs too; filter them out
    return [i for i in raw if "pull_request" not in i]


def fetch_pull_requests(owner: str, repo: str, token: str | None = None,
                        since: str | None = None) -> list[dict[str, Any]]:
    """Pull requests, all states, most recently updated first.

    `/pulls` has no `since` parameter (GitHub ignores it), so the walk is cut at
    the first PR updated before `since` instead.
    """
    params: dict = {"state": "all", "sort": "updated", "direction": "desc"}
    with _client(token) as c:
        return _paginate(c, f"{_BASE}/repos/{owner}/{repo}/pulls", f"{owner}/{repo}", params,
                         stop_when=_updated_before(since))


def fetch_releases(owner: str, repo: str, token: str | None = None) -> list[dict[str, Any]]:
    with _client(token) as c:
        return _paginate(c, f"{_BASE}/repos/{owner}/{repo}/releases", f"{owner}/{repo}")


def repo_exists(owner: str, repo: str, token: str | None = None) -> bool:
    """True for a repository the caller can see, False for a 404.

    Anything else — a rate limit, a bad token, GitHub down — is raised, because
    "not found" would be a lie about a repository that may well exist.
    """
    with _client(token) as c:
        try:
            resp = c.get(f"{_BASE}/repos/{owner}/{repo}")
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
