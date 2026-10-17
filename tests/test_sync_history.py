"""History is not news: many releases, and items closed before tracking (audit RR-01, RR-02).

RR-01, measured on 2026-10-17. `/releases` is a page walk with no end the
client controls: renovatebot/renovate's first sync read 100 pages and then got
"422 Only the first 10000 results are available." — the repository could never
be synced (anonymously the limit is 1,000, and electron/electron failed the
same way). And every later sync read every page again: about 70 s and 22
requests for electron with nothing changed, more than the anonymous hourly
quota at the dashboard's 15-minute interval.

So a first sync reads one page — the stored releases serve "latest release",
and history is not news — and a later sync stops at the end of the first page
that holds a tag it already has. At the end of that page, not at the tag:
GitHub orders releases by creation, and a draft created last week and
published now sits after tags the tracker knows. A 422 that says GitHub lists
no further is the end of the list, said as a warning.

RR-02, seen live on home-assistant/core: the tracker announced `pr_merged
#186812` for a PR GitHub merged seven and a half hours before the seed. The
first sync stores what is open; a later one asks for everything updated since,
and an item closed long ago that gets a comment comes back unseen —
`previous is None`, so it read as a transition. Now an item first seen closed
is an event only when its closing happened after the last sync.

GitHub is replayed from recordings (renovatebot/renovate's first page and its
422, psf/requests' issues and pulls); derived variants say what was changed.
"""
from __future__ import annotations

import sqlite3

import httpx

from releaseradar import db as _db
from releaseradar.sync import sync_repo
from tests.conftest import API, empty_page, recorded, replay

RENOVATE = "gh_renovate_releases_page1_of_many.json"
NEXT_PAGE = f"{API}/repositories/76734068/releases"     # where the recorded Link points


def track(db_path, owner, name) -> _db._DB:
    database = _db.open_db(db_path)
    database.add_repo(owner, name)
    return database


def renovate(github, releases):
    base = f"{API}/repos/renovatebot/renovate"
    github.get(f"{base}/issues").mock(return_value=empty_page())
    github.get(f"{base}/pulls").mock(return_value=empty_page())
    first = github.get(f"{base}/releases").mock(return_value=releases)
    following = github.get(url__startswith=NEXT_PAGE).mock(return_value=empty_page())
    return first, following


def page_one(**overrides) -> httpx.Response:
    return replay(RENOVATE, last_page=False) if not overrides else httpx.Response(
        200, json=overrides["body"], headers={"link": recorded(RENOVATE)["headers"]["link"]})


# ─── RR-01 ───────────────────────────────────────────────────────────────────

def test_a_first_sync_reads_one_page_of_releases(db_path, github):
    _, following = renovate(github, page_one())
    database = track(db_path, "renovatebot", "renovate")

    assert sync_repo("renovatebot", "renovate", db_path=db_path) == []

    assert following.call_count == 0
    summary = database.repo_summary("renovatebot", "renovate")
    assert summary["latest_release"]["tag_name"] == recorded(RENOVATE)["body"][0]["tag_name"]
    assert summary["last_synced_at"] is not None


def test_a_later_sync_stops_at_the_end_of_the_first_page_holding_a_known_tag(db_path, github):
    first, following = renovate(github, page_one())
    track(db_path, "renovatebot", "renovate")
    sync_repo("renovatebot", "renovate", db_path=db_path)

    assert sync_repo("renovatebot", "renovate", db_path=db_path) == []

    assert first.call_count == 2
    assert following.call_count == 0


def test_a_release_published_late_from_an_old_draft_is_still_found(db_path, github):
    """Derived: the recorded page with one release added after its first known
    tag, as a draft created earlier and published since would be listed."""
    body = recorded(RENOVATE)["body"]
    first, _ = renovate(github, page_one())
    track(db_path, "renovatebot", "renovate")
    sync_repo("renovatebot", "renovate", db_path=db_path)

    late = {**body[2], "id": 1, "tag_name": "43.0.0-late", "name": "43.0.0-late", "draft": False}
    first.mock(return_value=page_one(body=body[:1] + [late] + body[1:]))
    events = sync_repo("renovatebot", "renovate", db_path=db_path)

    assert [(e["kind"], e["tag"]) for e in events] == [("release_published", "43.0.0-late")]


