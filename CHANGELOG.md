# Changelog

All notable changes to ReleaseRadar are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow the suite's [CalVer](https://calver.org/): `YYYY.count[.fix]`, where
the year is the generation shared by the five Radar and the count is this one's own.

## [Unreleased]

## [2026.7] - 2026-10-23

### Security

- **certifi 2026.10.19** in `uv.lock`, from 2026.7.22. The older bundle trusts emSign root CAs whose
  intermediate CA keys were compromised (GHSA-6q48-6c7w-9gw2, critical, published 2026-10-20);
  2026.10.19 is the release the advisory names as fixed. The Security Posture gate stopped every pull
  request on it.

## [2026.6] - 2026-10-17

### Added

- **`docs/nis2-compliance.md`: the answers to the seven questions a customer subject
  to NIS2 asks a software supplier, with only what the repository can back.** Written
  in Italian, for customers under d.lgs. 138/2024, after the same document in
  mailradar and exeradar. Where the answer is "no" it says no: no penetration test,
  no second reviewer, no access control, no audit log, no export command. It says
  what ReleaseRadar sends to its one host, api.github.com (the `owner/repo` names and
  the `GITHUB_TOKEN`, in the `Authorization` header), which commands contact it and
  which only touch the database, what the five tables of the SQLite database hold,
  that the dashboard listens on 127.0.0.1 with no login and checks neither `Host` nor
  `Origin`, and that the MCP server speaks stdio to whoever started it.
  `tests/test_nis2_evidence.py` holds each of those to the code — a sync with a token,
  replayed from recordings, leaves the token out of the database — and refuses
  "NIS2-ready", "no data sent to third parties" and similar claims in the document,
  in `SECURITY.md` and in the README.
- **Every release carries SBOMs and dated scans** (`.github/workflows/sbom.yml`):
  CycloneDX 1.5 of the image, Debian packages included (Syft), and of the Python
  environment with the `[mcp]` extra the image installs (cyclonedx-py), plus Trivy
  reports of the image and of the repository at the tag, and a note giving the commit
  and Docker Hub's digests and saying that a CVE scan is not a penetration test. It
  runs after the image of the tag has been pushed, and on demand for a release that
  is already out.
- **`uv.lock`**, which ReleaseRadar did not have, and a Quality step that fails when
  it falls behind `pyproject.toml`.
- **`SECURITY.md` says how vulnerabilities are found, how fast they are answered, how
  customers are told and how to roll back.** A fix ships with the next release; for
  CVSS 9.0 or more, customers who gave a security contact hear within 48 hours.

### Fixed

- **A repository that changed owner or name broke its sync.** GitHub answers the old
  `owner/name` with 301 to `/repositories/<id>/...`, and the client followed no
  redirect, so the 301's body was walked as a page: its keys `message`, `url` and
  `documentation_url` came back as three items, and the sync stopped on a TypeError;
  `track` refused the repository with "GitHub answered 301". Redirects are now
  followed within `https://api.github.com`, five at most, and refused unsent
  anywhere else, so the token still goes to GitHub only. Recorded on 2026-10-16 from
  joyent/node, now nodejs/node-v0.x-archive. It mattered before moving the Radar to
  the maksimtech-com organisation, which would have turned every repository tracked
  under maksimtech into one of these.
- **The licence gate had never refused anything.** `pip-licenses
  --fail-on="GPL;AGPL;LGPL"` compares whole licence strings, and no package declares
  just "GPL": measured in mailradar on 2026-10-11, exit 0 without `--partial-match`
  and exit 1 with it, with an LGPL package installed. The flag is there now;
  ReleaseRadar's base install passes it, and so did an install with the `[mcp]`
  extra, checked by hand the same day.
- **A repository with many releases could not be synced, and every sync re-read them all.**
  renovatebot/renovate's first sync read 100 pages of releases and failed on GitHub's "422 Only the
  first 10000 results are available" (1,000 without a token: electron/electron failed too); later
  syncs read every page again, ~70 s and 22 requests for electron with nothing changed. A first
  sync now reads one page — the stored releases serve "latest release" — and a later one stops
  after the first page holding a known tag (after the page, so a draft published late is still
  found). Reaching GitHub's limit ends the list with a warning.
- **Items closed before tracking produced false events.** A later sync asks for everything updated
  since the last; an item closed long ago that gets a comment came back unseen and read as a
  transition — home-assistant/core announced `pr_merged #186812`, merged 7½ hours before the
  seed. An item first seen closed is now an event only if it closed after the last sync.
