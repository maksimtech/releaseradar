# Changelog

All notable changes to ReleaseRadar are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow the suite's [CalVer](https://calver.org/): `YYYY.count[.fix]`, where
the year is the generation shared by the five Radar and the count is this one's own.

## [Unreleased]

### Changed

- **Mutation testing runs again, and a test catches the next gap on the pull request.** The run of
  2026-11-21 stopped: `tests/test_safe_chain.py`, since 2026-11-16, reads `.aikido`, which mutmut did
  not copy into `mutants/`. Behind it was a second gap the run never reached, since it stops at the
  first failure: `tests/test_sonar_version.py`, since 2026-11-17, reads `sonar-project.properties`.
  Both are now in `also_copy`. `tests/test_mutmut_copy.py` reads the tests for the files they join to
  the repository root and fails when one that exists is not copied, so the next one shows on its pull
  request instead of a Saturday later. Verified by rebuilding `mutants/` from mutmut 3.8's copy rules
  and running the suite inside it: 502 passed.

## [2026.8] - 2026-11-18

### Changed

- **Two OpenSSL issues of 2026-11-17 are accepted until Debian ships 3.5.10.** On 2026-11-18 Docker
  Scout reported CVE-2026-107493 and CVE-2026-124709 as high against the same openssl, and Security
  Posture failed again. OpenSSL rates both Low and fixes them in 3.5.10; Debian lists trixie and sid
  as vulnerable, with no fix. Neither is reachable here: one needs an application passing its own
  stack to `PEM_X509_INFO_read_bio_ex()`, which OpenSSL and Python's ssl never do, the other a QUIC
  server. The advisory's third issue, CVE-2026-35190, is not reported by any scanner yet and has no
  entry, since an unused one fails the gate. Reviewed on 2026-12-15 with the four of 2026-11-10 below.

- **Four OpenSSL issues of 2026-11-10 are accepted until Debian ships 3.5.10.** At the scan of main
  on 2026-11-17 Docker Scout reported CVE-2026-95691, CVE-2026-95692, CVE-2026-88823 and
  CVE-2026-107496 as high against the base image's openssl 3.5.7-1~deb13u3, and Security Posture
  failed. OpenSSL rates all four Low and fixes them in 3.5.10; Debian has no trixie package with the
  fix yet. None is reachable here: they need the OpenSSL QUIC client, AES-GCM-SIV through EVP, or
  `CMS_encrypt()`, and Python's ssl exposes none of them. The entries are reviewed on 2026-12-15;
  the rebuild that brings the DSA will make them fail the gate as unused.

### Fixed

- **`untrack` checks the name like every other command, and `untrack --invalid` removes the rows
  earlier versions stored under names GitHub could not have given.** Until now `untrack`
  (CLI and MCP `untrack_repo`) removed a row by the name exactly as typed, unchecked, so that such
  rows could be removed at all: the one input path the name rules did not cover, and no help for a
  row with a NUL or a newline in it, which cannot be typed back. `untrack owner/repo` now refuses an
  invalid name like `track` and `sync` do, and says to use `releaseradar untrack --invalid`, which
  takes no name: it finds every stored row whose owner or name fails the rules, removes it with its
  issues, pull requests, releases and events, and prints each one as its repr, so a NUL, a newline
  or `[/]` is visible and harmless; with none it says so. Exit 0 either way. `list`, the MCP
  `list_repos` (`invalid_name`, and a `hint`) and the dashboard mark such rows the same way and
  point to the command, and `sync` names them by their repr in its error lines, escaped, where a
  `[/]` in a stored name used to stop the output with a MarkupError. The MCP server has no tool for
  the cleanup: it deletes only rows no valid command can reach, but deleting in bulk is left to a
  person at the command line, and its errors say which command.