def test_github_s_result_limit_ends_the_release_list_with_a_warning(db_path, github):
    """A later sync with no release stored yet walks on, and GitHub stops it
    with the 422 recorded on renovatebot/renovate's 101st page."""
    first, following = renovate(github, empty_page())
    database = track(db_path, "renovatebot", "renovate")
    sync_repo("renovatebot", "renovate", db_path=db_path)

    first.mock(return_value=page_one())
    following.mock(return_value=replay("gh_renovate_releases_page101_422.json"))
    warnings: list[str] = []
    events = sync_repo("renovatebot", "renovate", db_path=db_path, on_warning=warnings.append)

    assert len(events) == 5
    assert any("10000" in w for w in warnings), warnings
    assert database.repo_summary("renovatebot", "renovate")["latest_release"] is not None


# ─── RR-02 ───────────────────────────────────────────────────────────────────

def _requests(github, issues, pulls):
    base = f"{API}/repos/psf/requests"
    i = github.get(f"{base}/issues").mock(return_value=httpx.Response(200, json=issues))
    p = github.get(f"{base}/pulls").mock(return_value=httpx.Response(200, json=pulls))
    github.get(f"{base}/releases").mock(return_value=empty_page())
    return i, p


def _an_hour_from_now() -> str:
    from datetime import UTC, datetime, timedelta
    return (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_an_issue_closed_before_tracking_that_gets_a_comment_is_not_an_event(db_path, github):
    """Derived from the recorded open issues: the fifth comes back closed, its
    `closed_at` a year before the seed, its `updated_at` after the last sync."""
    open_five = recorded("gh_requests_issues_open_5.json")["body"]
    issues, _ = _requests(github, open_five[1:], [])
    track(db_path, "psf", "requests")
    sync_repo("psf", "requests", db_path=db_path)

    old = {**open_five[0], "state": "closed", "closed_at": "2025-10-01T00:00:00Z", "updated_at": _an_hour_from_now()}
    issues.mock(return_value=httpx.Response(200, json=[old]))

    assert sync_repo("psf", "requests", db_path=db_path) == []
    with sqlite3.connect(db_path) as connection:
        [(state,)] = connection.execute("SELECT state FROM issues WHERE number = ?", (old["number"],)).fetchall()
    assert state == "closed", "recorded, silently"


def test_a_pull_request_merged_before_tracking_is_not_an_event(db_path, github):
    open_five = recorded("gh_requests_pulls_open_5.json")["body"]
    _, pulls = _requests(github, [], open_five[1:])
    track(db_path, "psf", "requests")
    sync_repo("psf", "requests", db_path=db_path)

    old = {**open_five[0], "state": "closed", "merged_at": "2025-10-01T00:00:00Z",
           "closed_at": "2025-10-01T00:00:00Z", "updated_at": _an_hour_from_now()}
    pulls.mock(return_value=httpx.Response(200, json=[old]))

    assert sync_repo("psf", "requests", db_path=db_path) == []


def test_a_pull_request_closed_before_tracking_is_not_an_event_either(db_path, github):
    open_five = recorded("gh_requests_pulls_open_5.json")["body"]
    _, pulls = _requests(github, [], open_five[1:])
    track(db_path, "psf", "requests")
    sync_repo("psf", "requests", db_path=db_path)

    old = {**open_five[0], "state": "closed", "merged_at": None,
           "closed_at": "2025-10-01T00:00:00Z", "updated_at": _an_hour_from_now()}
    pulls.mock(return_value=httpx.Response(200, json=[old]))

    assert sync_repo("psf", "requests", db_path=db_path) == []
