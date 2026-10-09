"""ReleaseRadar MCP server — exposes tracker operations as Claude tools."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import db as _db
from . import github as gh
from .sync import sync_all, sync_repo

_server = Server("releaseradar")


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN")


def _db_path() -> Path:
    path = os.environ.get("RELEASERADAR_DB")
    return Path(path) if path else _db._DEFAULT_DB


def _ok(data: object) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}))]


# ── tool definitions ─────────────────────────────────────────────────────────

@_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="track_repo",
            description="Start tracking a GitHub repository (owner/repo format). Verifies the repo exists first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo, e.g. torvalds/linux"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="untrack_repo",
            description="Stop tracking a repository and remove all its stored data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo to stop tracking"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="list_repos",
            description="List all tracked repositories with their open issue/PR counts and latest release.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sync_repo",
            description="Fetch the latest state from GitHub for one repository and record any changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo to sync"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="sync_all",
            description="Sync every tracked repository. Returns new events grouped by repo.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="repo_status",
            description="Show open issues, open PRs and latest release for a tracked repo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "owner/repo to inspect"},
                },
                "required": ["repo"],
            },
        ),
        Tool(
            name="recent_events",
            description="Return the N most recent events from the event log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of events to return (default 20)", "default": 20},
                    "pending_only": {"type": "boolean", "description": "Return only unnotified events", "default": False},
                },
            },
        ),
    ]


# ── tool handlers ────────────────────────────────────────────────────────────

@_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    match name:
        case "track_repo":
            repo = arguments.get("repo", "")
            if "/" not in repo:
                return _err("repo must be in owner/repo format")
            owner, rname = repo.split("/", 1)
            if not gh.repo_exists(owner, rname, _token()):
                return _err(f"{owner}/{rname} not found or not accessible")
            database = _db.open_db(_db_path())
            database.add_repo(owner, rname)
            return _ok({"tracked": f"{owner}/{rname}", "message": "Now tracking. Run sync_repo to fetch state."})

        case "untrack_repo":
            repo = arguments.get("repo", "")
            if "/" not in repo:
                return _err("repo must be in owner/repo format")
            owner, rname = repo.split("/", 1)
            database = _db.open_db(_db_path())
            removed = database.remove_repo(owner, rname)
            if not removed:
                return _err(f"{owner}/{rname} was not being tracked")
            return _ok({"untracked": f"{owner}/{rname}"})

        case "list_repos":
            database = _db.open_db(_db_path())
            rows = database.list_repos()
            result = []
            for row in rows:
                summary = database.repo_summary(row["owner"], row["name"])
                result.append({
                    "repo": f"{row['owner']}/{row['name']}",
                    "added_at": row["added_at"],
                    "open_issues": summary.get("open_issues"),
                    "open_prs": summary.get("open_prs"),
                    "latest_release": summary.get("latest_release"),
                })
            return _ok(result)

        case "sync_repo":
            repo = arguments.get("repo", "")
            if "/" not in repo:
                return _err("repo must be in owner/repo format")
            owner, rname = repo.split("/", 1)
            events = sync_repo(owner, rname, token=_token(), db_path=_db_path())
            return _ok({"repo": f"{owner}/{rname}", "new_events": events})

        case "sync_all":
            results = sync_all(token=_token(), db_path=_db_path())
            total = sum(len(v) for v in results.values())
            return _ok({"results": results, "total_new_events": total})

        case "repo_status":
            repo = arguments.get("repo", "")
            if "/" not in repo:
                return _err("repo must be in owner/repo format")
            owner, rname = repo.split("/", 1)
            database = _db.open_db(_db_path())
            summary = database.repo_summary(owner, rname)
            if not summary:
                return _err(f"{owner}/{rname} is not being tracked")
            return _ok({"repo": f"{owner}/{rname}", **summary})

        case "recent_events":
            limit = int(arguments.get("limit", 20))
            pending_only = bool(arguments.get("pending_only", False))
            database = _db.open_db(_db_path())
            if pending_only:
                rows = database.pending_events()
            else:
                import sqlite3
                with _db._connect(database.path) as conn:
                    rows = conn.execute(
                        """SELECT e.*, r.owner, r.name
                           FROM events e JOIN repos r ON r.id = e.repo_id
                           ORDER BY e.seen_at DESC LIMIT ?""",
                        (limit,),
                    ).fetchall()
            events = [
                {
                    "id": r["id"],
                    "repo": f"{r['owner']}/{r['name']}",
                    "kind": r["kind"],
                    "ref_number": r["ref_number"],
                    "ref_tag": r["ref_tag"],
                    "seen_at": r["seen_at"],
                    "notified": bool(r["notified"]),
                }
                for r in rows
            ]
            return _ok(events)

        case _:
            return _err(f"Unknown tool: {name}")


# ── entry point ───────────────────────────────────────────────────────────────

async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await _server.run(read_stream, write_stream, _server.create_initialization_options())


def main() -> None:
    import asyncio
    asyncio.run(_main())


if __name__ == "__main__":
    main()