- **The dashboard answers only to its own address, and a sync only to its own page.** Bound to
  127.0.0.1, it still served any request that got there: a page whose domain is made to resolve to
  127.0.0.1 after it loaded (DNS rebinding) could read `/api/state` under its own `Host`, and a
  cross-site form or fetch got 202 from `POST /api/sync`, a sync with the token of whoever started
  the dashboard; both reproduced on 2026-11-17 with `Host: evil.example:80` and
  `Origin: https://evil.example`. docs/nis2-compliance.md listed it as an accepted gap. Every
  request must now carry a `Host` of `127.0.0.1`, `localhost` or `[::1]` with the dashboard's port,
  or a name given with the new `--allow-host NAME[:PORT]`; anything else, or no `Host`, is a 403
  with no data. A POST must also carry an `Origin` of `http://` and one of those names, or, without
  one, `Sec-Fetch-Site: same-origin`, which the page's *Sync now* does on its own; a client that
  sends neither is refused. The document, README and SECURITY.md now say what is checked;
  `tests/test_web.py` and `tests/test_nis2_evidence.py` ask a real server.

- **A repository name can no longer take a request, token included, outside `/repos/<owner>/<repo>`,
  and one bad name no longer stops every sync.** The name went into the URL as typed. httpx
  normalises `/repos/../users` to `/users`, so `releaseradar track ../users` printed "Now tracking"
  after GitHub answered 200 for an endpoint that is not a repository, and syncing `../..` asked
  `/issues`, the token owner's own issues across GitHub; `psf/requests#x` and `psf/requests?` lost
  the rest as a fragment or a query, checked psf/requests and were stored. The next `sync` walked
  that repository object as a page of issues and died on a TypeError, which `sync_all` did not
  catch: the CLI, the dashboard's poller and the MCP `sync_all` stopped syncing every repository. A
  NUL in a name reached the MCP client as a bare "Error executing tool track_repo". All seen on
  2026-11-17. The name is now checked against GitHub's rules (owner: letters, digits and hyphens,
  at most 39, starting with a letter or a digit; repository: letters, digits, `.`, `_` and `-`, at
  most 100, not `.` or `..`) by `track`, `untrack`, `sync` and `status` and by every MCP tool that
  takes one, with a sentence saying what is wrong; the GitHub client builds every URL through one
  function that checks the name again and percent-encodes each segment. A stored row that fails the
  check is that repository's error in `sync`, and `untrack --invalid` removes it (see above).
  Anything else that goes wrong with one repository in `sync_all` is reported for it,
  by type, and the others still sync; a list endpoint that answers with something other than a list
  is a GitHub error with a sentence, not a TypeError.

## [2026.7] - 2026-11-17

### Added

- **Every package the CI installs from PyPI goes through Aikido Safe Chain.** None of the scanners
  here tells a malicious package from a clean one: they match versions against advisories, and a
  package published to steal tokens has none on the day it is installed. Safe Chain (1.5.27,
  AGPL, no account or token) checks each download against Aikido's malware list and hides releases
  younger than 48 hours. The nine jobs that install set it up through
  `.github/actions/safe-chain`, after setup-python, and the action proves it blocks — pip on PATH
  is the shim, `safe-chain-verify` answers, and Aikido's canary package is refused with Safe
  Chain's own message — because the installer only warns when its setup fails. Its shims stand in
  for `pip`, `uv` and `python` by name, so the installs that went by a venv's path (the SBOM venvs,
  and `install_from_pypi.sh` in a release) now run `pip --python <venv>`: measured on 2026-11-16, a
  venv's pip called by its path went to PyPI unchecked. `.aikido` spares releaseradar alone from
  the age check, which would otherwise hide the version a release installs minutes after
  uploading it. The package is built with `python -m build --no-isolation` after pip has installed
  hatchling: build's own isolated environment is fetched by a `python` the shim passes straight
  through, and on 2026-11-17 a project whose build requirement was the canary had it blocked under
  `pip install -e .` and fetched unchecked under `python -m build`. Release Evidence sets the
  action up from the workflow's own commit before checking the tag out, so the evidence of a tag
  cut before the action existed can still be rebuilt. The action's version pin is not called
  `SAFE_CHAIN_VERSION`, a name the installer reads as its own deprecated pin. Not covered: pip
  inside `docker build`. `tests/test_safe_chain.py` holds all of it. A pilot: the other five Radar
  follow once it has run here.

