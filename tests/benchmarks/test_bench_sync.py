"""A whole sync, GitHub replayed, the database real: the first one that seeds
the state, the next one that finds every item changed, and the dashboard that
reads what they wrote."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import respx

from releaseradar import db as _db
from releaseradar import web
from releaseradar.sync import sync_repo
from tests.benchmarks.datasets import route_scaled_repo, scaled, scaled_releases

OWNER, NAME = "psf", "requests"

ISSUES = scaled("gh_requests_issues_open_5.json", 500)
PULLS = scaled("gh_requests_pulls_open_5.json", 300)
RELEASES = scaled_releases(34)


def later() -> str:
    """After any `last_synced_at` this run can write, so the pull-request walk
    does not cut the changed items as already seen."""
    return (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def closed(items: list[dict], merged: bool = False) -> list[dict]:
    """The same items, closed (or merged) since the last sync."""
    when = later()
    changed = []
    for item in items:
        update = {"state": "closed", "closed_at": when, "updated_at": when}
        if merged:
            update["merged_at"] = when
        changed.append({**item, **update})
    return changed


def seeded(path: Path, owner: str = OWNER, name: str = NAME) -> Path:
    """A database where `owner/name` has had its first sync."""
    _db.open_db(path).add_repo(owner, name)
    with respx.mock(assert_all_mocked=True) as mock:
        route_scaled_repo(mock, owner, name, ISSUES, PULLS, RELEASES[:17])
        sync_repo(owner, name, db_path=path)
    return path


def fresh_copy(template: Path, directory: Path):
    """A pedantic `setup` that hands each round its own copy of `template`."""
    counter = iter(range(1_000_000))

    def setup():
        target = directory / f"round-{next(counter)}.db"
        shutil.copyfile(template, target)
        return (OWNER, NAME), {"db_path": target}

    return setup


def test_first_sync_seeds_the_state(benchmark, tmp_path):
    template = tmp_path / "template.db"
    _db.open_db(template).add_repo(OWNER, NAME)

    with respx.mock(assert_all_mocked=True) as mock:
        route_scaled_repo(mock, OWNER, NAME, ISSUES, PULLS, RELEASES)

        events = benchmark.pedantic(sync_repo, setup=fresh_copy(template, tmp_path), rounds=3)

    assert events == []


def test_next_sync_records_every_transition(benchmark, tmp_path):
    template = seeded(tmp_path / "template.db")

    with respx.mock(assert_all_mocked=True) as mock:
        route_scaled_repo(mock, OWNER, NAME, closed(ISSUES), closed(PULLS, merged=True), RELEASES)

        events = benchmark.pedantic(sync_repo, setup=fresh_copy(template, tmp_path), rounds=3)

    kinds = {event["kind"] for event in events}
    assert kinds == {"issue_closed", "pr_merged", "release_published"}


def test_dashboard_state(benchmark, tmp_path):
    """What /api/state answers every five seconds: per-repository counts and the
    latest hundred events."""
    path = tmp_path / "state.db"
    for owner, name in ((OWNER, NAME), ("maksimtech", "cookieradar"), ("python", "cpython")):
        seeded(path, owner, name)
        with respx.mock(assert_all_mocked=True) as mock:
            route_scaled_repo(mock, owner, name, closed(ISSUES), closed(PULLS, merged=True), RELEASES)
            sync_repo(owner, name, db_path=path)

    result = benchmark(web.state, path, None)

    assert len(result["repos"]) == 3
    assert len(result["events"]) == 100
