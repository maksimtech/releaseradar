"""The MCP server: the real `mcp` client over a real stdio subprocess, and the
real server object in-process with GitHub replayed from recordings.

The scaffold shipped a server that died at import against the `mcp` its own
pyproject installs, and nothing here noticed because nothing imported it. The
first test is the one that would have.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from releaseradar import db as _db
from tests.conftest import API, replay, route_repo

TOOLS = {"track_repo", "untrack_repo", "list_repos", "sync_repo", "sync_all", "repo_status", "recent_events"}


def text_of(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


class TestOverStdio:
    """The server is started as a subprocess, as an MCP client starts it.

    Its stderr goes to a file of the test's own. stdio_client hands the child
    `sys.stderr` by default, and a runner that swaps sys.stderr for a stream with
    no file descriptor breaks the start: mutmut's clean-test phase does, and the
    Saturday run stopped on "I/O operation on closed file" (found in WSL on
    2026-10-17). A file has a descriptor whoever runs the suite, and keeps what
    the server printed if it fails.
    """

    def test_the_server_starts_lists_its_tools_and_answers(self, db_path: Path, tmp_path: Path):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def session() -> tuple[list[str], str, str]:
            env = {**os.environ, "RELEASERADAR_DB": str(db_path)}
            params = StdioServerParameters(command=sys.executable, args=["-m", "releaseradar.mcp_server"], env=env)
            with open(tmp_path / "server.err", "w", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as s:
                    init = await s.initialize()
                    tools = [t.name for t in (await s.list_tools()).tools]
                    repos = await s.call_tool("list_repos", {})
                    return tools, text_of(repos), init.server_info.name

        tools, repos, name = asyncio.run(asyncio.wait_for(session(), timeout=60))
        assert set(tools) == TOOLS
        assert json.loads(repos) == {"repos": []}
        assert name == "releaseradar"

    def test_the_cli_entry_point_runs_the_same_server(self, db_path: Path, tmp_path: Path):
        """`releaseradar mcp` is what `claude mcp add` and the Docker image call."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def session() -> list[str]:
            env = {**os.environ, "RELEASERADAR_DB": str(db_path)}
            params = StdioServerParameters(command=sys.executable, args=["-m", "releaseradar.cli", "mcp"], env=env)
            with open(tmp_path / "server.err", "w", encoding="utf-8") as errlog:
                async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as s:
                    await s.initialize()
                    return [t.name for t in (await s.list_tools()).tools]

        assert set(asyncio.run(asyncio.wait_for(session(), timeout=60))) == TOOLS


@pytest.fixture
def server():
    from releaseradar import mcp_server

    return mcp_server.server


def call(server, name: str, **arguments):
    return asyncio.run(server.call_tool(name, arguments))