### Changed

- **The sqlite3 findings say what Debian decided, and Trivy's two new ones are recorded.** On
  2026-11-09 Trivy began reporting CVE-2026-119374 and CVE-2026-119379, which only Docker Scout had
  reported, and Security Posture failed on main. Both now have a Trivy entry beside the Scout one.
  The sqlite3 entries also said there was no fixed version anywhere and that they waited for Debian:
  Debian has since fixed the three CVEs in sid (3.54.1-1) and marked them no-dsa for trixie, so no
  trixie update will come. They stay accepted on the ground they already gave, that no code of the
  project reaches the flawed parts of SQLite, until 2026-12-31, when the question becomes a base
  image with a fixed sqlite3.

- **Trivy scans the image a pull request builds; Snyk is gone.** As in maksimtech/apkradar#54: of what
  `snyk.yml` ran, only the container scan worked. Snyk Open Source stopped on `SNYK-OS-PYTHON-0013`
  ("Missing required packages") here too, so its check on new dependency issues let everything through;
  Snyk Code repeated CodeQL, Bandit and SonarCloud; and the free plan's monthly test quota, shared by the
  Radar, was spent. `trivy.yml` now builds the image and scans it before merge, with no token and no
  quota. The Snyk-sourced entries left `SECURITY-EXCEPTIONS.toml`, their reasons moving into the entries
  of the scanners that still report the same CVEs; `tests/test_trivy_workflow.py` holds the image scan,
  and `tests/test_workflow_set.py` keeps `snyk.yml` retired.
  Its first run reported nine CVEs in the base image's Debian packages (util-linux ×4, ncurses, systemd, perl, sqlite3, acl), all
  without a fix in trixie and none in code this tool runs; with the three sqlite3 CVEs Docker Scout raised
  on `main` on 2026-11-02, which had turned Security Posture red, they are recorded in
  `SECURITY-EXCEPTIONS.toml` to wait for Debian, as the maintainer decided that day for every Radar.

- **The author address published on PyPI is the project alias, github@maksimtech.com.** It was a
  personal mailbox, which PyPI shows to anyone and bulk senders harvest: on 2026-10-31 a cold "patch
  offer" from a mass-mailing account reached it. Releases already published keep the old address;
  the next one carries the alias. `tests/test_package_metadata.py` keeps it that way.

- **The retried install that checks a release is a tested script, the same in all six Radar.** The
  loop added to *Publish to PyPI* after v2026.2 and v2026.4 sat inline in the workflow with no test.
  It is now `.github/scripts/install_from_pypi.sh`, run by `tests/test_install_from_pypi.py`, and
  installs the wheel only (`--only-binary :all:`). The 45 s grace in front of it goes to 0: it did
  not save v2026.4, and the retry is what holds. apkradar lost the same race on 2026.48, which is
  why the other five Radar now have it too.

- **The image is built on Python 3.14.** It was built on `python:3.12-slim-trixie`; 3.12 has had
  security fixes only since April 2025 and 3.13 joined it in October 2026, while 3.14 gets bug fixes
  until October 2027 and security fixes until October 2030. 3.15, a month old, is too new for the
  wheels an image needs. The SBOM of a release's Python environment is resolved with the same
  interpreter, so the two SBOMs attached to a release describe one resolution;
  `tests/test_docker_python.py`, the same in every Radar, keeps them together. All six Radar are now
  built on 3.14. The test matrix (3.11 to 3.14) and the Python that runs the tools are unchanged.
  Measured on 2026-11-16: the image builds on 3.14.10, `--version` runs, pip is absent, and Trivy
  reports the same high findings as the published 3.12 image, all in Debian packages, none in
  Python.

