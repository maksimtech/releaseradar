# Changelog

All notable changes to ReleaseRadar are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [CalVer](https://calver.org/) — `YYYY.WW.MICRO`.

## [Unreleased]

## [2026.43.0] — 2026-10-09

### Added
- Initial release: track GitHub repositories from issue open → PR → release published
- SQLite state store (`~/.releaseradar/state.db`) with tables for repos, issues, PRs, releases and events
- GitHub REST API client with pagination, optional auth token, and linked-issue extraction
- CLI commands: `track`, `untrack`, `list`, `sync`, `status`, `events`
- MCP server (`releaseradar.mcp_server`) exposing all CLI operations as tools for Claude
- Docker image (`maksimtech/releaseradar`) multi-arch amd64/arm64
- CI workflows: test matrix 3.11–3.14-dev, PyPI publish (GPG-signed), Docker build + Scout scan, GitHub Release

[Unreleased]: https://github.com/maksimtech/releaseradar/compare/v2026.43.0...HEAD
[2026.43.0]: https://github.com/maksimtech/releaseradar/releases/tag/v2026.43.0
