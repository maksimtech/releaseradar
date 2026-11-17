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
from .sync import UNTRACK_INVALID, NotTracked, name_is_valid, shown_repo, sync_all, sync_repo

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


def _split(repo: str, hint: str = "") -> tuple[str, str]:
    """owner/repo by GitHub's rules, or a ToolError saying what is wrong with it.

    A name the SDK sees fail any other way reaches the client as a bare "Error
    executing tool": a NUL in it did, from deep inside httpx (2026-11-17).
    """
    try:
        return gh.split_repo(repo)
    except gh.InvalidRepoName as exc:
        raise ToolError(f"{exc} {hint}".rstrip()) from exc


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
    # Checked like every other name. A row an earlier version stored under a name
    # GitHub could not have given is removed with the CLI's `untrack --invalid`,
    # which a person runs and reads, not by a tool deleting rows in bulk.
    owner, name = _split(repo, hint=f"A name an earlier version stored like this is removed with "
                                    f"`{UNTRACK_INVALID}` on the command line.")

    def work() -> dict:
        if not _db.open_db(_db_path()).remove_repo(owner, name):
            raise NotTracked(f"{owner}/{name} was not being tracked")
        return {"untracked": f"{owner}/{name}"}

    return await _in_thread(work)


@server.tool()
async def list_repos() -> dict:
    """List all tracked repositories with open issue/PR counts, latest release and last sync time.
    A name GitHub could not have given, stored by an earlier version, is shown as its repr and marked."""

    def work() -> dict:
        database = _db.open_db(_db_path())
        repos = []
        for row in database.list_repos():
            summary = database.repo_summary(row["owner"], row["name"])
            repos.append({
                "repo": shown_repo(row["owner"], row["name"]),
                "invalid_name": not name_is_valid(row["owner"], row["name"]),
                "added_at": row["added_at"],
                "last_synced_at": row["last_synced_at"],
                "open_issues": summary.get("open_issues"),
                "open_prs": summary.get("open_prs"),
                "latest_release": summary.get("latest_release"),
            })
        if any(r["invalid_name"] for r in repos):
            return {"repos": repos, "hint": (
                "Repositories marked invalid_name were stored by an earlier version under names GitHub could "
                f"not have given; they cannot be synced. Remove them with `{UNTRACK_INVALID}` on the command line.")}
        return {"repos": repos}

    return await _in_thread(work)


@server.tool(name="sync_repo")
async def sync_repo_tool(repo: str) -> dict:
    """Fetch what changed on GitHub since the last sync of one tracked repository and return the new events."""
    owner, name = _split(repo)
    warnings: list[str] = []
    events = await _in_thread(lambda: sync_repo(owner, name, token=_token(), db_path=_db_path(),
                                                on_warning=warnings.append))
    return {"repo": f"{owner}/{name}", "new_events": events, "warnings": warnings}


@server.tool(name="sync_all")
async def sync_all_tool() -> dict:
    """Sync every tracked repository. Returns new events grouped by repo, the repos GitHub refused, and warnings."""
    warnings: list[str] = []
    results, errors = await _in_thread(lambda: sync_all(token=_token(), db_path=_db_path(),
                                                        on_warning=warnings.append))
    total = sum(len(v) for v in results.values())
    return {"results": results, "errors": errors, "warnings": warnings, "total_new_events": total}


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
