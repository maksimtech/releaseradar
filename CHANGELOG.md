# Changelog

All notable changes to ReleaseRadar are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow the suite's [CalVer](https://calver.org/): `YYYY.count[.fix]`, where
the year is the generation shared by the five Radar and the count is this one's own.

## [Unreleased]


## [2026.1] - 2026-10-10

The first release. The scaffold of 2026-10-09 was reviewed against real GitHub
traffic before anything shipped; what follows is what that review changed.

### Added
- Track GitHub repositories from issue open → PR → release published, in a local
  SQLite database (`~/.releaseradar/state.db`).
- CLI: `track`, `untrack`, `list`, `sync`, `status`, `events`, `notify`, `mcp`.
- `notify` prints the events nobody has been told about, as a table or `--json`,
  and marks them notified; `--dry-run` prints without marking.
- MCP server (`releaseradar mcp`, or `python -m releaseradar.mcp_server`) on the
  `mcp` 2.x SDK, exposing `track_repo`, `untrack_repo`, `list_repos`,
  `sync_repo`, `sync_all`, `repo_status` and `recent_events`. The SDK is the
  `[mcp]` extra: a plain `pip install releaseradar` stays at 16 distributions
  instead of 40.
- Docker image (`maksimtech/releaseradar`, amd64/arm64) with the MCP extra
  installed, so `docker run -i … mcp` is a server.
- The suite's CI: tests on 3.11–3.14 with 3.15-dev experimental, Quality,
  CodeQL, Snyk, Trivy, Bandit, licences, mutation, SonarCloud, Docker build check
  and Scout, and the Security Posture gate over `SECURITY-EXCEPTIONS.toml`.

### Security
- `SECURITY-EXCEPTIONS.toml` records the three findings Snyk Container raised on
  the first scan, all in the `python:3.12-slim-trixie` base image and none in
  anything this image adds: zlib CVE-2026-85091 (open everywhere, Debian bug
  1146895), attr CVE-2026-54371 and acl CVE-2026-54369 (fixed in sid, scheduled
  for a trixie point release). Positions read with `patchradar debian` on
  2026-10-10, review dates attached.

### Fixed (against the unreleased scaffold)
- **The MCP server did not start.** `mcp>=1.0` installs the 2.x SDK, where the
  low-level `Server` lost its decorators and `FastMCP` became `MCPServer`; the
  module died at import and no test imported it. Rewritten on `MCPServer`, and
  tested with the real client over a real stdio subprocess.
- **Events were read off the current state, not off a transition.** A comment on
  an open issue re-emitted `issue_opened`; the first sync of a repository
  reported its whole history as fresh events (7 299 for psf/requests). Now an
  event is a transition the tracker saw, and the first sync seeds in silence.
- **Every sync downloaded the whole history.** `since` existed in the client and
  nothing passed it: the README's own example cost 105 requests and four minutes
  on every run. The last sync time is stored per repository; issues are asked
  for `since` it and pull requests are cut at the first one updated before it.
- **One SQLite connection and one commit per row** — about 24 ms each on
  Windows, so a sync spent three minutes in `fsync` with no network involved. A
  sync is now one transaction.
- GitHub errors reached the user as tracebacks. `GitHubError` carries one
  sentence: repository not found, rate limit with its reset time and the
  `GITHUB_TOKEN` hint, bad token, network down. `track` no longer reports a rate
  limit as "not found".
- `sync owner/repo` on a repository nobody tracked quietly started tracking it,
  without the existence check `track` does; it is refused now.
- Linked-issue keywords matched inside other words: `prefixes #12` linked
  issue 12. Whole words only.
- Events of one sync share a second and came back oldest-first; the log is now
  ordered by insertion.
- A draft release (`published_at: null`) crashed the sync on `NOT NULL`; drafts
  are skipped, because nothing was published.
- Version scheme aligned with the suite (`2026.1`, not `2026.43.0`); the README
  named three of the five Radar and a Claude configuration path that does not
  exist.

[Unreleased]: https://github.com/maksimtech/releaseradar/compare/v2026.1...HEAD
[2026.1]: https://github.com/maksimtech/releaseradar/releases/tag/v2026.1
