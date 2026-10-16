"""A repository that changed owner or name: GitHub answers its old name with 301.

GitHub keeps the old `owner/name` working after a transfer or a rename, through a
301 to `/repositories/<id>/...`, and the body of that answer says what to do:
"follow-redirects" (docs.github.com, best practices for the REST API). httpx does
not follow redirects unless told to, and every request here asked for JSON and
read what came back, so the 301's own body — a dict of `message`, `url` and
`documentation_url` — was walked as a page of issues: its keys came back as three
"items", and the first one normalised raised TypeError. `repo_exists` refused
the repository outright with "GitHub answered 301".

The 301s are recorded from api.github.com for joyent/node, transferred long ago
and now nodejs/node-v0.x-archive (repository 211666), together with what GitHub
serves at the address it redirects to. That archive has no open issue, pull
request or release, so the one test that needs items there pairs the recorded
301 with the recorded releases of cookieradar, and says so.
"""

from __future__ import annotations

import httpx
import pytest

from releaseradar import github as gh
from releaseradar.sync import sync_repo
from tests.conftest import API, recorded, replay
from tests.test_sync import track

OLD = f"{API}/repos/joyent/node"
NEW = f"{API}/repositories/211666"


def moved_to(location: str) -> httpx.Response:
    """The recorded 301, its Location replaced — derived: GitHub was not seen
    redirecting anywhere but to itself, and these are the cases it must not."""
    doc = recorded("gh_moved_repo_301.json")
    return httpx.Response(301, json=doc["body"], headers={**doc["headers"], "location": location})


def test_the_recordings_are_a_redirect_to_the_repository_s_new_address():
    moved = recorded("gh_moved_repo_301.json")
    assert moved["status"] == 301
    assert moved["headers"]["location"] == NEW
    assert recorded("gh_moved_repo.json")["body"]["full_name"] == "nodejs/node-v0.x-archive"


def test_releases_under_the_old_name_come_from_the_new_address(github):
    """The redirect target is cookieradar's recorded release page — derived: the
    archive the 301 points to has no release to show."""
    github.get(f"{OLD}/releases").mock(return_value=replay("gh_moved_repo_releases_301.json"))
    github.get(f"{NEW}/releases").mock(return_value=replay("gh_cookieradar_releases.json"))
    releases = gh.fetch_releases("joyent", "node")
    assert len(releases) == 17
    assert releases[0]["tag_name"] == "v2026.44"


def test_the_body_of_a_301_is_never_read_as_a_page(github):
    """What GitHub serves at the new address is empty: so is the answer, and the
    301's `message`, `url` and `documentation_url` are not three releases."""
    github.get(f"{OLD}/releases").mock(return_value=replay("gh_moved_repo_releases_301.json"))
    github.get(f"{NEW}/releases").mock(return_value=replay("gh_moved_repo_releases.json"))
    assert gh.fetch_releases("joyent", "node") == []


def test_issues_and_pull_requests_follow_the_redirect_too(github):
    for kind in ("issues", "pulls"):
        github.get(f"{OLD}/{kind}").mock(return_value=replay(f"gh_moved_repo_{kind}_301.json"))
        github.get(f"{NEW}/{kind}").mock(return_value=replay(f"gh_moved_repo_{kind}.json"))
    assert gh.fetch_issues("joyent", "node", state="open") == []
    assert gh.fetch_pull_requests("joyent", "node", state="open") == []


def test_a_moved_repository_still_exists(github):
    github.get(OLD).mock(return_value=replay("gh_moved_repo_301.json"))
    github.get(NEW).mock(return_value=replay("gh_moved_repo.json"))
    assert gh.repo_exists("joyent", "node") is True


def test_the_size_of_a_first_sync_is_read_at_the_new_address(github):
    github.get(OLD).mock(return_value=replay("gh_moved_repo_301.json"))
    github.get(NEW).mock(return_value=replay("gh_moved_repo.json"))
    assert gh.open_items("joyent", "node") == 0


def test_a_tracked_repository_that_moved_keeps_syncing(db_path, github):
    """The case a transfer of one's own repositories creates: tracked under the old
    owner, synced after the move. The releases at the new address are cookieradar's,
    derived as above."""
    for kind in ("issues", "pulls", "releases"):
        github.get(f"{OLD}/{kind}").mock(return_value=replay(f"gh_moved_repo_{kind}_301.json"))
    github.get(f"{NEW}/issues").mock(return_value=replay("gh_moved_repo_issues.json"))
    github.get(f"{NEW}/pulls").mock(return_value=replay("gh_moved_repo_pulls.json"))
    github.get(f"{NEW}/releases").mock(return_value=replay("gh_cookieradar_releases.json"))
    database = track(db_path, "joyent", "node")

    assert sync_repo("joyent", "node", db_path=db_path) == []
    assert database.repo_summary("joyent", "node")["latest_release"]["tag_name"] == "v2026.44"


# --- the token goes to api.github.com and nowhere else -------------------------------


@pytest.mark.parametrize("location", [
    "https://example.com/repositories/211666/releases",
    "http://api.github.com/repositories/211666/releases",
    "https://api.github.com.example.com/repositories/211666/releases",
])
def test_a_redirect_off_api_github_com_is_refused_and_not_requested(github, location):
    github.get(f"{OLD}/releases").mock(return_value=moved_to(location))
    elsewhere = github.get(location).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(gh.GitHubError, match="redirected .* not followed"):
        gh.fetch_releases("joyent", "node", token="ghp_must_stay_with_github")
    assert not elsewhere.called


def test_a_repository_redirected_off_github_is_not_reported_as_existing(github):
    github.get(OLD).mock(return_value=moved_to("https://example.com/repositories/211666"))
    with pytest.raises(gh.GitHubError, match="not followed"):
        gh.repo_exists("joyent", "node")


def test_the_size_of_a_repository_redirected_off_github_is_unknown(github):
    """open_items answers None for what GitHub will not describe, and a refused
    redirect is one of those: the sync itself then says why."""
    github.get(OLD).mock(return_value=moved_to("https://example.com/repositories/211666"))
    assert gh.open_items("joyent", "node") is None


def test_a_redirect_that_never_ends_is_given_up(github):
    github.get(f"{OLD}/releases").mock(return_value=moved_to(f"{OLD}/releases"))
    with pytest.raises(gh.GitHubError, match="too many redirects"):
        gh.fetch_releases("joyent", "node")
