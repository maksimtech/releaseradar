"""Sync a tracked repo: fetch GitHub state, update DB, emit events.

An event is a transition this tracker saw: open to closed, open to merged, a
release that was not there at the last sync. The first sync of a repository
sees no transition — it seeds the state, in one transaction, and reports
nothing; a comment or a label on an open issue moves `updated_at` and nothing
else, and is not an event either.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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


def _closed_before(previous: str | None, closed_at: str | None, since: str | None) -> bool:
    """An item first seen closed, closed before the last sync: recorded, not news.

    The first sync stores what is open; a later one asks for everything updated
    since the last, and an item closed long ago that got a comment or a label
    comes back unseen. On home-assistant/core on 2026-10-17 that announced
    `pr_merged #186812`, merged seven and a half hours before the seed.
    """
    return previous is None and closed_at is not None and since is not None and closed_at < since


Warn = Callable[[str], None]
Progress = Callable[[str], None]


class _Counter:
    """What the three walks of one repository have fetched so far, for the
    progress line. Each walk reports from its own thread; the line is built
    under a lock and handed to the caller whole."""

    def __init__(self, label: str, expected: int | None, report: Progress | None) -> None:
        self.label = label
        self.expected = expected
        self.report = report
        self.counts = {"issues": 0, "pull requests": 0, "releases": 0}
        self.pages = {"issues": 0, "pull requests": 0, "releases": 0}
        self._lock = threading.Lock()

    def on_page(self, kind: str) -> gh.OnPage:
        def update(fetched: int, pages: int) -> None:
            if self.report is None:
                return
            with self._lock:
                self.counts[kind] = fetched
                self.pages[kind] = pages
                self.report(self.line())
        return update

    def line(self) -> str:
        done = self.counts["issues"] + self.counts["pull requests"]
        requests = sum(self.pages.values())
        if self.expected:
            head = f"{self.label}: {done:,} of ~{self.expected:,} open issues and pull requests"
        else:
            head = (f"{self.label}: {self.counts['issues']:,} issues, "
                    f"{self.counts['pull requests']:,} pull requests")
        return f"{head}, {self.counts['releases']:,} releases — {requests} requests so far…"


def _pull_requests_or_none(owner: str, name: str, token: str | None, since: str | None,
                           warn: Warn | None, state: str = "all",
                           on_page: gh.OnPage | None = None) -> list[dict]:
    """The pull requests, or an empty list for a repository that has none to list.

    GitHub answers 404 to every `/pulls` query of torvalds/linux — pull requests
    are switched off there, and the search API finds none — while the repository
    itself, its (disabled) issues and its releases answer 200. A 404 on this one
    endpoint is therefore asked about once more: if the repository is there, the
    feature is not, and the sync goes on without it and says so; if the
    repository is gone, that is the error.
    """
    try:
        return gh.fetch_pull_requests(owner, name, token, since=since, state=state, on_page=on_page)
    except gh.GitHubError as exc:
        if exc.status != 404 or not gh.repo_exists(owner, name, token):
            raise
    if warn is not None:
        warn(f"{owner}/{name}: pull requests are not available on this repository "
             "(GitHub answers 404 to /pulls); issues and releases were synced.")
    return []


def sync_repo(owner: str, name: str, token: str | None = None,
              db_path: Path | None = None, on_warning: Warn | None = None,
              on_progress: Progress | None = None) -> list[dict]:
    """
    Pull what changed on GitHub since the last sync of one tracked repo, persist
    it, and return the events that change produced.

    `on_warning` receives what was synced less than fully and why; `on_progress`
    receives a one-line account of how far the download has got, after every
    page, from whichever thread fetched it. Raises `NotTracked` for a repository
    that was never `track`ed and `gh.GitHubError` for anything GitHub refused to
    answer.
    """
    database = _db.open_db(db_path or _db._DEFAULT_DB)
    repo = database.get_repo(owner, name)
    if repo is None:
        raise NotTracked(f"{owner}/{name} is not being tracked; run `releaseradar track {owner}/{name}` first.")
    repo_id = int(repo["id"])
    since = repo["last_synced_at"]
    seeding = since is None
    started = _now()

    # The first sync records what is open now, and nothing that was closed before
    # anyone watched: those are not transitions this tracker can ever report, and
    # for python/cpython they are 146 000 items against 9 700 open ones. Every
    # later sync asks for everything updated since the last one, closed included,
    # which is where the transitions are.
    state = "open" if seeding else "all"
    # Only a first sync with somebody watching pays the one extra request that
    # tells the progress line how far it is from done.
    expected = gh.open_items(owner, name, token) if seeding and on_progress is not None else None
    counter = _Counter(f"{owner}/{name}", expected, on_progress)
    if on_progress is not None:
        on_progress(counter.line())

    # The three walks are independent and each one is a chain of round trips to
    # GitHub, so they run side by side: python/cpython's first sync went from
    # 121 s to about half on 2026-10-10. Errors come back through the futures in
    # the order the walks are named, so a missing repository is reported by the
    # issues walk before the pull-request walk gets to ask about it.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="releaseradar-fetch") as pool:
        issues_future = pool.submit(gh.fetch_issues, owner, name, token, since=since, state=state,
                                    on_page=counter.on_page("issues"))
        pulls_future = pool.submit(_pull_requests_or_none, owner, name, token, since, on_warning,
                                   state=state, on_page=counter.on_page("pull requests"))
        releases_future = pool.submit(gh.fetch_releases, owner, name, token, on_page=counter.on_page("releases"),
                                      first_page_only=seeding,
                                      known_tags=frozenset() if seeding else database.release_tags(repo_id),
                                      on_warning=on_warning)
        issues = issues_future.result()
        pulls = pulls_future.result()
        releases = releases_future.result()

    new_events: list[dict] = []
    with database.transaction():
        for raw in issues:
            norm = gh.normalise_issue(raw)
            previous = database.upsert_issue(repo_id, norm)
            if seeding or previous == norm["state"] or _closed_before(previous, norm["closed_at"], since):
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
            if seeding or previous == norm["state"] or _closed_before(
                    previous, norm["merged_at"] or raw.get("closed_at"), since):
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


def sync_all(token: str | None = None, db_path: Path | None = None, on_warning: Warn | None = None,
             on_progress: Progress | None = None) -> tuple[dict[str, list[dict]], dict[str, str]]:
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
            if on_progress is not None:
                on_progress(f"{key}: asking GitHub what changed…")
            results[key] = sync_repo(row["owner"], row["name"], token=token, db_path=db_path,
                                     on_warning=on_warning, on_progress=on_progress)
        except gh.GitHubError as exc:
            errors[key] = str(exc)
    return results, errors
