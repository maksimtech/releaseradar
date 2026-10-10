# Security Policy

## Reporting Vulnerabilities

Please report security issues to security@maksimtech.com (GPG key on keys.openpgp.org).

## What this tool touches

ReleaseRadar reads the GitHub REST API and writes a local SQLite file. It sends
nothing anywhere else. A `GITHUB_TOKEN`, when given, travels only to
api.github.com in the `Authorization` header and is never written to the
database or the logs. The MCP server speaks over stdio to the client that
started it and opens no port.

## Known Open CVEs

`SECURITY-EXCEPTIONS.toml` is the record: every open high or critical finding
that is accepted for now, with the reason and the date it will be looked at
again. `.github/workflows/security-posture.yml` fails the build when a finding
is open and not in that file, and when an entry is past its date.

Last reviewed: 2026-10-10
