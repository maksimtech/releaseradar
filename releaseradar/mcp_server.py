"""ReleaseRadar MCP server — the tracker's operations as tools for Claude.

Built on `MCPServer` from the `mcp` 2.x SDK (the class FastMCP became): each
tool is a typed function, the schema comes from the signature, and the result is
the dict the function returns. GitHub is reached from a worker thread so a slow
sync does not stall the protocol.

Run it with `releaseradar mcp`, or `python -m releaseradar.mcp_server`.
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from . import db as _db
from . import github as gh
from .sync import NotTracked, sync_all, sync_repo

server = MCPServer(
    "releaseradar",
    version=__version__,
    instructions=(
        "ReleaseRadar tracks GitHub repositories (issues, pull requests, releases) "
        "in a local SQLite database. track_repo first, then sync_repo or sync_all; "
        "repo_status, list_repos and recent_events read what was recorded."
    ),
)


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN")


def _db_path() -> Path:
    path = os.environ.get("RELEASERADAR_DB")
    return Path(path) if path else _db._DEFAULT_DB


def _split(repo: str) -> tuple[str, str]:
    owner, sep, name = repo.strip().partition("/")
    if not sep or not owner or not name or "/" in name:
        raise ToolError("repo must be in owner/repo format, e.g. torvalds/linux")
    return owner, name


def _event_dict(row) -> dict:
    return {
        "id": row["id"],
        "repo": f"{row['owner']}/{row['name']}",
        "kind": row["kind"],
        "ref_number": row["ref_number"],
        "ref_tag": row["ref_tag"],
        "seen_at": row["seen_at"],
        "notified": bool(row["notified"]),
    }


async def _in_thread(fn, *args):
    try:
        return await anyio.to_thread.run_sync(fn, *args)
    except (gh.GitHubError, NotTracked) as exc:
        raise ToolError(str(exc)) from exc


# ── tools ────────────────────────────────────────────────────────────────────

@server.tool()
async def track_repo(repo: str) -> dict:
    """Start tracking a GitHub repository (owner/repo). Verifies on GitHub that it exists first."""
    owner, name = _split(repo)

    def work() -> dict:
        if not gh.repo_exists(owner, name, _token()):
            raise gh.GitHubError(f"{owner}/{name} not found or not accessible.", 404)
        _db.open_db(_db_path()).add_repo(owner, name)
        return {"tracked": f"{owner}/{name}", "message": "Now tracking. Run sync_repo to fetch state."}

    return await _in_thread(work)


@server.tool()
async def untrack_repo(repo: str) -> dict:
    """Stop tracking a repository and remove all its stored data."""
    owner, name = _split(repo)

    def work() -> dict:
        if not _db.open_db(_db_path()).remove_repo(owner, name):
            raise NotTracked(f"{owner}/{name} was not being tracked")
        return {"untracked": f"{owner}/{name}"}

    return await _in_thread(work)


@server.tool()
async def list_repos() -> dict:
    """List all tracked repositories with open issue/PR counts, latest release and last sync time."""

    def work() -> dict:
        database = _db.open_db(_db_path())
        repos = []
        for row in database.list_repos():
            summary = database.repo_summary(row["owner"], row["name"])
            repos.append({
                "repo": f"{row['owner']}/{row['name']}",
                "added_at": row["added_at"],
                "last_synced_at": row["last_synced_at"],
                "open_issues": summary.get("open_issues"),
                "open_prs": summary.get("open_prs"),
                "latest_release": summary.get("latest_release"),
            })
        return {"repos": repos}

    return await _in_thread(work)


@server.tool(name="sync_repo")
async def sync_repo_tool(repo: str) -> dict:
    """Fetch what changed on GitHub since the last sync of one tracked repository and return the new events."""
    owner, name = _split(repo)
    events = await _in_thread(lambda: sync_repo(owner, name, token=_token(), db_path=_db_path()))
    return {"repo": f"{owner}/{name}", "new_events": events}


@server.tool(name="sync_all")
async def sync_all_tool() -> dict:
    """Sync every tracked repository. Returns new events grouped by repo, and the repos GitHub refused."""
    results, errors = await _in_thread(lambda: sync_all(token=_token(), db_path=_db_path()))
    total = sum(len(v) for v in results.values())
    return {"results": results, "errors": errors, "total_new_events": total}


@server.tool()
async def repo_status(repo: str) -> dict:
    """Open issues, open PRs, latest release and last sync time of a tracked repo."""
    owner, name = _split(repo)

    def work() -> dict:
        summary = _db.open_db(_db_path()).repo_summary(owner, name)
        if not summary:
            raise NotTracked(f"{owner}/{name} is not being tracked")
        return {"repo": f"{owner}/{name}", **summary}

    return await _in_thread(work)


@server.tool()
async def recent_events(limit: int = 20, pending_only: bool = False) -> dict:
    """The most recent events (newest first), or only the ones not yet notified."""
    if limit < 1:
        raise ToolError("limit must be at least 1")

    def work() -> dict:
        database = _db.open_db(_db_path())
        rows = database.pending_events() if pending_only else database.recent_events(limit)
        return {"events": [_event_dict(r) for r in rows]}

    return await _in_thread(work)



# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
