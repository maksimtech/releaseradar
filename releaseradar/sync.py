"""Sync a tracked repo: fetch GitHub state, update DB, emit events.

An event is a transition this tracker saw: open to closed, open to merged, a
release that was not there at the last sync. The first sync of a repository
sees no transition — it seeds the state, in one transaction, and reports
nothing; a comment or a label on an open issue moves `updated_at` and nothing
else, and is not an event either.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from . import db as _db
from . import github as gh


class NotTracked(ValueError):
    """A repository nobody asked to track."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _issue_kind(state: str) -> str:
    return "issue_opened" if state == "open" else "issue_closed"


def _pr_kind(state: str) -> str:
    return {"open": "pr_opened", "merged": "pr_merged"}.get(state, "pr_closed")


def sync_repo(owner: str, name: str, token: str | None = None,
              db_path: Path | None = None) -> list[dict]:
    """
    Pull what changed on GitHub since the last sync of one tracked repo, persist
    it, and return the events that change produced.

    Raises `NotTracked` for a repository that was never `track`ed and
    `gh.GitHubError` for anything GitHub refused to answer.
    """
    database = _db.open_db(db_path or _db._DEFAULT_DB)
    repo = database.get_repo(owner, name)
    if repo is None:
        raise NotTracked(f"{owner}/{name} is not being tracked; run `releaseradar track {owner}/{name}` first.")
    repo_id = int(repo["id"])
    since = repo["last_synced_at"]
    seeding = since is None
    started = _now()

    issues = gh.fetch_issues(owner, name, token, since=since)
    pulls = gh.fetch_pull_requests(owner, name, token, since=since)
    releases = gh.fetch_releases(owner, name, token)

    new_events: list[dict] = []
    with database.transaction():
        for raw in issues:
            norm = gh.normalise_issue(raw)
            previous = database.upsert_issue(repo_id, norm)
            if seeding or previous == norm["state"]:
                continue
            kind = _issue_kind(norm["state"])
            database.add_event(
                repo_id, kind,
                ref_number=norm["number"],
                payload=json.dumps({"title": norm["title"], "url": norm["html_url"]}),
            )
            new_events.append({"kind": kind, "number": norm["number"], "title": norm["title"]})

        for raw in pulls:
            norm = gh.normalise_pr(raw)
            previous = database.upsert_pr(repo_id, norm)
            if seeding or previous == norm["state"]:
                continue
            kind = _pr_kind(norm["state"])
            database.add_event(
                repo_id, kind,
                ref_number=norm["number"],
                payload=json.dumps({
                    "title": norm["title"],
                    "url": norm["html_url"],
                    "linked_issues": norm["linked_issues"],
                }),
            )
            new_events.append({"kind": kind, "number": norm["number"], "title": norm["title"]})

        for raw in releases:
            norm = gh.normalise_release(raw)
            if norm["draft"]:
                continue
            is_new = database.upsert_release(repo_id, norm)
            if seeding or not is_new:
                continue
            database.add_event(
                repo_id, "release_published",
                ref_tag=norm["tag_name"],
                payload=json.dumps({
                    "name": norm["name"],
                    "url": norm["html_url"],
                    "prerelease": bool(norm["prerelease"]),
                }),
            )
            new_events.append({"kind": "release_published", "tag": norm["tag_name"], "name": norm["name"]})

        database.set_last_synced(repo_id, started)

    return new_events


def sync_all(token: str | None = None, db_path: Path | None = None
             ) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Sync every tracked repo. Returns ({owner/name: [events]}, {owner/name: error}).

    One repository GitHub refuses does not stop the others; its message is
    returned beside the events of the rest.
    """
    database = _db.open_db(db_path or _db._DEFAULT_DB)
    results: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    for row in database.list_repos():
        key = f"{row['owner']}/{row['name']}"
        try:
            results[key] = sync_repo(row["owner"], row["name"], token=token, db_path=db_path)
        except gh.GitHubError as exc:
            errors[key] = str(exc)
    return results, errors
