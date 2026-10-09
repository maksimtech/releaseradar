"""GitHub REST API client — read-only, no auth required for public repos."""

from __future__ import annotations

import re
from typing import Any

import httpx

_BASE = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Pattern to extract "Fixes #N", "Closes #N", "Resolves #N" from PR body/title
_CLOSES_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
    re.IGNORECASE,
)


def _client(token: str | None = None) -> httpx.Client:
    headers = dict(_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(headers=headers, timeout=30)


def _paginate(client: httpx.Client, url: str, params: dict | None = None) -> list[dict]:
    results: list[dict] = []
    page = 1
    while True:
        resp = client.get(url, params={**(params or {}), "per_page": 100, "page": page})
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        results.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return results


# ── public API ───────────────────────────────────────────────────────────────

def fetch_issues(owner: str, repo: str, token: str | None = None,
                 since: str | None = None) -> list[dict[str, Any]]:
    """Return open + recently closed issues (not PRs)."""
    with _client(token) as c:
        params: dict = {"state": "all", "sort": "updated", "direction": "desc"}
        if since:
            params["since"] = since
        raw = _paginate(c, f"{_BASE}/repos/{owner}/{repo}/issues", params)
    # GitHub /issues returns PRs too; filter them out
    return [i for i in raw if "pull_request" not in i]


def fetch_pull_requests(owner: str, repo: str, token: str | None = None,
                        since: str | None = None) -> list[dict[str, Any]]:
    with _client(token) as c:
        params: dict = {"state": "all", "sort": "updated", "direction": "desc"}
        if since:
            params["since"] = since
        return _paginate(c, f"{_BASE}/repos/{owner}/{repo}/pulls", params)


def fetch_releases(owner: str, repo: str, token: str | None = None) -> list[dict[str, Any]]:
    with _client(token) as c:
        return _paginate(c, f"{_BASE}/repos/{owner}/{repo}/releases")


def repo_exists(owner: str, repo: str, token: str | None = None) -> bool:
    with _client(token) as c:
        resp = c.get(f"{_BASE}/repos/{owner}/{repo}")
        return resp.status_code == 200


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
    return {
        "tag_name": raw["tag_name"],
        "name": raw.get("name") or raw["tag_name"],
        "html_url": raw["html_url"],
        "published_at": raw["published_at"],
        "prerelease": 1 if raw.get("prerelease") else 0,
        "body": raw.get("body"),
    }
