"""SonarCloud is told the version from the package, not from a file nobody updates.

sonar-project.properties said `sonar.projectVersion=2026.1` from the first commit
to 2026.6, and release.sh never touched it, so every analysis was filed under a
version the project had left behind in October. cookieradar had the same stale
line (2026.09.1) and takes the version from the package; this is the same rule
(cookieradar's tests/test_ci_scripts.py, test_sonar_version_comes_from_the_package).
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]


def _sonar_steps():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "sonarcloud.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["sonarcloud"]["steps"]


def test_no_version_is_written_down_in_the_properties():
    properties = (ROOT / "sonar-project.properties").read_text(encoding="utf-8")
    assert "sonar.projectVersion" not in properties


def test_the_scan_is_given_the_version_of_the_package():
    steps = _sonar_steps()
    scan = next(s for s in steps if "sonarqube-scan-action" in s.get("uses", ""))
    assert "-Dsonar.projectVersion=${{ steps.version.outputs.version }}" in scan["with"]["args"]
    version = next(s for s in steps if s.get("id") == "version")
    assert "__version__" in version["run"], "read from releaseradar/__init__.py, the one place it lives"
    assert steps.index(version) < steps.index(scan)