def refused(server, name: str, **arguments) -> str:
    """The message of the ToolError a tool raises. Over the wire the SDK turns it
    into a result with `is_error`; in-process it is the exception itself."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as exc:
        call(server, name, **arguments)
    return str(exc.value)


class TestTools:
    def test_every_tool_has_a_schema_and_a_description(self, server):
        tools = asyncio.run(server.list_tools())
        assert {t.name for t in tools} == TOOLS
        for tool in tools:
            assert tool.description, tool.name
            assert tool.input_schema["type"] == "object", tool.name
        by_name = {t.name: t for t in tools}
        assert by_name["track_repo"].input_schema["required"] == ["repo"]
        assert by_name["recent_events"].input_schema["properties"]["limit"]["default"] == 20

    def test_track_rejects_a_bad_name_without_asking_github(self, server, db_path, github):
        assert "owner/repo" in refused(server, "track_repo", repo="nope")
        assert github.calls.call_count == 0

    def test_track_a_missing_repository(self, server, db_path, github):
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz").mock(return_value=replay("gh_missing_repo_404.json"))
        assert "not found" in refused(server, "track_repo", repo="maksimtech/does-not-exist-xyz")

    def test_track_sync_status_events_untrack(self, server, db_path, github):
        github.get(f"{API}/repos/maksimtech/cookieradar").mock(return_value=replay("gh_codecov-mcp_repo.json"))
        route_repo(github, "maksimtech", "cookieradar",
                   "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json", "gh_cookieradar_releases.json")

        tracked = call(server, "track_repo", repo="maksimtech/cookieradar")
        assert not tracked.is_error
        assert json.loads(text_of(tracked))["tracked"] == "maksimtech/cookieradar"

        synced = json.loads(text_of(call(server, "sync_repo", repo="maksimtech/cookieradar")))
        assert synced == {"repo": "maksimtech/cookieradar", "new_events": [], "warnings": []}

        status = json.loads(text_of(call(server, "repo_status", repo="maksimtech/cookieradar")))
        assert status["latest_release"]["tag_name"] == "v2026.44"
        assert status["last_synced_at"]

        repos = json.loads(text_of(call(server, "list_repos")))["repos"]
        assert [r["repo"] for r in repos] == ["maksimtech/cookieradar"]

        events = json.loads(text_of(call(server, "recent_events", limit=5)))
        assert events == {"events": []}

        everything = json.loads(text_of(call(server, "sync_all")))
        assert everything == {"results": {"maksimtech/cookieradar": []}, "errors": {}, "warnings": [],
                              "total_new_events": 0}

        gone = json.loads(text_of(call(server, "untrack_repo", repo="maksimtech/cookieradar")))
        assert gone == {"untracked": "maksimtech/cookieradar"}
        assert _db.open_db(db_path).list_repos() == []

    def test_sync_of_an_untracked_repository_is_an_error_not_a_side_effect(self, server, db_path, github):
        assert "not being tracked" in refused(server, "sync_repo", repo="psf/requests")
        assert _db.open_db(db_path).list_repos() == []

    def test_a_github_failure_is_a_tool_error_with_the_sentence(self, server, db_path, github):
        github.get(f"{API}/repos/maksimtech/does-not-exist-xyz/issues").mock(
            return_value=replay("gh_missing_repo_issues_404.json"))
        _db.open_db(db_path).add_repo("maksimtech", "does-not-exist-xyz")
        message = refused(server, "sync_repo", repo="maksimtech/does-not-exist-xyz")
        assert "not found" in message
        assert "Traceback" not in message

    def test_recent_events_newest_first_and_pending_only(self, server, db_path):
        database = _db.open_db(db_path)
        repo_id = database.add_repo("t", "l")
        for n in (1, 2, 3):
            database.add_event(repo_id, "issue_opened", ref_number=n)
        database.mark_notified([database.pending_events()[0]["id"]])

        recent = json.loads(text_of(call(server, "recent_events", limit=2)))["events"]
        assert [e["ref_number"] for e in recent] == [3, 2]
        pending = json.loads(text_of(call(server, "recent_events", pending_only=True)))["events"]
        assert [e["ref_number"] for e in pending] == [2, 3]
        assert all(e["notified"] is False for e in pending)

    def test_a_non_positive_limit_is_refused(self, server, db_path):
        assert "at least 1" in refused(server, "recent_events", limit=0)

    def test_status_of_an_untracked_repository(self, server, db_path):
        assert "not being tracked" in refused(server, "repo_status", repo="nobody/nothing")


class TestErrorsOverTheWire:
    def test_a_refusal_reaches_the_client_as_an_error_result(self, db_path: Path):
        """In-process a tool raises; over stdio the SDK turns that into a result with
        `is_error` and the sentence, which is what Claude reads."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def session() -> tuple[bool, str]:
            env = {**os.environ, "RELEASERADAR_DB": str(db_path)}
            params = StdioServerParameters(command=sys.executable, args=["-m", "releaseradar.mcp_server"], env=env)
            async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
                await s.initialize()
                result = await s.call_tool("repo_status", {"repo": "nobody/nothing"})
                return result.is_error, text_of(result)

        is_error, text = asyncio.run(asyncio.wait_for(session(), timeout=60))
        assert is_error is True
        assert "not being tracked" in text
