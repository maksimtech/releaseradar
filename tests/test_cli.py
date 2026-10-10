"""The CLI, end to end: real Typer runner, real SQLite, GitHub replayed from recordings."""

from __future__ import annotations

import json
import re

import httpx
from typer.testing import CliRunner

from releaseradar import db as _db
from releaseradar.cli import app
from tests.conftest import API, replay, route_repo
from tests.test_github import RATE_LIMITED

# A wide terminal: rich truncates table cells to fit COLUMNS, and "codecov-m…" is not a name.
runner = CliRunner(env={"COLUMNS": "200"})


def tracked(db_path, owner="maksimtech", name="codecov-mcp") -> _db._DB:
    database = _db.open_db(db_path)
    database.add_repo(owner, name)
    return database


class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "releaseradar" in result.output


class TestTrack:
    def test_track_invalid_format(self, db_path):
        result = runner.invoke(app, ["track", "noowner"])
        assert result.exit_code == 1
        assert "owner/repo" in result.output

    def test_track_repo_not_found(self, db_path, github):
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(return_value=replay("gh_missing_repo_404.json"))
        result = runner.invoke(app, ["track", "maksimtech/does-not-exist-xyz"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert _db.open_db(db_path).list_repos() == []

    def test_track_success(self, db_path, github):
        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        result = runner.invoke(app, ["track", "maksimtech/codecov-mcp"])
        assert result.exit_code == 0
        assert "maksimtech/codecov-mcp" in result.output
        assert [(r["owner"], r["name"]) for r in _db.open_db(db_path).list_repos()] == [("maksimtech", "codecov-mcp")]

    def test_track_under_a_rate_limit_says_so(self, db_path, github):
        github.get(f"{API}/repos/psf/requests").mock(return_value=RATE_LIMITED)
        result = runner.invoke(app, ["track", "psf/requests"])
        assert result.exit_code == 1
        assert "rate limit" in result.output
        assert "Traceback" not in result.output


class TestUntrack:
    def test_untrack_not_tracked(self, db_path):
        result = runner.invoke(app, ["untrack", "nobody/nothing"])
        assert result.exit_code == 1
        assert "not being tracked" in result.output

    def test_untrack_success(self, db_path):
        tracked(db_path)
        result = runner.invoke(app, ["untrack", "maksimtech/codecov-mcp"])
        assert result.exit_code == 0
        assert _db.open_db(db_path).list_repos() == []


class TestList:
    def test_list_empty(self, db_path):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No repositories" in result.output

    def test_list_with_repos(self, db_path):
        tracked(db_path)
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "codecov-mcp" in result.output
        assert "never" in result.output  # not synced yet


class TestStatus:
    def test_status_not_tracked(self, db_path):
        result = runner.invoke(app, ["status", "nobody/nothing"])
        assert result.exit_code == 1

    def test_status_tracked(self, db_path):
        tracked(db_path)
        result = runner.invoke(app, ["status", "maksimtech/codecov-mcp"])
        assert result.exit_code == 0
        assert "maksimtech/codecov-mcp" in result.output
        assert "no release yet" in result.output


class TestSync:
    def test_sync_nothing_to_sync(self, db_path):
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 0
        assert "Nothing to sync" in result.output

    def test_sync_single_repo_invalid(self, db_path):
        assert runner.invoke(app, ["sync", "noowner"]).exit_code == 1

    def test_sync_does_not_track_on_its_own(self, db_path, github):
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        result = runner.invoke(app, ["sync", "maksimtech/codecov-mcp"])
        assert result.exit_code == 1
        assert "not being tracked" in result.output
        assert _db.open_db(db_path).list_repos() == []

    def test_first_sync_of_a_repo_is_quiet(self, db_path, github):
        route_repo(github, "maksimtech", "cookieradar",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_cookieradar_releases.json")
        tracked(db_path, "maksimtech", "cookieradar")
        result = runner.invoke(app, ["sync", "maksimtech/cookieradar"])
        assert result.exit_code == 0
        assert "Nothing new" in result.output
        status = runner.invoke(app, ["status", "maksimtech/cookieradar"])
        assert "v2026.44" in status.output

    def test_a_missing_repository_is_an_error_line_not_a_traceback(self, db_path, github):
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        tracked(db_path, "maksimtech", "does-not-exist-xyz")
        result = runner.invoke(app, ["sync", "maksimtech/does-not-exist-xyz"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert "Traceback" not in result.output and "HTTPStatusError" not in result.output

    def test_a_rate_limit_is_explained(self, db_path, github):
        github.get(f"{API}/repos/psf/requests/issues").mock(return_value=RATE_LIMITED)
        tracked(db_path, "psf", "requests")
        result = runner.invoke(app, ["sync", "psf/requests"])
        assert result.exit_code == 1
        assert "rate limit" in result.output
        assert "GITHUB_TOKEN" in result.output

    def test_sync_all_reports_each_failure_and_keeps_going(self, db_path, github):
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        database = tracked(db_path)
        database.add_repo("maksimtech", "does-not-exist-xyz")
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 1
        assert "does-not-exist-xyz" in result.output and "not found" in result.output
        assert database.repo_summary("maksimtech", "codecov-mcp")["last_synced_at"] is not None


class TestEvents:
    def test_events_empty(self, db_path):
        result = runner.invoke(app, ["events"])
        assert result.exit_code == 0
        assert "No events" in result.output

    def test_events_are_newest_first_even_within_one_second(self, db_path):
        database = tracked(db_path, "t", "l")
        repo_id = database.get_repo_id("t", "l")
        for n in (1, 2, 3):
            database.add_event(repo_id, "issue_opened", ref_number=n)
        result = runner.invoke(app, ["events"])
        assert re.findall(r"#(\d)", result.output) == ["3", "2", "1"], result.output

    def test_events_limit_must_be_positive(self, db_path):
        assert runner.invoke(app, ["events", "-n", "0"]).exit_code != 0


class TestNotify:
    def test_nothing_pending(self, db_path):
        result = runner.invoke(app, ["notify"])
        assert result.exit_code == 0
        assert "Nothing to notify" in result.output
        assert runner.invoke(app, ["notify", "--json"]).output.strip() == "[]"

    def test_prints_pending_events_once(self, db_path):
        database = tracked(db_path, "t", "l")
        repo_id = database.get_repo_id("t", "l")
        database.add_event(repo_id, "release_published", ref_tag="v1.0.0", payload=json.dumps({"name": "First"}))

        first = runner.invoke(app, ["notify", "--json"])
        assert first.exit_code == 0
        events = json.loads(first.output)
        assert [(e["repo"], e["kind"], e["ref_tag"], e["payload"]) for e in events] == [
            ("t/l", "release_published", "v1.0.0", {"name": "First"}),
        ]

        second = runner.invoke(app, ["notify", "--json"])
        assert json.loads(second.output) == []
        assert runner.invoke(app, ["events", "--pending"]).output.strip().endswith("No events.")

    def test_dry_run_marks_nothing(self, db_path):
        database = tracked(db_path, "t", "l")
        database.add_event(database.get_repo_id("t", "l"), "issue_closed", ref_number=4)
        runner.invoke(app, ["notify", "--dry-run"])
        assert len(database.pending_events()) == 1


class TestMcpCommand:
    def test_help_lists_the_mcp_command(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "mcp" in result.output
        assert "notify" in result.output


def test_a_real_response_object_is_what_the_replay_serves():
    """The replay helper hands httpx a status, headers and a body as recorded —
    not a stand-in object — so what the CLI parses is what GitHub sent."""
    response = replay("gh_codecov-mcp_repo.json")
    assert isinstance(response, httpx.Response)
    assert response.status_code == 200
    assert response.json()["full_name"] == "maksimtech/codecov-mcp"
