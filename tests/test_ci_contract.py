"""What the Tests workflow has to do, read from the workflow file.

Ported from mailradar, where the workflow once ran `--help` and two live-domain
commands and never pytest. Here the invariants are the same: the suite runs,
with the dev extra installed, without touching the network; the coverage report
that is written is the one that is uploaded; the matrix promises what the
classifiers promise.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"


@pytest.fixture(scope="module")
def workflow() -> str:
    return TEST_WORKFLOW.read_text(encoding="utf-8")


# ── the suite has to run ────────────────────────────────────────────────────


def test_the_workflow_invokes_pytest(workflow):
    assert re.search(r"\bpytest\b", workflow), (
        "the Tests workflow never invokes pytest — the suite cannot fail the build"
    )


def test_the_workflow_runs_the_tests_directory(workflow):
    assert re.search(r"pytest[^\n]*\btests/", workflow)


def test_the_workflow_installs_what_the_suite_needs(workflow):
    """`pip install -e .` alone does not bring pytest, so the step above would
    fail on a missing command rather than on a failing test."""
    assert re.search(r"pip install[^\n]*\[dev\]", workflow), (
        "the dev extra is not installed, so pytest is not there to run"
    )


def test_no_step_queries_the_network_for_a_smoke_test(workflow):
    """`releaseradar track` and `sync` reach api.github.com, with 60 requests an
    hour for an anonymous runner. The suite replays GitHub from recordings; a
    check that depends on the live API goes red for reasons that have nothing
    to do with the commit."""
    for command in ("releaseradar track ", "releaseradar sync"):
        assert command not in workflow, f"{command.strip()} runs against the live API"


def test_the_cli_is_still_exercised(workflow):
    """--help costs nothing, needs no network, and catches an import error or a
    broken entry point — which the suite, importing modules directly, does not."""
    assert "releaseradar --help" in workflow


# ── the coverage upload ─────────────────────────────────────────────────────


def test_the_suite_writes_a_coverage_report(workflow):
    assert "--cov-report=xml" in workflow, "no XML is produced, so nothing can be uploaded"


def test_the_file_uploaded_is_the_file_written(workflow):
    """A path typo here reads as zero coverage, not as a broken upload."""
    written = re.search(r"--cov-report=xml(?::(\S+))?", workflow)
    assert written, "no XML report is written"
    produced = written.group(1) or "coverage.xml"

    uploaded = re.search(r"^\s*files?:\s*(\S+)", workflow, re.MULTILINE)
    assert uploaded, "the upload step names no file"
    assert uploaded.group(1) == produced


def test_the_upload_runs_once_on_a_version_the_matrix_has(workflow):
    """Guarded to one entry, so one measurement is not uploaded four times — and
    that entry has to exist, or the step never runs at all."""
    import yaml

    parsed = yaml.safe_load(workflow)
    versions = {
        str(version)
        for job in parsed["jobs"].values()
        for version in (job.get("strategy", {}).get("matrix", {}).get("python-version") or [])
    }

    guard = re.search(r"matrix\.python-version\s*==\s*'([^']+)'", workflow)
    assert guard, "the upload is not guarded to a single matrix entry"
    assert guard.group(1) in versions, (
        f"the upload is pinned to Python {guard.group(1)}, absent from {sorted(versions)}"
    )


def test_the_upload_proves_which_repository_it_comes_from(workflow):
    """Codecov wants the upload authenticated even for a public repository; without
    it the upload is rejected — silently, like everything else here.

    It was a `CODECOV_TOKEN` secret until 2026-11-22, and Codecov files an upload
    under the project the token belongs to, not under the repository that sends it:
    three Radars held exeradar's token, and their reports went to exeradar's project
    for at least a month. This repository's token was its own, but nothing kept it
    so. With OIDC, GitHub itself tells Codecov which repository the job runs in, so
    no report can land in another project; there is no secret to copy or mix up. The
    job needs `id-token: write` for that, and a token in the step would only be
    ignored next to `use_oidc` and mislead whoever reads it."""
    import yaml

    jobs = yaml.safe_load(workflow)["jobs"]
    uploads = [(name, job, step) for name, job in jobs.items() for step in job.get("steps", [])
               if str(step.get("uses", "")).startswith("codecov/codecov-action@")]
    assert uploads, "nothing uploads coverage"
    for name, job, step in uploads:
        options = step.get("with") or {}
        assert options.get("use_oidc") is True, f"{name}: the upload does not use OIDC"
        assert "token" not in options, f"{name}: a token next to use_oidc is ignored"
        assert (job.get("permissions") or {}).get("id-token") == "write", (
            f"{name}: without id-token: write the job cannot ask GitHub for the OIDC token")
    assert "secrets.CODECOV_TOKEN" not in workflow, "the secret is still read somewhere"


def test_the_actions_are_pinned_by_sha(workflow):
    """This repository pins its actions to a full commit, and a new step should
    not be the one exception."""
    for uses in re.findall(r"uses:\s*(\S+)", workflow):
        if uses.startswith("./"):
            continue  # an action of this repository, pinned by the commit it is in
        assert re.search(r"@[0-9a-f]{40}$", uses), f"{uses} is not pinned to a commit"





# ── the Python matrix ───────────────────────────────────────────────────────
#
# The matrix fell behind the project: pyproject declared 3.14 and
# the suite had been running on it for months, while test.yml still listed it as
# the `-dev` row that is allowed to fail. A version a classifier promises is a
# version the build has to be able to go red on.


def _matrix() -> dict:
    import yaml

    return yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["test"]


def _stable_versions() -> list[str]:
    return [str(v) for v in _matrix()["strategy"]["matrix"]["python-version"]]


def _classifier_versions() -> list[str]:
    import tomllib

    classifiers = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["classifiers"]
    return [
        m.group(1)
        for c in classifiers
        if (m := re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", c))
    ]


def test_every_declared_python_is_a_stable_matrix_entry():
    """A classifier is a promise; `continue-on-error` is where promises go to
    stay unverified. 3.14 was declared on PyPI and experimental in CI."""
    assert sorted(_classifier_versions()) == sorted(_stable_versions()), (
        "the classifiers and the stable matrix disagree on which Pythons are supported"
    )


def test_the_experimental_row_is_the_next_minor_as_dev():
    """One `-dev` row, the minor after the last stable one, marked experimental:
    when 3.16.0 is final (2027-10-05, PEP 826), this test says what to promote
    and what to add next (3.17-dev)."""
    stable = _stable_versions()
    major, minor = stable[-1].split(".")
    expected = f"{major}.{int(minor) + 1}-dev"

    include = _matrix()["strategy"]["matrix"].get("include") or []
    assert len(include) == 1, f"expected one experimental row, found {include}"
    row = include[0]
    assert str(row["python-version"]) == expected, (
        f"the experimental row is {row['python-version']}, not {expected}"
    )
    assert row.get("experimental") is True


def test_only_the_experimental_row_may_fail():
    """The flag is read by `continue-on-error`, or marking a row experimental
    changes nothing; and the stable rows carry `false`, or every row may fail."""
    matrix = _matrix()
    assert matrix["continue-on-error"] == "${{ matrix.experimental }}"
    assert matrix["strategy"]["matrix"]["experimental"] == [False]


def test_each_row_reports_under_a_name_the_ruleset_can_require():
    """The ruleset of main requires the stable rows by name, "Tests (Python
    3.11)" and so on, as in every Radar. Without a job name GitHub reports the
    raw matrix values, "test (3.11, false)", and a renamed row is a required
    check that never arrives."""
    assert _matrix()["name"] == "Tests (Python ${{ matrix.python-version }})"
