"""Tests for the CLI (cli.py) — Typer CliRunner."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from releaseradar.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_db(tmp_path: Path, monkeypatch):
    """Point every CLI test at a temp DB so tests are independent."""
    monkeypatch.setenv("RELEASERADAR_DB", str(tmp_path / "test.db"))


class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "releaseradar" in result.output


class TestTrack:
    def test_track_invalid_format(self):
        result = runner.invoke(app, ["track", "noowner"])
        assert result.exit_code != 0
        assert "owner/repo" in result.output

    def test_track_repo_not_found(self):
        with patch("releaseradar.cli.gh.repo_exists", return_value=False):
            result = runner.invoke(app, ["track", "nobody/nothing"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_track_success(self):
        with patch("releaseradar.cli.gh.repo_exists", return_value=True):
            result = runner.invoke(app, ["track", "torvalds/linux"])
        assert result.exit_code == 0
        assert "torvalds/linux" in result.output


class TestUntrack:
    def test_untrack_not_tracked(self):
        result = runner.invoke(app, ["untrack", "nobody/nothing"])
        assert result.exit_code != 0
        assert "not being tracked" in result.output

    def test_untrack_success(self):
        with patch("releaseradar.cli.gh.repo_exists", return_value=True):
            runner.invoke(app, ["track", "torvalds/linux"])
        result = runner.invoke(app, ["untrack", "torvalds/linux"])
        assert result.exit_code == 0


class TestList:
    def test_list_empty(self):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No repositories" in result.output

    def test_list_with_repos(self):
        with patch("releaseradar.cli.gh.repo_exists", return_value=True):
            runner.invoke(app, ["track", "torvalds/linux"])
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "torvalds/linux" in result.output


class TestStatus:
    def test_status_not_tracked(self):
        result = runner.invoke(app, ["status", "nobody/nothing"])
        assert result.exit_code != 0

    def test_status_tracked(self):
        with patch("releaseradar.cli.gh.repo_exists", return_value=True):
            runner.invoke(app, ["track", "torvalds/linux"])
        result = runner.invoke(app, ["status", "torvalds/linux"])
        assert result.exit_code == 0
        assert "torvalds/linux" in result.output


class TestSync:
    def test_sync_nothing_to_sync(self):
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == 0
        assert "Nothing to sync" in result.output

    def test_sync_single_repo_invalid(self):
        result = runner.invoke(app, ["sync", "noowner"])
        assert result.exit_code != 0

    def test_sync_all(self):
        with patch("releaseradar.cli.gh.repo_exists", return_value=True):
            runner.invoke(app, ["track", "torvalds/linux"])
        with (
            patch("releaseradar.cli.sync_all", return_value={"torvalds/linux": []}),
        ):
            result = runner.invoke(app, ["sync"])
        assert result.exit_code == 0


class TestEvents:
    def test_events_empty(self):
        result = runner.invoke(app, ["events"])
        assert result.exit_code == 0
        assert "No events" in result.output