- **The acl exception is reviewed, and runs to 2027-01-31.** CVE-2026-54369 was due on 2026-11-30,
  after which Security Posture would have failed on its own. Read again on 2026-11-17: still no-dsa
  for trixie (Debian bug 1141110), acl 2.4.0 only in forky and sid, nothing in
  trixie-proposed-updates, and the last point release still 13.7 of 2026-09-12. The new date falls
  after the 13.8 point release that is due.

### Fixed

- **SonarCloud files each analysis under the version of the package.** `sonar-project.properties`
  said `sonar.projectVersion=2026.1` from the first commit to 2026.6, and nothing updated it. The
  scan now reads `__version__` and passes it, as cookieradar does (`tests/test_sonar_version.py`).

- **Security Posture reads every page of alerts.** The code scanning API returns at most a hundred
  alerts per call, and the gate read one page of each list. Found in cookieradar on 2026-11-13
  (cookieradar #70): a pull request with 123 open alerts and main with 142, three exceptions whose
  findings sat past the hundredth read as matching nothing, and the gate failed on a product whose every
  alert was explained; read the other way, an unexplained alert past the hundredth would not have been
  seen at all. This repository had 47 open on main on 2026-11-15, so its gate had not met the limit yet.
  Each of the four reads now goes through `gh api --paginate --slurp` and joins the pages; a failed read
  still fails, so an unreadable list is not taken for an empty one.
  `tests/test_security_posture_workflow.py` runs the step's own function against a stand-in `gh` that
  answers in two pages.

- **Docker Scout no longer fails on its own base-image advice.** From 2026-10-24 every run of
  `docker-scout.yml` failed and GitHub mailed it on each push to main: the CVE scan worked, then the scout
  CLI crashed in the `recommendations` part (nil pointer in `recommendations/markdown.go`) on images built
  on python:3.12-slim-trixie. The scan is now a step of its own that writes the SARIF and fails the job if it
  fails; the recommendations are a second step with `continue-on-error`. `tests/test_scout_steps.py`
  holds the two apart. Same change as maksimtech/mailradar#76.

- **Docker Scout scans the image as soon as it is published, and Security Posture reads that scan.**
  The scan reads `latest` from Docker Hub but ran only on a push to main, weekly or by hand: after the
  certifi release the gate stayed red on the replaced image until both were dispatched by hand.
  `docker-scout.yml` now follows the image workflow (`workflow_run`, successful runs only) and
  `security-posture.yml` follows the scan. `docs/nis2-compliance.md` already said the scan ran on every image build; now it does, and `tests/test_scan_after_publish.py`
  holds them together.

- **Security Posture gives no verdict on a failed Docker Scout scan.** It ran after every Scout run,
  failed ones included. On 2026-11-16 exeradar's Scout stopped on a 403 before scanning, and the
  gate went green on the previous scan's alerts. It now runs on a `workflow_run` only when the scan
  succeeded, as Scout itself does after the image workflow. Push, pull request, schedule and manual
  runs are unchanged.

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

[Unreleased]: https://github.com/maksimtech/releaseradar/compare/v2026.8...HEAD
[2026.8]: https://github.com/maksimtech/releaseradar/compare/v2026.7...v2026.8
[2026.7]: https://github.com/maksimtech/releaseradar/compare/v2026.6...v2026.7
[2026.6]: https://github.com/maksimtech/releaseradar/compare/v2026.5...v2026.6
[2026.5]: https://github.com/maksimtech/releaseradar/compare/v2026.4...v2026.5
[2026.4]: https://github.com/maksimtech/releaseradar/compare/v2026.3...v2026.4
[2026.3]: https://github.com/maksimtech/releaseradar/compare/v2026.2...v2026.3
[2026.2]: https://github.com/maksimtech/releaseradar/compare/v2026.1...v2026.2
[2026.1]: https://github.com/maksimtech/releaseradar/releases/tag/v2026.1
