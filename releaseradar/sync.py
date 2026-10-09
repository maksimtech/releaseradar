"""Sync a tracked repo: fetch GitHub state, update DB, emit events."""

from __future__ import annotations

import json
from pathlib import Path

from . import db as _db
from . import github as gh


def sync_repo(owner: str, name: str, token: str | None = None,
              db_path: Path | None = None) -> list[dict]:
    """
    Pull the latest state from GitHub for one repo and persist changes.
    Returns a list of new events emitted during this sync.
    """
    database = _db.open_db(db_path or _db._DEFAULT_DB)
    repo_id = database.add_repo(owner, name)

    new_events: list[dict] = []

    # ── issues ───────────────────────────────────────────────────────────────
    for raw in gh.fetch_issues(owner, name, token):
        norm = gh.normalise_issue(raw)
        changed = database.upsert_issue(repo_id, norm)
        if changed:
            kind = "issue_opened" if norm["state"] == "open" else "issue_closed"
            database.add_event(
                repo_id, kind,
                ref_number=norm["number"],
                payload=json.dumps({"title": norm["title"], "url": norm["html_url"]}),
            )
            new_events.append({"kind": kind, "number": norm["number"], "title": norm["title"]})

    # ── pull requests ─────────────────────────────────────────────────────────
    for raw in gh.fetch_pull_requests(owner, name, token):
        norm = gh.normalise_pr(raw)
        changed = database.upsert_pr(repo_id, norm)
        if changed:
            kind = "pr_merged" if norm["state"] == "merged" else (
                "pr_opened" if norm["state"] == "open" else "pr_closed"
            )
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

    # ── releases ──────────────────────────────────────────────────────────────
    for raw in gh.fetch_releases(owner, name, token):
        norm = gh.normalise_release(raw)
        is_new = database.upsert_release(repo_id, norm)
        if is_new:
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

    return new_events


def sync_all(token: str | None = None, db_path: Path | None = None) -> dict[str, list[dict]]:
    """Sync every tracked repo. Returns {owner/name: [events]}."""
    database = _db.open_db(db_path or _db._DEFAULT_DB)
    results: dict[str, list[dict]] = {}
    for row in database.list_repos():
        key = f"{row['owner']}/{row['name']}"
        results[key] = sync_repo(row["owner"], row["name"], token=token, db_path=db_path)
    return results
