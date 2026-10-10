"""The local dashboard: a real server on a free port, a real browser-side client
(httpx), a real poller thread, GitHub replayed from recordings."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest

from releaseradar import db as _db
from releaseradar.web import PAGE, DashboardServer, Poller, state
from tests.conftest import API, replay, route_repo


@pytest.fixture
def server(db_path: Path):
    srv = DashboardServer("127.0.0.1", 0, db_path, poller=None)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


class TestPage:
    def test_the_page_is_served_at_the_root(self, server):
        response = httpx.get(server.url)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<title>ReleaseRadar</title>" in response.text
        assert response.text == PAGE

    def test_the_state_is_json_and_empty_for_an_empty_database(self, server):
        body = httpx.get(server.url + "api/state").json()
        assert body["repos"] == []
        assert body["events"] == []
        assert body["poller"] is None
        assert body["version"]

    def test_anything_else_is_a_404(self, server):
        assert httpx.get(server.url + "nope").status_code == 404
        assert httpx.post(server.url + "api/nope").status_code == 404

    def test_the_server_binds_to_loopback_only(self, server):
        assert server.server_address[0] == "127.0.0.1"
        assert server.url.startswith("http://127.0.0.1:") and server.server_port != 0

    def test_the_command_offers_no_host_option(self):
        """No `--host`: the page has no login, so the only address it may serve on
        is the loopback one, and that is not a choice offered to the caller."""
        import inspect

        from releaseradar.web import LOOPBACK, serve

        assert LOOPBACK == "127.0.0.1"
        assert "host" not in inspect.signature(serve).parameters

    def test_responses_are_not_cached(self, server):
        assert httpx.get(server.url + "api/state").headers["cache-control"] == "no-store"


class TestState:
    def test_repos_and_events_as_the_page_wants_them(self, db_path):
        database = _db.open_db(db_path)
        repo_id = database.add_repo("maksimtech", "cookieradar")
        database.upsert_release(repo_id, {"tag_name": "v2026.44", "name": "CookieRadar 2026.44",
                                          "html_url": "https://github.com/maksimtech/cookieradar/releases/tag/v2026.44",
                                          "published_at": "2026-10-10T10:00:00Z", "prerelease": 0, "body": None})
        database.add_event(repo_id, "release_published", ref_tag="v2026.44",
                           payload=json.dumps({"name": "CookieRadar 2026.44", "url": "https://example/r"}))
        database.add_event(repo_id, "issue_closed", ref_number=7,
                           payload=json.dumps({"title": "Fix the thing", "url": "https://example/i"}))

        snapshot = state(db_path, poller=None)

        assert snapshot["repos"] == [{
            "repo": "maksimtech/cookieradar", "open_issues": 0, "open_prs": 0,
            "latest_release": "v2026.44", "latest_release_at": "2026-10-10T10:00:00Z", "last_synced_at": None,
        }]
        kinds = [(e["icon"], e["kind"], e["ref"], e["title"], e["url"]) for e in snapshot["events"]]
        assert kinds == [
            ("✅", "issue_closed", "#7", "Fix the thing", "https://example/i"),
            ("🚀", "release_published", "v2026.44", "CookieRadar 2026.44", "https://example/r"),
        ]


class TestPoller:
    def test_one_run_syncs_every_tracked_repository_and_keeps_the_outcome(self, db_path, github):
        route_repo(github, "maksimtech", "cookieradar",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_cookieradar_releases.json")
        github.get(f"{API}/repos/maksimtech/cookieradar").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(return_value=replay("gh_missing_repo_404.json"))
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        database = _db.open_db(db_path)
        database.add_repo("maksimtech", "cookieradar")
        database.add_repo("maksimtech", "does-not-exist-xyz")
        poller = Poller(interval=3600, token=None, db_path=db_path)

        poller.sync_once()

        snap = poller.snapshot()
        assert snap["runs"] == 1 and snap["running"] is False
        assert snap["new_events_last_run"] == 0
        assert list(snap["errors"]) == ["maksimtech/does-not-exist-xyz"]
        assert "not found" in snap["errors"]["maksimtech/does-not-exist-xyz"]
        assert snap["last_finished"] and snap["next_at"] > snap["last_finished"]
        assert database.repo_summary("maksimtech", "cookieradar")["latest_release"]["tag_name"] == "v2026.44"

    def test_a_crash_inside_a_run_is_reported_not_swallowed(self, db_path, monkeypatch):
        """The database path is a directory: open_db cannot create a file there, so
        sync_all raises before reaching GitHub. The poller must report that, not
        die silently and leave the page saying "syncing…" for ever."""
        poller = Poller(interval=3600, token=None, db_path=db_path.parent)
        poller.sync_once()
        snap = poller.snapshot()
        assert snap["running"] is False
        assert list(snap["errors"]) == ["*"]
        assert "Error" in snap["errors"]["*"] or "error" in snap["errors"]["*"]

    def test_trigger_wakes_a_sleeping_poller(self, db_path, github):
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        _db.open_db(db_path).add_repo("maksimtech", "codecov-mcp")
        poller = Poller(interval=3600, token=None, db_path=db_path)
        poller.start()
        try:
            deadline = time.time() + 10
            while poller.snapshot()["runs"] < 1 and time.time() < deadline:
                time.sleep(0.05)
            assert poller.snapshot()["runs"] == 1, "the first run happens on start"
            poller.trigger()
            while poller.snapshot()["runs"] < 2 and time.time() < deadline:
                time.sleep(0.05)
            assert poller.snapshot()["runs"] == 2, "trigger() did not wake the poller"
        finally:
            poller.stop()
            poller.join(timeout=5)
        assert not poller.is_alive()

    def test_the_sync_endpoint_triggers_the_poller(self, db_path, github):
        # The replay router sees every httpx request in the process, the one to
        # the local dashboard included: that one goes through to the real server.
        github.route(host="127.0.0.1").pass_through()
        route_repo(github, "maksimtech", "codecov-mcp",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_codecov-mcp_releases.json")
        github.get(f"{API}/repos/maksimtech/codecov-mcp").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        _db.open_db(db_path).add_repo("maksimtech", "codecov-mcp")
        poller = Poller(interval=3600, token=None, db_path=db_path)
        srv = DashboardServer("127.0.0.1", 0, db_path, poller)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        poller.start()
        try:
            deadline = time.time() + 10
            while poller.snapshot()["runs"] < 1 and time.time() < deadline:
                time.sleep(0.05)
            response = httpx.post(srv.url + "api/sync")
            assert response.status_code == 202
            while poller.snapshot()["runs"] < 2 and time.time() < deadline:
                time.sleep(0.05)
            assert poller.snapshot()["runs"] == 2
            body = httpx.get(srv.url + "api/state").json()
            assert body["poller"]["runs"] == 2
            assert body["repos"][0]["repo"] == "maksimtech/codecov-mcp"
            assert body["repos"][0]["last_synced_at"]
        finally:
            poller.stop()
            srv.shutdown()
            srv.server_close()


class TestCommand:
    def test_help_lists_web(self):
        from typer.testing import CliRunner

        from releaseradar.cli import app

        # No colour: on a CI runner rich paints the help and splits "--every"
        # with escape codes, which is not what a reader of --help sees.
        result = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}).invoke(app, ["web", "--help"])
        assert result.exit_code == 0
        assert "--every" in result.output and "--port" in result.output