- **On a pull request, Security Posture called live exceptions unused.** CodeQL reports only what
  the change introduces on a pull request ref, so when it reported anything, an exception for a
  finding still open on main in an untouched file matched nothing and failed the gate
  (cookieradar #37, 2026-10-12). main's open alerts now count as history the record may match,
  like the closed ones; blocking is still decided on the pull request's own alerts. Simulated on
  #37's real alerts: the false "unused" goes, the real new alert stays.
- **Security Posture judged every pull request by main's alerts.** It listed the alerts without
  a `ref`, so GitHub answered for main, and its wait compared the scanners' runs with the merge
  commit, which none of them carries: on mailradar #39 it waited two seconds. It now waits for
  the runs of the branch head, Bandit included, and reads the open alerts of `refs/pull/N/merge`;
  the closed ones come from that ref and from main, so an exception main holds as settled is not
  called unused on a ref with no history. Checked against a recent pull request of each Radar
  with the real tool: the same verdict as main in all six.
- **The Saturday mutation run would have died on the NIS2 tests.** mutmut copies only the
  paths in `also_copy` into `mutants/`, and `tests/test_nis2_evidence.py` reads `docs/`, which
  was not among them. Listed now, with a test that reads, from its own source, every root
  path those tests use and checks that mutmut copies it.

### Changed

- **The NIS2 document describes the branch rules as they are since 2026-10-12.** The ruleset on
  main lost the owner's bypass and requires a pull request for every change, the version bump
  included; the document said the maintainer could bypass the rules and that `release.sh` pushed the bump straight to main. A test holds the new
  wording and refuses the old.
- **`release.sh` falls back to a rebase merge** when the ruleset refuses a squash, as apkradar's
  does, and says to enable auto-merge when it cannot be turned on.
- **`release.sh` goes through a pull request.** main now takes pull requests only, the owner
  included, after a cloud session pushed straight to it with the owner's account. The script
  commits the bump on `release/vX` and pushes that branch only; with `gh` and a GitHub origin it
  opens the pull request with auto-merge, waits for the merge and tags the merge commit.
  `./release.sh --tag X` is that last step alone. The guarantees stay: nothing is committed when
  the suite fails, and the tag is made before anything is pushed. Ported from mailradar.

## [2026.5] - 2026-10-10

### Added
- **`releaseradar web`: a local dashboard.** One page on 127.0.0.1 with the
  tracked repositories, the feed of what changed (new entries highlighted as
  they arrive), last and next sync, a *Sync now* button, and the poller's
  warnings and errors. A thread runs the same `sync` as the CLI every
  `--every` minutes (default 15). Standard library only — `http.server` and a
  thread — so the base install stays at 16 distributions; no login, because it
  is bound to the loopback address and nothing else.


## [2026.4] - 2026-10-10

The first sync of `python/cpython` took two minutes behind a spinner that said
"Syncing all tracked repos…" and nothing else.

### Changed
- **`sync` says how far it is**: `python/cpython: 3,700 of ~9,642 open issues
  and pull requests, 0 releases — 37 requests so far…`, updated after every
  page. The total comes from the repository record, one extra request paid
  only by a first sync with somebody watching; later syncs count without it.
- **Issues, pull requests and releases are fetched side by side.** Each is a
  chain of round trips to GitHub; in sequence they were three chains. Measured
  on the first sync of `python/cpython`, 124 requests either way: 121 s before,
  80 s after (the issues walk alone is the long pole); the second sync, 1 s.
- The MCP `sync_repo` and `sync_all` results are unchanged; progress is a CLI
  affair.


## [2026.3] - 2026-10-10

Found by the same first user, minutes later, with `python/cpython`.

### Fixed
- **`sync` of a large repository failed with GitHub's 422** "Pagination with
  the page parameter is not supported for large datasets, please use cursor
  based pagination". The walk counted `page=1, 2, 3…`; GitHub refuses that
  from page 100 and names the next page, cursor included, in the `Link` header
  from page 1 on. The walk now follows `Link: rel="next"` and stops when no
  next page is offered.

### Changed
- The first sync of a repository asks for what is **open** now, not for its
  whole history: what was closed before anyone watched is not a transition this
  tracker can ever report. For `python/cpython` that is about 100 requests
  instead of 1 560; later syncs ask for everything updated since the last one,
  closed included.


## [2026.2] - 2026-10-10

What the first user of 2026.1 found within the hour, with the README's own
first example.

### Fixed
- **`sync` of `torvalds/linux` failed as "not found".** GitHub answers 404 to
  every `/pulls` query of that repository — pull requests are switched off
  there — while the repository, its disabled issues and its releases answer
  200. A 404 on `/pulls` alone is now checked against the repository once: if
  it is there, the sync goes on without pull requests and says so (CLI warning,
  `warnings` in the MCP result); if it is gone, that is still the error.
- `sync` printed "Nothing new." above the error of the only repository it had;
  that line is now about the repositories that were synced, so with none of
  them it is not said.
- The hint printed when `releaseradar mcp` runs without the extra said
  `pip install 'releaseradar'`: rich had taken `[mcp]` for a style tag. Escaped.


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

[Unreleased]: https://github.com/maksimtech/releaseradar/compare/v2026.7...HEAD
[2026.7]: https://github.com/maksimtech/releaseradar/compare/v2026.6...v2026.7
[2026.6]: https://github.com/maksimtech/releaseradar/compare/v2026.5...v2026.6
[2026.5]: https://github.com/maksimtech/releaseradar/compare/v2026.4...v2026.5
[2026.4]: https://github.com/maksimtech/releaseradar/compare/v2026.3...v2026.4
[2026.3]: https://github.com/maksimtech/releaseradar/compare/v2026.2...v2026.3
[2026.2]: https://github.com/maksimtech/releaseradar/compare/v2026.1...v2026.2
[2026.1]: https://github.com/maksimtech/releaseradar/releases/tag/v2026.1
