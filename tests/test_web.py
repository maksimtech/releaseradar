"""The local dashboard: a real server on a free port, a real browser-side client
(httpx), a real poller thread, GitHub replayed from recordings."""

from __future__ import annotations

import http.client
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
        assert httpx.post(server.url + "api/nope", headers={"Origin": server.url.rstrip("/")}).status_code == 404

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


def _started(db_path: Path, extra_hosts: tuple[str, ...] = ()) -> tuple[DashboardServer, Poller]:
    """A dashboard with a poller that is never started: `trigger()` only sets its
    wake-up flag, which says whether a request got through to it."""
    poller = Poller(interval=3600, token=None, db_path=db_path)
    srv = DashboardServer("127.0.0.1", 0, db_path, poller, extra_hosts=extra_hosts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, poller


@pytest.fixture
def guarded(db_path: Path):
    srv, poller = _started(db_path, extra_hosts=("radar.lan", "localhost:9000"))
    try:
        yield srv, poller
    finally:
        srv.shutdown()
        srv.server_close()


def _raw(srv: DashboardServer, method: str, path: str, headers: dict[str, str], host: bool = True) -> int:
    """One request with exactly these headers: httpx always adds a Host of its own."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=not host, skip_accept_encoding=True)
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


class TestHostAndOrigin:
    """Bound to 127.0.0.1, the dashboard was still reachable from any web page:
    a page whose host name resolves to 127.0.0.1 a minute after it loaded (DNS
    rebinding) read /api/state under its own Host, and a cross-site form or
    fetch got 202 from POST /api/sync, a sync with the caller's token. Seen on
    2026-11-17 with `Host: evil.example:80` and `Origin: https://evil.example`."""

    @pytest.mark.parametrize("path", ["/", "/api/state", "/nope"])
    def test_a_foreign_host_is_refused(self, guarded, path):
        srv, _ = guarded
        response = httpx.get(srv.url.rstrip("/") + path, headers={"Host": "evil.example:80"})
        assert response.status_code == 403
        assert "repos" not in response.text

    @pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "[::1]:{port}", "LOCALHOST:{port}",
                                      "radar.lan:{port}", "localhost:9000"])
    def test_the_loopback_names_and_the_configured_ones_are_served(self, guarded, host):
        srv, _ = guarded
        response = httpx.get(srv.url + "api/state", headers={"Host": host.format(port=srv.server_port)})
        assert response.status_code == 200
        assert response.json()["repos"] == []

    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:1", "localhost.:{port}", "radar.lan:9000",
                                      "127.0.0.1:{port}.evil.example", "evil.example"])
    def test_a_loopback_name_on_another_port_or_a_lookalike_is_refused(self, guarded, host):
        srv, _ = guarded
        response = httpx.get(srv.url + "api/state", headers={"Host": host.format(port=srv.server_port)})
        assert response.status_code == 403

    def test_a_request_without_a_host_is_refused(self, guarded):
        srv, _ = guarded
        assert _raw(srv, "GET", "/api/state", {}, host=False) == 403

    @pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://evil.example:{port}",
                                        "https://127.0.0.1:{port}", "http://127.0.0.1:1", "http://127.0.0.1"])
    def test_a_sync_from_another_origin_is_refused(self, guarded, origin):
        srv, poller = guarded
        response = httpx.post(srv.url + "api/sync", headers={"Origin": origin.format(port=srv.server_port)})
        assert response.status_code == 403
        assert not poller._wake.is_set()

    def test_a_sync_without_origin_or_fetch_metadata_is_refused(self, guarded):
        srv, poller = guarded
        assert httpx.post(srv.url + "api/sync").status_code == 403
        assert _raw(srv, "POST", "/api/sync", {"Host": f"127.0.0.1:{srv.server_port}",
                                                "Sec-Fetch-Site": "cross-site"}) == 403
        assert not poller._wake.is_set()

    @pytest.mark.parametrize("origin", ["http://127.0.0.1:{port}", "http://localhost:{port}",
                                        "http://radar.lan:{port}"])
    def test_a_sync_from_the_page_itself_is_accepted(self, guarded, origin):
        srv, poller = guarded
        response = httpx.post(srv.url + "api/sync", headers={"Origin": origin.format(port=srv.server_port)})
        assert response.status_code == 202
        assert poller._wake.is_set()

    def test_same_origin_fetch_metadata_stands_in_for_a_missing_origin(self, guarded):
        srv, poller = guarded
        assert _raw(srv, "POST", "/api/sync", {"Host": f"127.0.0.1:{srv.server_port}",
                                                "Sec-Fetch-Site": "same-origin"}) == 202
        assert poller._wake.is_set()

    def test_a_right_origin_does_not_excuse_a_foreign_host(self, guarded):
        srv, poller = guarded
        response = httpx.post(srv.url + "api/sync", headers={"Host": "evil.example:80",
                                                             "Origin": f"http://127.0.0.1:{srv.server_port}"})
        assert response.status_code == 403
        assert not poller._wake.is_set()

    def test_the_page_sends_the_origin_the_check_wants(self):
        """The browser adds `Origin` to every POST a page makes with fetch,
        same-origin included; the page's own button is one."""
        assert 'fetch("/api/sync", {method: "POST"})' in PAGE


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
            "invalid_name": False,
        }]
        kinds = [(e["icon"], e["kind"], e["ref"], e["title"], e["url"]) for e in snapshot["events"]]
        assert kinds == [
            ("✅", "issue_closed", "#7", "Fix the thing", "https://example/i"),
            ("🚀", "release_published", "v2026.44", "CookieRadar 2026.44", "https://example/r"),
        ]

    def test_an_invalid_stored_name_is_shown_escaped_and_marked(self, db_path):
        """The page escapes HTML; the name still has to be readable, a NUL or a
        newline included, and the way to remove it has to be on the page."""
        database = _db.open_db(db_path)
        database.add_repo("psf", "re\x00quests")
        snapshot = state(db_path, poller=None)
        assert [(r["repo"], r["invalid_name"]) for r in snapshot["repos"]] == [("'psf/re\\x00quests'", True)]
        assert "releaseradar untrack --invalid" in PAGE


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
            # As the page's button sends it: a browser adds the Origin to a POST.
            response = httpx.post(srv.url + "api/sync", headers={"Origin": srv.url.rstrip("/")})
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
        assert "--allow-host" in result.output

    def test_allow_host_reaches_the_server(self, db_path, monkeypatch):
        from typer.testing import CliRunner

        from releaseradar import web
        from releaseradar.cli import app

        seen: dict = {}
        monkeypatch.setattr(web, "serve", lambda **kwargs: seen.update(kwargs))
        result = CliRunner().invoke(app, ["web", "--no-browser", "--allow-host", "radar.lan",
                                          "--allow-host", "localhost:9000"])
        assert result.exit_code == 0, result.output
        assert seen["extra_hosts"] == ["radar.lan", "localhost:9000"]
