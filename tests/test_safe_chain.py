"""Every package the CI installs from PyPI goes through Aikido Safe Chain.

None of the scanners here (Dependabot, Trivy, Docker Scout, CodeQL) knows a
malicious package from a clean one: they match versions against advisories, and
a package published to steal tokens has no advisory on the day it is installed.
Safe Chain checks every download against Aikido's malware list and hides
releases younger than 48 hours, which is when most such packages are found and
pulled.

It works by shims: `pip`, `uv`, `python` on PATH are wrappers that route the
download through a local proxy. Measured on 2026-11-16 with 1.5.27, in WSL:

  pip install <canary>                       blocked ("blocked by safe-chain")
  pip --python <venv>/bin/python install     blocked
  <venv>/bin/pip install                     NOT protected: called by path, it
                                             goes straight to PyPI
  a release one hour old                     hidden by the age check
  the same with an exclusion in .aikido      allowed

so this file holds four things: the jobs that install set Safe Chain up first,
and after setup-python, whose own PATH entry would otherwise come first; nothing
installs by a path that skips the shims; the action proves the blocking works
rather than trusting the installer, which only warns when its setup fails; and
the age exclusion covers this package alone, which a release has to install
minutes after uploading it.

Not covered: `docker build`. pip inside the image's build runs in the container,
where the runner's shims are not.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / ".github" / "scripts"
ACTION = ROOT / ".github" / "actions" / "safe-chain" / "action.yml"
AIKIDO = ROOT / ".aikido"
PACKAGE = "releaseradar"
SAFE_CHAIN = "./.github/actions/safe-chain"

# A download from an index, by a command found on PATH.
BY_NAME = re.compile(
    r"(?<![\w/.\"-])(?:pip3?|python3?\s+-m\s+pip)\b[^\n]*?\b(?:install|download|wheel)\b"
    r"|(?<![\w/.\"-])uvx?\s+(?:[^\n]*\s)?(?:sync|lock|add|pip|tool|run)\b"
)
# The same by a path, which the shims never see.
BY_PATH = re.compile(
    r"/bin/(?:pip3?\"?|python3?\"?\s+-m\s+pip)\s+(?:-\S+\s+)*(?:install|download|wheel)\b"
)


def _code(text: str) -> str:
    """Shell text without its comments, so a comment naming a command is not one."""
    return "\n".join(line.split(" #")[0] for line in text.splitlines() if not line.lstrip().startswith("#"))


def _scripts_that_install() -> set[str]:
    return {p.name for p in SCRIPTS.glob("*.sh") if BY_NAME.search(_code(p.read_text(encoding="utf-8")))}


def _installs(step: dict) -> bool:
    run = _code(step.get("run", ""))
    return bool(BY_NAME.search(run)) or any(name in run for name in _scripts_that_install())


def _jobs():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (workflow.get("jobs") or {}).items():
            yield f"{path.name}:{job_id}", job.get("steps") or []


def _installing_jobs():
    return [(name, steps) for name, steps in _jobs() if any(_installs(s) for s in steps)]


def test_the_jobs_that_install_are_the_ones_expected():
    """Holds the inventory still, so a regex that stops matching cannot make every
    other test here pass by finding nothing to check."""
    assert {name for name, _ in _installing_jobs()} == {
        "bandit.yml:bandit",
        "codspeed.yml:benchmarks",
        "licenses.yml:licenses",
        "mutation.yml:mutmut",
        "publish.yml:build-and-publish",
        "quality.yml:quality",
        "sbom.yml:evidence",
        "sonarcloud.yml:sonarcloud",
        "test.yml:test",
    }


@pytest.mark.parametrize("name, steps", _installing_jobs(), ids=lambda v: v if isinstance(v, str) else "")
def test_safe_chain_is_set_up_before_anything_is_installed(name, steps):
    uses = [s.get("uses", "") for s in steps]
    guard = [i for i, u in enumerate(uses) if u == SAFE_CHAIN]
    assert len(guard) == 1, f"{name}: Safe Chain is set up {len(guard)} times"
    first_install = next(i for i, s in enumerate(steps) if _installs(s))
    assert guard[0] < first_install, f"{name}: something is installed before Safe Chain"
    checkout = next(i for i, u in enumerate(uses) if u.startswith("actions/checkout"))
    assert checkout < guard[0], f"{name}: the local action needs the checkout first"


@pytest.mark.parametrize("name, steps", _installing_jobs(), ids=lambda v: v if isinstance(v, str) else "")
def test_safe_chain_comes_after_the_python_it_has_to_shadow(name, steps):
    """setup-python adds its directory to PATH too; whichever is added last is
    looked up first, and pip found before the shim is pip without Safe Chain."""
    uses = [s.get("uses", "") for s in steps]
    guard = uses.index(SAFE_CHAIN)
    for i, u in enumerate(uses):
        if u.startswith(("actions/setup-python", "astral-sh/setup-uv")):
            assert i < guard, f"{name}: {u} comes after Safe Chain and would put its pip first"


def test_nothing_installs_by_a_path_the_shims_do_not_see():
    files = [*WORKFLOWS.glob("*.yml"), *SCRIPTS.glob("*.sh")]
    found = [
        f"{path.name}: {match.group(0)}"
        for path in files
        for match in BY_PATH.finditer(_code(path.read_text(encoding="utf-8")))
    ]
    assert not found, found


# ─── the action ─────────────────────────────────────────────────────────────

def _action_steps():
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    return action["runs"]["steps"]


def test_the_installer_is_a_pinned_release_checked_before_it_runs():
    install = next(s for s in _action_steps() if "install-safe-chain.sh" in s.get("run", ""))
    run = install["run"]
    url = re.search(r"/releases/download/\$\{?(\w+)\}?/install-safe-chain\.sh", run)
    assert url, run
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(install["env"][url.group(1)])), "a version, not latest"
    assert re.fullmatch(r"[0-9a-f]{64}", str(install["env"]["INSTALLER_SHA256"]))
    assert run.index("sha256sum -c") < run.index("--ci"), "the script runs only once its hash matches"


def test_the_action_sets_no_variable_the_installer_reads():
    """The pilot named its version SAFE_CHAIN_VERSION, which is the installer's own
    deprecated pin. With it set, install-safe-chain.sh warns in every job and, as a
    "legacy" install, downloads the glibc build (safe-chain-linux-x64) instead of the
    static one its release otherwise uses. The pin belongs in the URL alone."""
    for step in _action_steps():
        assert "SAFE_CHAIN_VERSION" not in (step.get("env") or {}), step.get("name")
        assert not re.search(r"\bSAFE_CHAIN_VERSION=", _code(step.get("run", ""))), step.get("name")


# ─── what the shims cannot see ──────────────────────────────────────────────

def test_no_build_runs_in_an_isolation_safe_chain_cannot_see():
    """`python -m build` installs the build backend into an isolated environment of
    its own, and the `python` shim passes everything but `-m pip` straight to the
    interpreter. Measured on 2026-11-17 in WSL with a project whose only build
    requirement was Aikido's canary: `pip install -e .` had it blocked (pip's own
    isolated build runs under the shim's proxy), `python -m build` fetched it from
    PyPI unchecked. So the backend is installed by pip, through the shim, and the
    build runs without isolation."""
    for path in WORKFLOWS.glob("*.yml"):
        for line in _code(path.read_text(encoding="utf-8")).splitlines():
            if re.search(r"\bpython3?\s+-m\s+build\b", line):
                assert re.search(r"\s(--no-isolation|-n)(\s|$)", line), f"{path.name}: {line.strip()}"


def test_the_backend_a_build_needs_is_installed_through_the_shim_first():
    job = yaml.safe_load((WORKFLOWS / "publish.yml").read_text(encoding="utf-8"))["jobs"]["build-and-publish"]
    runs = [_code(s.get("run", "")) for s in job["steps"]]
    build = next(i for i, r in enumerate(runs) if re.search(r"\bpython3?\s+-m\s+build\b", r))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    backend = re.search(r'requires\s*=\s*\["([A-Za-z0-9_.-]+)', pyproject).group(1)
    installs = [i for i, r in enumerate(runs) if BY_NAME.search(r) and backend in r]
    assert installs and installs[0] < build, f"{backend} is not installed by pip before the build"


def test_the_evidence_job_sets_safe_chain_up_from_its_own_commit():
    """Release Evidence can be dispatched for any tag, and it checks that tag out.
    Tags cut before Safe Chain have no .github/actions/safe-chain, so the action has
    to come from the workflow's own checkout, before the tag replaces it; the shims
    live in $HOME and stay on PATH after the second checkout."""
    steps = yaml.safe_load((WORKFLOWS / "sbom.yml").read_text(encoding="utf-8"))["jobs"]["evidence"]["steps"]
    uses = [s.get("uses", "") for s in steps]
    checkouts = [i for i, u in enumerate(uses) if u.startswith("actions/checkout")]
    guard = uses.index(SAFE_CHAIN)
    first_install = next(i for i, s in enumerate(steps) if _installs(s))

    own = [i for i in checkouts if "ref" not in (steps[i].get("with") or {})]
    tag = [i for i in checkouts if "TAG" in str((steps[i].get("with") or {}).get("ref", ""))]
    assert own and own[0] < guard, "the action is set up from the workflow's own commit"
    assert tag and guard < tag[0] < first_install, "the tag is checked out after Safe Chain, before installing"


def test_the_action_proves_the_blocking_instead_of_trusting_the_installer():
    """The installer prints a warning and exits 0 when `safe-chain setup-ci`
    fails. A job would then install everything unprotected and look protected."""
    steps = _action_steps()
    install = next(i for i, s in enumerate(steps) if "install-safe-chain.sh" in s.get("run", ""))
    proof = next(i for i, s in enumerate(steps) if "safe-chain-pi-test" in s.get("run", ""))
    assert install < proof, "the proof runs in a later step, once PATH holds the shims"
    run = steps[proof]["run"]
    assert "command -v pip" in run, "pip on PATH has to be the shim"
    assert "OK: Safe-chain works!" in run
    # The canary does not exist on PyPI: without Safe Chain it fails too, with
    # "No matching distribution". Only the message tells the two apart.
    assert "blocked by safe-chain" in run


# ─── the age exclusion ──────────────────────────────────────────────────────

def test_only_this_package_is_spared_the_age_check():
    """A release installs releaseradar==<new> minutes after uploading it — the
    wait for the index and the check that the wheel installs — and the age check
    would hide exactly that version. Nothing else is spared, and the 48 hours are
    not shortened."""
    config = yaml.safe_load(AIKIDO.read_text(encoding="utf-8"))["safe-chain"]
    assert config["pip"]["minimumPackageAgeExclusions"] == [PACKAGE]
    assert config.get("minimumPackageAgeHours", 48) >= 48
    assert set(config) == {"pip"}
