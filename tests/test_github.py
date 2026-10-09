"""Tests for the GitHub API client (github.py)."""

import pytest
import httpx
import respx

from releaseradar import github as gh


# ── extract_linked_issues ─────────────────────────────────────────────────────

class TestExtractLinkedIssues:
    def test_empty_body(self):
        assert gh.extract_linked_issues(None) == []

    def test_fixes(self):
        assert gh.extract_linked_issues("Fixes #42") == [42]

    def test_closes(self):
        assert gh.extract_linked_issues("Closes #10") == [10]

    def test_resolves(self):
        assert gh.extract_linked_issues("Resolves #7") == [7]

    def test_multiple(self):
        result = gh.extract_linked_issues("Fixes #1, closes #2, resolves #3")
        assert result == [1, 2, 3]

    def test_case_insensitive(self):
        assert gh.extract_linked_issues("FIXES #99") == [99]

    def test_no_matches(self):
        assert gh.extract_linked_issues("Just a regular PR description") == []

    def test_title_too(self):
        assert gh.extract_linked_issues("body", "Fix #5") == [5]


# ── normalise_issue ───────────────────────────────────────────────────────────

class TestNormaliseIssue:
    def test_basic(self):
        raw = {
            "number": 1,
            "title": "Bug report",
            "state": "open",
            "html_url": "https://github.com/t/l/issues/1",
            "created_at": "2026-01-01T00:00:00Z",
            "closed_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
        }
        norm = gh.normalise_issue(raw)
        assert norm["number"] == 1
        assert norm["state"] == "open"
        assert norm["opened_at"] == "2026-01-01T00:00:00Z"


# ── normalise_pr ──────────────────────────────────────────────────────────────

class TestNormalisePr:
    def test_open_pr(self):
        raw = {
            "number": 2,
            "title": "Feature",
            "state": "open",
            "html_url": "https://github.com/t/l/pull/2",
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
            "body": None,
        }
        norm = gh.normalise_pr(raw)
        assert norm["state"] == "open"
        assert norm["merged_at"] is None
        assert norm["linked_issues"] is None

    def test_merged_pr(self):
        raw = {
            "number": 3,
            "title": "Merged feature",
            "state": "closed",
            "html_url": "https://github.com/t/l/pull/3",
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "body": "Fixes #10",
        }
        norm = gh.normalise_pr(raw)
        assert norm["state"] == "merged"
        assert norm["linked_issues"] == "10"

    def test_linked_issues_multiple(self):
        raw = {
            "number": 4,
            "title": "Multi-fix",
            "state": "closed",
            "html_url": "https://github.com/t/l/pull/4",
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "body": "Closes #1, fixes #2",
        }
        norm = gh.normalise_pr(raw)
        assert norm["linked_issues"] == "1,2"


# ── normalise_release ─────────────────────────────────────────────────────────

class TestNormaliseRelease:
    def test_basic(self):
        raw = {
            "tag_name": "v1.0.0",
            "name": "First release",
            "html_url": "https://github.com/t/l/releases/tag/v1.0.0",
            "published_at": "2026-01-01T00:00:00Z",
            "prerelease": False,
            "body": "Release notes",
        }
        norm = gh.normalise_release(raw)
        assert norm["tag_name"] == "v1.0.0"
        assert norm["prerelease"] == 0

    def test_prerelease_flag(self):
        raw = {
            "tag_name": "v2.0.0-rc1",
            "name": None,
            "html_url": "https://github.com/t/l/releases/tag/v2.0.0-rc1",
            "published_at": "2026-01-01T00:00:00Z",
            "prerelease": True,
            "body": None,
        }
        norm = gh.normalise_release(raw)
        assert norm["prerelease"] == 1
        # name falls back to tag_name when absent
        assert norm["name"] == "v2.0.0-rc1"


# ── repo_exists ───────────────────────────────────────────────────────────────

class TestRepoExists:
    @respx.mock
    def test_exists(self):
        respx.get("https://api.github.com/repos/torvalds/linux").mock(
            return_value=httpx.Response(200)
        )
        assert gh.repo_exists("torvalds", "linux") is True

    @respx.mock
    def test_not_found(self):
        respx.get("https://api.github.com/repos/nobody/nothing").mock(
            return_value=httpx.Response(404)
        )
        assert gh.repo_exists("nobody", "nothing") is False
