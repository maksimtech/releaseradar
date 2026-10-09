# ReleaseRadar

**Track GitHub repo lifecycles — from issue open to release published.**

ReleaseRadar polls GitHub's REST API for the repos you care about and records
every state change (issue opened/closed, PR opened/merged/closed, release
published) in a local SQLite database. Run it on a schedule, query it from the
CLI, or call it as an MCP tool from inside Claude.

Part of the Radar suite: [MailRadar](https://github.com/maksimtech/mailradar) ·
[PatchRadar](https://github.com/maksimtech/patchradar) ·
[ApkRadar](https://github.com/maksimtech/apkradar) · **ReleaseRadar**

---

## Installation

```bash
pip install releaseradar
```

Or with Docker:

```bash
docker run --rm -v ~/.releaseradar:/home/releaseradar/.releaseradar \
  -e GITHUB_TOKEN=ghp_… \
  maksimtech/releaseradar track torvalds/linux
```

## Quick start

```bash
# Start tracking a repo (verifies it exists on GitHub first)
releaseradar track torvalds/linux
releaseradar track psf/requests

# Fetch the latest state from GitHub
releaseradar sync

# See what's new
releaseradar events --limit 30

# Check a single repo
releaseradar status torvalds/linux

# List all tracked repos with open counts and latest release
releaseradar list
```

## Authentication

Public repos work without a token. For private repos or to raise the API rate
limit (5 000 req/h authenticated vs 60 unauthenticated):

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | *(none)* | GitHub personal access token |
| `RELEASERADAR_DB` | `~/.releaseradar/state.db` | SQLite database path |

## MCP server

ReleaseRadar ships an [MCP](https://modelcontextprotocol.io/) server so Claude
can query and update the tracker directly.

```bash
# In your MCP client config (e.g. ~/.config/claude/mcp.json):
{
  "releaseradar": {
    "command": "python",
    "args": ["-m", "releaseradar.mcp_server"],
    "env": { "GITHUB_TOKEN": "ghp_…" }
  }
}
```

Tools exposed: `track_repo`, `untrack_repo`, `list_repos`, `sync_repo`,
`sync_all`, `repo_status`, `recent_events`.

## CLI reference

```
releaseradar track <owner/repo>     Start tracking a repository
releaseradar untrack <owner/repo>   Stop tracking and remove all data
releaseradar list                   List tracked repos with summary
releaseradar sync [owner/repo]      Fetch latest state from GitHub
releaseradar status <owner/repo>    Show open issues/PRs and latest release
releaseradar events [--limit N]     Show event log
releaseradar events --pending       Show unnotified events only
```

## License

MIT — see [LICENSE](LICENSE).
