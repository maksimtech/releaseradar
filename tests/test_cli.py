"""The CLI, end to end: real Typer runner, real SQLite, GitHub replayed from recordings."""

from __future__ import annotations

import json
import re

import httpx
import pytest
from typer.testing import CliRunner

from releaseradar import db as _db
from releaseradar.cli import app
from tests.conftest import API, replay, route_repo
from tests.test_github import RATE_LIMITED

# A wide terminal: rich truncates table cells to fit COLUMNS, and "codecov-m…" is not a name.
runner = CliRunner(env={"COLUMNS": "200"})


def flat(output: str) -> str:
    """The output as one line, without colour: rich wraps a long message at the
    terminal's width, and on a CI runner it paints it."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", output).split())


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

    @pytest.mark.parametrize("name", ["../users", "../..", "psf/requests#x", "psf/requests?", "psf/re\x00quests"])
    def test_track_refuses_a_name_that_leaves_the_repository_path(self, db_path, github, name):
        """`track ../users` printed "Now tracking": GET /repos/../users is GET /users
        once httpx has normalised the path, and that answers 200 — with the token,
        if there is one. `psf/requests#x` lost its fragment and checked psf/requests
        (both seen live on 2026-11-17). Nothing is asked of GitHub now."""
        github.get(f"{API}/users").mock(return_value=httpx.Response(200, json=[{"login": "mojombo"}]))
        github.get(f"{API}/repos/psf/requests").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        result = runner.invoke(app, ["track", name])
        assert result.exit_code == 1
        assert "owner/repo" in result.output
        assert "Now tracking" not in result.output
        assert github.calls.call_count == 0
        assert _db.open_db(db_path).list_repos() == []

    def test_a_refused_name_is_printed_as_text_not_markup(self, db_path, github):
        """The refusal quotes the name, and rich would read `[/b]` in it as a closing tag."""
        result = runner.invoke(app, ["track", "a[/b]/c"])
        assert result.exit_code == 1
        assert "[/b]" in result.output
        assert isinstance(result.exception, SystemExit)


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

    def test_untrack_checks_the_name_like_every_other_command(self, db_path):
        """Removing by the name as typed, unchecked, was the one input path the
        name rules did not cover; a row an earlier version stored under such a
        name is what `untrack --invalid` is for, and the refusal says so."""
        tracked(db_path, "psf", "requests#x")
        result = runner.invoke(app, ["untrack", "psf/requests#x"])
        assert result.exit_code == 1
        assert "owner/repo" in result.output
        assert "releaseradar untrack --invalid" in flat(result.output)
        assert [(r["owner"], r["name"]) for r in _db.open_db(db_path).list_repos()] == [("psf", "requests#x")]

    def test_untrack_of_a_malformed_name_nobody_tracked_says_what_is_wrong(self, db_path):
        result = runner.invoke(app, ["untrack", "../.."])
        assert result.exit_code == 1
        assert "owner/repo" in result.output

    def test_untrack_needs_a_repository_or_invalid(self, db_path):
        result = runner.invoke(app, ["untrack"])
        assert result.exit_code == 1
        assert "--invalid" in result.output

    def test_invalid_and_a_repository_are_not_asked_for_together(self, db_path):
        tracked(db_path)
        result = runner.invoke(app, ["untrack", "--invalid", "maksimtech/codecov-mcp"])
        assert result.exit_code == 1
        assert "not both" in result.output
        assert len(_db.open_db(db_path).list_repos()) == 1


def planted(db_path) -> _db._DB:
    """One valid repository and every kind of stored name an earlier version
    accepted: a NUL, a newline, a fragment, `..`, and rich markup."""
    database = tracked(db_path)
    for owner, name in BAD_ROWS:
        repo_id = database.add_repo(owner, name)
        database.add_event(repo_id, "issue_opened", ref_number=1)
    return database


BAD_ROWS = [("psf", "re\x00quests"), ("psf", "re\nquests"), ("psf", "requests#x"), ("..", ".."), ("a[/]", "b")]
# As they are printed: repr, so a NUL or a newline is visible and markup is text.
BAD_SHOWN = ["'psf/re\\x00quests'", "'psf/re\\nquests'", "'psf/requests#x'", "'../..'", "'a[/]/b'"]


class TestUntrackInvalid:
    def test_every_invalid_row_goes_with_its_data_and_the_valid_one_stays(self, db_path):
        database = planted(db_path)
        result = runner.invoke(app, ["untrack", "--invalid"])
        assert result.exit_code == 0, result.output
        assert [(r["owner"], r["name"]) for r in database.list_repos()] == [("maksimtech", "codecov-mcp")]
        assert database.recent_events(50) == []
        for shown in BAD_SHOWN:
            assert shown in result.output
        assert "\x00" not in result.output
        assert "5" in result.output

    def test_nothing_invalid_is_said_and_is_not_an_error(self, db_path):
        tracked(db_path)
        result = runner.invoke(app, ["untrack", "--invalid"])
        assert result.exit_code == 0
        assert "No invalid repositories" in result.output
        assert len(_db.open_db(db_path).list_repos()) == 1


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

    def test_invalid_rows_are_shown_escaped_and_marked_with_the_way_out(self, db_path):
        planted(db_path)
        result = CliRunner(env={"COLUMNS": "300"}).invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        for shown in BAD_SHOWN:
            assert shown in result.output
        assert "\x00" not in result.output
        assert "invalid name" in result.output
        assert "releaseradar untrack --invalid" in flat(result.output)
        assert "maksimtech/codecov-mcp" in result.output


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
        github.get(f"{API}/repos/maksimtech/cookieradar").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        route_repo(github, "maksimtech", "cookieradar",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_cookieradar_releases.json")
        tracked(db_path, "maksimtech", "cookieradar")
        result = runner.invoke(app, ["sync", "maksimtech/cookieradar"])
        assert result.exit_code == 0
        assert "Nothing new" in result.output
        status = runner.invoke(app, ["status", "maksimtech/cookieradar"])
        assert "v2026.44" in status.output

    def test_a_missing_repository_is_an_error_line_not_a_traceback(self, db_path, github):
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(return_value=replay("gh_missing_repo_404.json"))
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        tracked(db_path, "maksimtech", "does-not-exist-xyz")
        result = runner.invoke(app, ["sync", "maksimtech/does-not-exist-xyz"])
        assert result.exit_code == 1
        assert "not found" in result.output
        assert "Traceback" not in result.output and "HTTPStatusError" not in result.output

    def test_a_rate_limit_is_explained(self, db_path, github):
        """The quota is gone: the repository record answers 403 too, the size of the
        open set stays unknown, and the first walk reports the limit."""
        github.get(f"{API}/repos/psf/requests").mock(return_value=RATE_LIMITED)
        github.get(f"{API}/repos/psf/requests/issues").mock(return_value=RATE_LIMITED)
        tracked(db_path, "psf", "requests")
        result = runner.invoke(app, ["sync", "psf/requests"])
        assert result.exit_code == 1
        assert "rate limit" in result.output
        assert "GITHUB_TOKEN" in result.output

    def test_sync_all_reports_each_failure_and_keeps_going(self, db_path, github):
        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(return_value=replay("gh_missing_repo_404.json"))
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

    def test_sync_all_reports_a_malformed_tracked_name_and_syncs_the_rest(self, db_path, github):
        """A row stored before names were checked is that row's error line, not a
        TypeError that stops every repository (seen live on 2026-11-17)."""
        github.get(f"{API}/repos/psf/requests").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        database = tracked(db_path)
        database.add_repo("psf", "requests#x")
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 1
        assert "psf/requests#x" in result.output and "releaseradar untrack --invalid" in flat(result.output)
        assert isinstance(result.exception, SystemExit)
        assert database.repo_summary("maksimtech", "codecov-mcp")["last_synced_at"] is not None

    def test_sync_all_prints_every_kind_of_invalid_name_as_text(self, db_path, github):
        """A NUL, a newline or `[/]` in a stored name reached the error line as it
        was: rich read `[/]` as a closing tag and stopped the output."""
        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        database = planted(db_path)
        result = CliRunner(env={"COLUMNS": "300"}).invoke(app, ["sync"])
        assert result.exit_code == 1, result.output
        assert isinstance(result.exception, SystemExit)
        for shown in BAD_SHOWN:
            assert shown in result.output
        assert "\x00" not in result.output
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
    def test_the_install_hint_survives_rich_markup(self, capsys):
        """Seen on the first PyPI install: `pip install 'releaseradar[mcp]'` came out as
        `pip install 'releaseradar'`, rich having eaten `[mcp]` as a style tag. The hint
        was escaped by hand; since #55 `_fail` escapes whatever it prints, and the hint
        is written as it reads."""
        import typer

        from releaseradar.cli import MCP_MISSING, _fail

        with pytest.raises(typer.Exit):
            _fail(MCP_MISSING)
        assert "pip install 'releaseradar[mcp]'" in flat(capsys.readouterr().out)

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


class TestSyncOutputWhenThingsFail:
    def test_nothing_new_is_not_said_when_the_only_repo_failed(self, db_path, github):
        """Seen by the first user: "Nothing new." followed by the error for the one
        repository there was. Nothing was synced, so nothing is "new" or old."""
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(return_value=replay("gh_missing_repo_404.json"))
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        tracked(db_path, "maksimtech", "does-not-exist-xyz")
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 1
        assert "Nothing new" not in result.output
        assert "not found" in result.output

    def test_a_repository_without_pull_requests_syncs_with_a_warning(self, db_path, github):
        base = f"{API}/repos/torvalds/linux"
        github.get(base).mock(return_value=replay("gh_linux_repo.json"))
        route_repo(github, "torvalds", "linux", "gh_linux_issues_all.json", "gh_linux_pulls_404.json",
                   "gh_linux_releases.json")
        tracked(db_path, "torvalds", "linux")
        result = runner.invoke(app, ["sync", "torvalds/linux"])
        assert result.exit_code == 0, result.output
        assert "pull requests" in result.output
        assert "Nothing new" in result.output


class TestGitHubTextIsNotMarkup:
    """#55: issue and PR titles and release names are written by anyone on GitHub,
    and were printed as rich markup. `[/]` raised MarkupError after the events
    were committed, `[docs]` and `array[i]` lost their brackets, and `[link=…]`
    made a hyperlink the title's author chose."""

    TITLES = ["[/] stray closing tag", "[docs] Fix typo", "array[i] out of range",
              "[link=https://example.com]x[/link]"]
    RELEASE_NAME = "[bold]1.0[/bold] [/]"

    def _second_sync(self, db_path, github):
        from tests.conftest import empty_page

        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        routes = route_repo(github, "maksimtech", "codecov-mcp", empty_page(), empty_page(), empty_page())
        when = "2026-11-27T10:00:00Z"
        issues = [{"number": number, "title": title, "state": "open", "created_at": when, "updated_at": when,
                   "closed_at": None, "html_url": f"https://github.com/maksimtech/codecov-mcp/issues/{number}"}
                  for number, title in enumerate(self.TITLES, 1)]
        release = {"tag_name": "v1.0", "name": self.RELEASE_NAME, "published_at": when, "prerelease": False,
                   "draft": False, "body": "", "html_url": "https://github.com/maksimtech/codecov-mcp/releases/v1.0"}
        routes["issues"].side_effect = [empty_page(), httpx.Response(200, json=issues)]
        routes["pulls"].side_effect = [empty_page(), empty_page()]
        routes["releases"].side_effect = [empty_page(), httpx.Response(200, json=[release])]
        tracked(db_path)
        assert runner.invoke(app, ["sync", "maksimtech/codecov-mcp"]).exit_code == 0
        return runner.invoke(app, ["sync", "maksimtech/codecov-mcp"])

    def test_titles_and_release_names_are_printed_as_written(self, db_path, github):
        result = self._second_sync(db_path, github)
        assert result.exit_code == 0, result.output
        out = flat(result.output)
        for title in self.TITLES:
            assert title in out, out
        assert self.RELEASE_NAME in out, out

    def test_a_warning_from_github_is_printed_as_written(self, capsys):
        from releaseradar.cli import _print_warnings
        _print_warnings(["o/r: releases read up to GitHub's limit — Only the first [/] results [b]are[/b] shown"])
        assert "Only the first [/] results [b]are[/b] shown" in flat(capsys.readouterr().out)

    def test_an_error_message_is_printed_as_written(self, capsys):
        import typer

        from releaseradar.cli import _fail
        with pytest.raises(typer.Exit):
            _fail("GitHub said [/] and [link=https://example.com]x[/link]")
        assert "GitHub said [/] and [link=https://example.com]x[/link]" in flat(capsys.readouterr().out)
