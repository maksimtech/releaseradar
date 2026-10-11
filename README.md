# ReleaseRadar

**Track GitHub repo lifecycles — from issue open to release published.**

ReleaseRadar polls GitHub's REST API for the repositories you care about and
records every transition it sees — issue closed, PR merged, release published —
in a local SQLite database. Run it on a schedule, read it from the CLI, pipe
its new events wherever they should go, or call it as an MCP server from Claude.

Part of the Radar suite: [MailRadar](https://github.com/maksimtech/mailradar) ·
[PatchRadar](https://github.com/maksimtech/patchradar) ·
[ApkRadar](https://github.com/maksimtech/apkradar) ·
[CookieRadar](https://github.com/maksimtech/cookieradar) ·
[ExeRadar](https://github.com/maksimtech/exeradar) · **ReleaseRadar**

---

## Installation

```bash
pip install releaseradar          # the CLI
pip install "releaseradar[mcp]"   # the CLI and the MCP server
```

Or with Docker, where the MCP server is always included:

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

# Fetch the current state. The first sync of a repo seeds the database and
# reports nothing: history is not news. From then on, only transitions are.
releaseradar sync

# See what happened
releaseradar events --limit 30

# Check a single repo
releaseradar status torvalds/linux

# List all tracked repos with open counts, latest release and last sync
releaseradar list

# Print the events nobody was told about yet, then mark them notified
releaseradar notify --json | your-delivery-script
```

## Authentication and cost

Public repos work without a token. GitHub allows **60 requests an hour** to an
anonymous client and **5 000** to a token, and a sync walks pages of 100. The
first sync of a repository asks only for what is open now (for `psf/requests`
a handful of requests; for `python/cpython`, with 9 700 open issues and pull
requests, about a hundred, which does not fit in the anonymous quota); later
syncs ask only for what changed since the last one, closed included.

```bash
export GITHUB_TOKEN=ghp_your_token_here
```

When the quota runs out, `sync` says so, says when it resets, and exits 1;
nothing is written for a repository whose sync was refused.

## Dashboard

```bash
releaseradar web                 # http://127.0.0.1:8765/, syncs every 15 minutes
releaseradar web --every 5       # every 5 minutes
releaseradar web --port 9000 --no-browser
```

One local page: the tracked repositories with their open counts and latest
release, the feed of what changed (new entries highlighted as they arrive), the
last and next sync, a *Sync now* button, and the poller's warnings and errors.
It runs the same `sync` as the CLI, on a timer, with the same database and
the same `GITHUB_TOKEN`. Standard library only, bound to 127.0.0.1, no login:
it is for the machine it runs on.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | *(none)* | GitHub personal access token |
| `RELEASERADAR_DB` | `~/.releaseradar/state.db` | SQLite database path |

## MCP server

ReleaseRadar ships an [MCP](https://modelcontextprotocol.io/) server so Claude
can query and update the tracker directly. It runs over stdio, opens no port,
and uses the same database and the same `GITHUB_TOKEN` as the CLI.

With Claude Code, one command registers it:

```bash
pip install "releaseradar[mcp]"
claude mcp add --scope user releaseradar -- releaseradar mcp
```

Or, checked into a project for everyone who opens it, `.mcp.json`:

```json
{
  "mcpServers": {
    "releaseradar": {
      "command": "releaseradar",
      "args": ["mcp"],
      "env": { "GITHUB_TOKEN": "ghp_…" }
    }
  }
}
```

From the Docker image, the same server with `-i`:

```json
{
  "mcpServers": {
    "releaseradar": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "-v", "/home/you/.releaseradar:/home/releaseradar/.releaseradar",
               "-e", "GITHUB_TOKEN",
               "maksimtech/releaseradar", "mcp"]
    }
  }
}
```

Tools exposed: `track_repo`, `untrack_repo`, `list_repos`, `sync_repo`,
`sync_all`, `repo_status`, `recent_events`. A repository GitHub refuses comes
back as a tool error with the reason — not found, rate limit with its reset
time, bad token — never as a traceback.

## CLI reference

```
releaseradar track <owner/repo>     Start tracking a repository
releaseradar untrack <owner/repo>   Stop tracking and remove all its data
releaseradar list                   List tracked repos with summary
releaseradar sync [owner/repo]      Fetch what changed since the last sync
releaseradar status <owner/repo>    Open issues/PRs, latest release, last sync
releaseradar events [--limit N]     Show the event log, newest first
releaseradar events --pending       Show unnotified events only
releaseradar notify [--json]        Print unnotified events, then mark them notified
releaseradar notify --dry-run       Print without marking
releaseradar web [--every N]        Local dashboard, syncing every N minutes (default 15)
releaseradar mcp                    Run the MCP server on stdio
```

## What counts as an event

| Kind | When |
|---|---|
| `issue_opened` | an issue the tracker had not seen, open |
| `issue_closed` | an issue it had seen open is now closed |
| `pr_opened` | a pull request it had not seen, open |
| `pr_merged` / `pr_closed` | a pull request it had seen open is now merged / closed |
| `release_published` | a release it had not seen (drafts are not releases) |

A comment, a label or an edit changes nothing above and produces no event.
The first sync of a repository seeds its state and produces none either.

## Security

Report a vulnerability as described in [SECURITY.md](SECURITY.md), which also gives
the response times, how customers are told and how to roll back.

Every GitHub release from 2026.5 on carries CycloneDX SBOMs of the Docker image and
of the Python environment, and dated Trivy reports of the image and of the repository
at the tag. They are an automated scan for known vulnerabilities, not a penetration
test.

For customers subject to NIS2, [docs/nis2-compliance.md](docs/nis2-compliance.md)
(in Italian) answers the seven questions a supplier of software gets asked: what the
software is made of, what it sends to api.github.com and what it keeps on disk, who
can reach the dashboard and the MCP server, how it is checked before release, and
what it does not have.

## License

MIT — see [LICENSE](LICENSE).
