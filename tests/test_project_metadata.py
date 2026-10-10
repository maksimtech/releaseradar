"""What pyproject.toml tells PyPI about the project.

Nothing in the build reads these fields, so nothing fails when they are wrong:
the Changelog link answered 404 for as long as it was there, and the classifiers
stopped at 3.13 while the suite already ran on 3.14.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_the_changelog_url_is_not_a_404():
    """https://github.com/<org>/<repo>/CHANGELOG.md is a 404: /blob/main/ was missing."""
    url = _project()["urls"]["Changelog"]
    assert url == "https://github.com/maksimtech/releaseradar/blob/main/CHANGELOG.md"


def test_python_314_is_among_the_classifiers():
    """The suite runs on 3.14, and the other four Radar declare it."""
    assert "Programming Language :: Python :: 3.14" in _project()["classifiers"]


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_names_every_radar():
    """The suite line listed three of the five and called itself complete."""
    readme = _readme().lower()
    missing = [r for r in ("mailradar", "patchradar", "apkradar", "cookieradar", "exeradar") if r not in readme]
    assert missing == [], missing


def test_the_readme_configures_the_mcp_server_the_way_claude_code_reads_it():
    """`~/.config/claude/mcp.json` is not a path Claude Code reads. It reads a
    project `.mcp.json`, or what `claude mcp add` writes."""
    readme = _readme()
    assert "~/.config/claude/mcp.json" not in readme
    assert "claude mcp add" in readme
    assert "releaseradar mcp" in readme


def test_the_readme_says_what_a_sync_costs_without_a_token():
    """60 requests an hour anonymous, 5000 with a token: the first sync of a busy
    repository does not fit in the first number, and the reader should learn it
    here and not from a 403."""
    readme = _readme()
    assert "GITHUB_TOKEN" in readme
    assert "60" in readme and "5" in readme
