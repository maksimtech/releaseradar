"""What docs/nis2-compliance.md tells a customer has to stay true of this code.

The document answers the seven questions a customer subject to NIS2 (directive
2022/2555, in Italy d.lgs. 138/2024) can ask a software supplier, and a customer
reads it as evidence. Every statement in it that the repository can prove is
checked here, so that the document goes red when the code moves away from it
instead of quietly turning false: the one host the tool contacts and what it
sends there, which commands contact it, where the database is and what it holds,
that the token never reaches the disk, the address the dashboard listens on, the
transport of the MCP server, the user the image runs as, the files each release
carries, the scanners it names, and the words a supplier cannot honestly use.

Ported from mailradar and exeradar. What differs here is what ReleaseRadar keeps
and serves: a SQLite database of the repositories it follows, a token it sends
to GitHub, a local web page and an MCP server.

What the repository cannot prove is not claimed as proven. The branch rules live
in GitHub's settings, so the document gives the date they were read on; there is
no penetration test, so the document says there is none.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import sqlite3
from pathlib import Path

import pytest
import yaml

from releaseradar import cli, mcp_server, web
from releaseradar import db as _db
from releaseradar import github as gh
from releaseradar.sync import sync_repo
from tests.conftest import route_repo

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "releaseradar"
DOC = ROOT / "docs" / "nis2-compliance.md"
SECURITY = ROOT / "SECURITY.md"
README = ROOT / "README.md"
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOWS = ROOT / ".github" / "workflows"
EVIDENCE = WORKFLOWS / "sbom.yml"
DOCKER = WORKFLOWS / "docker.yml"
IMAGE = "maksimtech/releaseradar"

# A module that imports one of these can reach the network; one that does not
# cannot, whatever URLs its constants mention. http.server is here because the
# dashboard listens with it.
NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:httpx|requests|urllib\.request|http\.client|http\.server|socket)\b", re.M
)
URL_HOST = re.compile(r"https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def hosts_in_code() -> dict[str, set[str]]:
    """Fixed hosts named in string constants of the modules that can reach the network.

    The next page of a list is the URL GitHub names in its `Link` header, so it
    has no fixed name here and is declared in prose.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not NETWORK_IMPORT.search(source):
            continue
        tree = ast.parse(source)
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in skip:
                continue
            for host in URL_HOST.findall(node.value):
                found.setdefault(host.lower().rstrip("."), set()).add(path.name)
    return found


def section(text: str, name: str) -> str:
    """The text between `<!-- name:start -->` and `<!-- name:end -->`."""
    match = re.search(rf"<!-- {name}:start -->(.*?)<!-- {name}:end -->", text, re.S)
    assert match, f"docs/nis2-compliance.md has no {name} section between its markers"
    return match.group(1)


def table_first_column(block: str) -> set[str]:
    """The backticked value in the first cell of every row of a markdown table."""
    values = set()
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and cells and cells[0].startswith("`"):
            values.add(cells[0].strip("`"))
    return values


def prose(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


# --- Question 3: what leaves the machine ------------------------------------


def test_every_host_the_code_contacts_is_declared_and_no_other(doc):
    declared = table_first_column(section(doc, "hosts"))
    in_code = set(hosts_in_code())
    assert declared == in_code, (
        f"declared but not in the code: {sorted(declared - in_code)}; "
        f"in the code but not declared: {sorted(in_code - declared)}"
    )


def test_the_host_rule_looks_at_something():
    """A rule that found no host would pass for the wrong reason. Compared as a
    whole list: CodeQL reads `"<host>" in ...` as a URL substring check
    (py/incomplete-url-substring-sanitization), and flagged it on PR #11."""
    assert sorted(hosts_in_code()) == ["api.github.com"]


def test_the_listening_address_is_not_taken_for_a_host():
    """web.py names 127.0.0.1 in its docstring and in LOOPBACK: that is where
    the dashboard listens, not a host it contacts."""
    assert hosts_in_code().get("127.0.0.1") is None


def command_names(path: Path) -> dict[str, ast.AST]:
    """The functions a module exposes as CLI commands or MCP tools, by the name
    a user calls them by."""
    found: dict[str, ast.AST] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and deco.func.attr in {
                "command", "tool",
            }:
                name = next(
                    (k.value.value for k in deco.keywords if k.arg == "name" and isinstance(k.value, ast.Constant)),
                    node.name,
                )
                found[name] = node
    return found


# What reaches GitHub: the client module, the sync built on it, the dashboard
# that runs the sync on a timer, and the MCP server whose tools do.
NETWORK_NAMES = {"gh", "sync_all", "sync_repo", "serve", "mcp_main"}


def reaches_github(function: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id in NETWORK_NAMES for n in ast.walk(function))


def test_the_commands_that_contact_github_are_the_ones_declared(doc):
    """The document says which commands contact api.github.com and that the
    others only read or change the local database. A command that starts
    contacting GitHub has to be named, and so does one that stops."""
    cli_commands = command_names(PACKAGE / "cli.py")
    tools = command_names(PACKAGE / "mcp_server.py")
    online_cli = {n for n, f in cli_commands.items() if reaches_github(f)}
    online_tools = {n for n, f in tools.items() if reaches_github(f)}
    assert online_cli == {"track", "sync", "web", "mcp"}
    assert online_tools == {"track_repo", "sync_repo", "sync_all"}
    hosts = section(doc, "hosts")
    for name in online_cli | online_tools:
        assert f"`{name}`" in hosts, f"{name} contacts GitHub and the hosts table does not say so"
    local = prose(section(doc, "local"))
    for name in (set(cli_commands) - online_cli) | (set(tools) - online_tools):
        assert f"`{name}`" in local, f"{name} is local only and the document does not say so"


def test_what_the_requests_carry_is_what_the_document_says(doc):
    """No User-Agent of its own: httpx's default goes out, and the document
    names it. The token goes in the Authorization header and nowhere else, and
    no redirect is followed to another host with it."""
    assert not any(k.lower() == "user-agent" for k in gh._HEADERS)
    with gh._client("ghp_nis2") as client:
        assert client.headers["user-agent"].startswith("python-httpx/")
        assert client.headers["authorization"] == "Bearer ghp_nis2"
        assert client.follow_redirects is False
    with gh._client(None) as client:
        assert "authorization" not in client.headers
    text = prose(doc)
    assert "python-httpx" in text
    assert "`Authorization`" in text
    assert "GITHUB_TOKEN" in text


TOKEN = "ghp_nis2EvidenceTokenThatMustNotReachTheDisk"


def test_the_token_is_sent_to_github_and_never_written(db_path, github):
    """A sync with a token, GitHub replayed from recordings: every request
    carries it, the database file does not contain it, the directory holds no
    other file, and the dashboard's state does not expose it."""
    routes = route_repo(github, "maksimtech", "cookieradar",
                        "gh_codecov-mcp_issues_all.json", "gh_codecov-mcp_pulls_all.json",
                        "gh_cookieradar_releases.json")
    _db.open_db(db_path).add_repo("maksimtech", "cookieradar")

    sync_repo("maksimtech", "cookieradar", token=TOKEN, db_path=db_path)

    calls = [call for route in routes.values() for call in route.calls]
    assert calls, "nothing was requested: the test proves nothing"
    assert all(c.request.headers["Authorization"] == f"Bearer {TOKEN}" for c in calls)
    assert _db.open_db(db_path).repo_summary("maksimtech", "cookieradar")["latest_release"]
    assert [p.name for p in db_path.parent.iterdir()] == [db_path.name]
    assert TOKEN.encode() not in db_path.read_bytes()
    poller = web.Poller(interval=3600, token=TOKEN, db_path=db_path)
    assert TOKEN not in json.dumps(web.state(db_path, poller), default=str)


def test_the_database_declared_is_the_one_written(doc, monkeypatch, tmp_path):
    assert _db._DEFAULT_DB.relative_to(Path.home()).as_posix() == ".releaseradar/state.db"
    assert "`~/.releaseradar/state.db`" in doc
    chosen = tmp_path / "elsewhere.db"
    monkeypatch.setenv("RELEASERADAR_DB", str(chosen))
    assert cli._db_path() == chosen
    assert mcp_server._db_path() == chosen
    assert "`RELEASERADAR_DB`" in doc


def test_the_tables_named_are_the_tables_there_are(doc, tmp_path):
    """Every table the database holds is named in the document, and none of them
    has a column for a person: issues and pull requests are stored without
    their author."""
    path = tmp_path / "state.db"
    _db.init(path)
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        columns = {c[1] for t in tables for c in conn.execute(f"PRAGMA table_info({t})")}
    finally:
        conn.close()
    assert set(tables) == {"repos", "issues", "pull_requests", "releases", "events"}
    for table in tables:
        assert f"`{table}`" in doc, f"the document does not name the table {table}"
    for personal in ("user", "author", "login", "email", "token", "assignee"):
        assert not any(personal in c for c in columns), f"a column holds {personal}"


def test_the_database_is_a_plain_sqlite_file_and_there_is_no_export_command(doc, tmp_path):
    """Question 7: the data can be taken away because the file is SQLite 3, an
    open format, unencrypted. There is no export command; the document says so,
    and goes red if one appears without being described."""
    path = tmp_path / "state.db"
    database = _db.open_db(path)
    repo_id = database.add_repo("maksimtech", "cookieradar")
    database.add_event(repo_id, "release_published", ref_tag="v2026.44")
    assert path.read_bytes()[:16] == b"SQLite format 3\x00"
    # The dump the document points to: Python's own sqlite3 module, no extra tool.
    conn = sqlite3.connect(path)
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert 'INSERT INTO "repos"' in dump and "cookieradar" in dump
    assert 'INSERT INTO "events"' in dump
    commands = set(command_names(PACKAGE / "cli.py"))
    assert commands == {"track", "untrack", "list", "sync", "status", "events", "notify", "web", "mcp"}
    text = prose(doc)
    assert "SQLite" in text
    assert "iterdump()" in text
    assert "non c'è un comando di esportazione" in text.lower()
    assert "notify --json --dry-run" in text


def test_nothing_narrows_or_widens_the_database_permissions(doc):
    """The document says ReleaseRadar does not restrict the file's permissions
    and does not encrypt it: no chmod, no cipher, the standard sqlite3 module."""
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "chmod" not in source, f"{path.name} changes permissions"
        assert "sqlcipher" not in source.lower()
    assert "import sqlite3" in (PACKAGE / "db.py").read_text(encoding="utf-8")
    text = prose(doc)
    assert "non è cifrato" in text
    assert "non ne restringe i permessi" in text


# --- Question 2: who can see what -------------------------------------------


def test_the_dashboard_listens_where_the_document_says_and_nowhere_else(doc):
    assert web.LOOPBACK == "127.0.0.1"
    assert "host" not in inspect.signature(web.serve).parameters
    assert "host" not in inspect.signature(cli.web).parameters
    assert "DashboardServer(LOOPBACK," in (PACKAGE / "web.py").read_text(encoding="utf-8")
    assert inspect.signature(cli.web).parameters["every"].default == 15
    assert inspect.signature(cli.web).parameters["port"].default == 8765
    text = prose(doc)
    assert "127.0.0.1" in text
    assert "8765" in text
    assert "non ha autenticazione" in text
    assert "ogni 15 minuti" in text


def test_the_dashboard_checks_neither_host_nor_origin(doc):
    """The document tells the customer that any page open in a browser on the
    same machine can send the dashboard its sync request, because the handler
    reads neither the Host nor the Origin header. If a check is added, this goes
    red and the sentence comes out."""
    handler = inspect.getsource(web._Handler)
    assert "Origin" not in handler and "Host" not in handler and "self.headers" not in handler
    assert "POST /api/sync" in doc
    assert "non controlla le intestazioni `Host` e `Origin`" in prose(doc)


def test_the_dashboard_keeps_no_access_log(doc, capsys):
    """The stdlib server writes a line per request to stderr; this one does not,
    and the document says there is no access log."""
    web._Handler.log_message(None, "%s", "GET /api/state")  # type: ignore[arg-type]
    assert capsys.readouterr().err == ""
    assert "log di accesso" in prose(doc)


def test_the_dashboard_page_loads_nothing_from_elsewhere():
    """The document says the page loads no external resource: no script, font
    or stylesheet from a CDN. The links to GitHub it shows come from the
    database and are opened only by a click."""
    assert not re.search(r"https?://", web.PAGE)
    assert not re.search(r"\b(?:src|href)\s*=\s*[\"']//", web.PAGE)


def test_the_mcp_server_speaks_stdio_only(doc):
    source = (PACKAGE / "mcp_server.py").read_text(encoding="utf-8")
    assert 'server.run("stdio")' in source
    for transport in ("streamable-http", "sse", "streamable_http"):
        assert transport not in source
    assert "stdio" in doc


def test_the_image_runs_as_the_user_the_document_names(doc):
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    user = re.findall(r"^USER\s+(\S+)", dockerfile, re.M)[-1]
    uid = re.search(rf"useradd\b[^\n]*-u\s+(\d+)[^\n]*\b{re.escape(user)}\b", dockerfile)
    assert user not in {"root", "0"}
    assert uid, f"the Dockerfile does not create {user} with an explicit UID"
    assert f"UID {uid.group(1)}" in doc
    assert f"`{user}`" in doc


def test_the_image_publishes_no_port(doc):
    """The document says the Dockerfile exposes no port, and that the database
    lives in the image's volume."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert not re.search(r"^EXPOSE\b", dockerfile, re.M)
    assert "`EXPOSE`" in doc
    assert 'VOLUME ["/home/releaseradar/.releaseradar"]' in dockerfile
    assert "`/home/releaseradar/.releaseradar`" in doc


# --- Questions 1 and 6: what every release carries ---------------------------


@pytest.fixture(scope="module")
def evidence() -> dict:
    return yaml.safe_load(EVIDENCE.read_text(encoding="utf-8"))


def test_the_evidence_follows_the_image_of_a_release(evidence):
    """The image SBOM and its scan describe the image of the tag, so the job
    runs when that image has been pushed — not when the tag is, when it does not
    exist yet."""
    triggers = evidence.get(True, evidence.get("on"))
    docker_name = yaml.safe_load(DOCKER.read_text(encoding="utf-8"))["name"]
    assert triggers["workflow_run"]["workflows"] == [docker_name]
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert "tag" in triggers["workflow_dispatch"]["inputs"]
    job = next(iter(evidence["jobs"].values()))
    assert "conclusion == 'success'" in job["if"]
    assert "startsWith(github.event.workflow_run.head_branch, 'v')" in job["if"]


JOB_ENV_CONTEXTS = {"github", "inputs", "matrix", "needs", "secrets", "strategy", "vars"}


def test_the_job_env_uses_only_contexts_github_allows_there(evidence):
    """`runner` is not available in a job's `env`, and GitHub refuses the whole
    file when it finds it: mailradar's first dispatch of this workflow, on
    2026-10-11, answered HTTP 422, "Unrecognized named-value: 'runner'"."""
    for job in evidence["jobs"].values():
        for name, value in (job.get("env") or {}).items():
            for expression in re.findall(r"\$\{\{(.*?)\}\}", str(value)):
                used = set(re.findall(r"(?<![\w.])([a-z_]+)\.", expression))
                assert used <= JOB_ENV_CONTEXTS, f"env {name} uses {sorted(used - JOB_ENV_CONTEXTS)}"


def test_the_image_tag_named_is_the_one_docker_yml_pushes(doc):
    """docker.yml pushes the git tag as it is, v included: Docker Hub listed
    v2026.1 to v2026.5 and latest on 2026-10-11, and no tag without the v."""
    docker = DOCKER.read_text(encoding="utf-8")
    assert re.search(r"VERSION=(?:\$\{RAW\}|v\$\{RAW#v\})", docker)
    assert "format('" + IMAGE + ":{0}', steps.version.outputs.VERSION)" in docker
    for name, text in (("the document", doc), ("SECURITY.md", SECURITY.read_text(encoding="utf-8"))):
        assert f"{IMAGE}:v" in text, f"{name} does not name the image by its v tag"
        assert not re.search(rf"{re.escape(IMAGE)}:(?:\d|<)", text), f"{name} names a tag docker.yml never pushes"
    evidence = EVIDENCE.read_text(encoding="utf-8")
    assert '"registry:${IMAGE}:${TAG}"' in evidence
    assert "image-ref: ${{ env.IMAGE }}:${{ env.TAG }}" in evidence


def test_the_weekly_rebuild_leaves_the_version_tags_alone(doc):
    """The scheduled run pushes `latest` only, so the tag a customer pins to
    roll back keeps pointing at the image of that release."""
    docker = DOCKER.read_text(encoding="utf-8")
    assert "github.event_name != 'schedule' && format('" + IMAGE in docker
    assert re.search(r"cron:\s*'[^']+'", docker)
    assert "non sovrascrivono" in prose(doc)


def step(evidence: dict, name: str) -> str:
    job = next(iter(evidence["jobs"].values()))
    return next(s["run"] for s in job["steps"] if s.get("name") == name)


def test_the_evidence_note_gives_the_digest_and_not_the_tag_twice(evidence):
    """Syft puts the tag in metadata.component.version, and mailradar's first
    EVIDENCE note, on v2026.44, read "digest v2026.44". The digest a customer pins
    comes from Docker Hub, which gives the index digest and one per platform."""
    describe = step(evidence, "Describe the evidence")
    assert "sbom-image" not in describe.split("cat >")[0], "the note reads the image SBOM before writing"
    assert '["metadata"]["component"]' not in describe
    assert "https://hub.docker.com/v2/repositories/${IMAGE}/tags/${TAG}" in describe


def test_the_source_sbom_says_which_version_it_describes(evidence):
    """The version is dynamic in pyproject.toml, so cyclonedx-py names the main
    component and leaves its version out: the first sbom-source attached to
    v2026.5, on 2026-10-11, said "releaseradar" and no version. The workflow
    writes the version of the package it installed, and refuses a tag that
    disagrees with it."""
    sbom = step(evidence, "SBOM of the Python environment")
    assert "importlib.metadata" in sbom and "releaseradar" in sbom
    assert '!= "$VERSION"' in sbom or "!= \"${VERSION}\"" in sbom
    assert '["version"] =' in sbom


def test_the_source_sbom_leaves_out_what_the_image_removes(evidence):
    """The Dockerfile uninstalls pip, and the sbom-source attached to v2026.5 on
    2026-10-11 still listed pip 25.0.1: the venv's own copy, which the image does
    not ship. The environment described drops it the same way."""
    assert "pip uninstall pip -y" in DOCKERFILE.read_text(encoding="utf-8")
    sbom = step(evidence, "SBOM of the Python environment")
    install = sbom.index('pip install --quiet ".[mcp]"')
    uninstall = sbom.index("/tmp/product/bin/python -m pip uninstall --yes --quiet pip")
    describe = sbom.index("cyclonedx-py environment")
    assert install < uninstall < describe


def test_an_empty_repository_scan_is_not_presented_as_a_clean_one(evidence, doc):
    """The trivy-fs report attached to v2026.5 has no Results at all: that tag
    has no lockfile, so Trivy found nothing it could analyse. An empty report
    reads as "no vulnerabilities" to whoever opens it; the note says what it is."""
    describe = step(evidence, "Describe the evidence")
    assert "Results" in describe
    assert "found no file it could analyse" in describe
    assert "non indica l'assenza di vulnerabilità" in prose(doc)


def test_the_source_sbom_names_the_package_and_the_extra_the_image_ships():
    """Without --pyproject, cyclonedx-py lists the environment and names no main
    component. And the image installs the [mcp] extra, so the environment the
    source SBOM describes has to hold it too."""
    evidence = EVIDENCE.read_text(encoding="utf-8")
    assert "--pyproject pyproject.toml --mc-type application" in evidence
    assert "[mcp]" in DOCKERFILE.read_text(encoding="utf-8")
    assert 'pip install --quiet ".[mcp]"' in evidence


def test_the_evidence_job_pins_its_actions():
    for line in EVIDENCE.read_text(encoding="utf-8").splitlines():
        if "uses:" in line:
            ref = line.split("uses:", 1)[1].split("#")[0].strip()
            assert re.search(r"@[0-9a-f]{40}$", ref), f"not pinned to a commit: {line.strip()}"


def test_the_release_carries_the_files_the_document_promises(doc):
    promised = table_first_column(section(doc, "assets"))
    workflow = EVIDENCE.read_text(encoding="utf-8")
    assert len(promised) == 5, "the document promises a different set of release files"
    for name in promised:
        stem = name.split("<")[0].rstrip("-")
        assert stem in workflow, f"{name} is promised but sbom.yml never writes {stem}"
    assert "gh release upload" in workflow


def test_the_scan_is_not_presented_as_a_penetration_test(doc):
    """Question 6 asks for a penetration test. There has been none, and a scan of
    known CVEs is not one: the release note that travels with the files says so."""
    assert "non è un penetration test" in prose(doc)
    assert "not a penetration test" in EVIDENCE.read_text(encoding="utf-8")


# --- Questions 4 and 5: policy and pipeline ----------------------------------

SCANNERS = {
    "CodeQL": "codeql",
    "Bandit": "bandit",
    "Trivy": "trivy",
    "Snyk": "snyk",
    "Docker Scout": "docker-scout",
    "SonarCloud": "sonarcloud",
    "Security Posture": "security-posture",
    "mutmut": "mutation",
}


def test_every_scanner_named_has_its_workflow_and_none_is_left_out(doc):
    named = table_first_column(section(doc, "scanners"))
    assert named <= set(SCANNERS), f"unknown scanner names: {sorted(named - set(SCANNERS))}"
    for name in named:
        assert (WORKFLOWS / f"{SCANNERS[name]}.yml").is_file(), f"{name} has no workflow"
    present = {n for n, w in SCANNERS.items() if (WORKFLOWS / f"{w}.yml").is_file()}
    assert present <= named, f"workflows the document does not name: {sorted(present - named)}"


def test_the_posture_gate_is_described_as_reading_main(doc):
    """Security Posture lists the open alerts without a `ref`, and GitHub then
    answers for the default branch: on a pull request it checks main's alerts,
    not the ones the pull request adds. On PR #11, on 2026-10-11, it passed, and
    CodeQL then reported a new high alert on the PR's merge ref."""
    posture = (WORKFLOWS / "security-posture.yml").read_text(encoding="utf-8")
    call = next(line for line in posture.splitlines() if "code-scanning/alerts?state=open" in line)
    assert "ref=" not in call
    assert "avvisi aperti sul ramo `main`" in prose(doc)
    assert "open alerts on `main`" in prose(SECURITY.read_text(encoding="utf-8"))


def test_security_md_answers_the_fourth_question():
    text = SECURITY.read_text(encoding="utf-8")
    for heading in (
        "## Reporting a vulnerability",
        "## How vulnerabilities are found",
        "## Response times",
        "## Customer notification",
        "## Rollback",
    ):
        assert heading in text, f"SECURITY.md has no {heading!r}"
    assert "single maintainer" in text
    assert "Massimo Moretti" in text


def test_the_document_and_security_md_give_the_same_contact_and_no_other(doc):
    """One address for reports, and no personal address published as evidence."""
    for text in (doc, SECURITY.read_text(encoding="utf-8")):
        assert set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)) == {"security@maksimtech.com"}


def test_the_document_does_not_claim_a_second_review(doc):
    """The repository has one maintainer, its ruleset asks for zero approvals and
    lets the maintainer bypass it. Question 5 asks for a second reviewer; the
    honest answer is that there is none."""
    text = prose(doc)
    assert "manutentore unico" in text
    assert "nessuna revisione obbligatoria da parte di una seconda persona" in text


def test_the_readme_points_to_the_policy_and_the_answers():
    readme = README.read_text(encoding="utf-8")
    security = readme.split("## Security", 1)
    assert len(security) == 2, "the README has no Security section"
    assert "(SECURITY.md)" in security[1]
    assert "(docs/nis2-compliance.md)" in security[1]


FORBIDDEN = [
    re.compile(r"NIS2[- ](?:ready|compliant|certified)", re.I),
    re.compile(r"certificat\w* NIS2", re.I),
    re.compile(r"nessun dato (?:è )?inviato a terzi", re.I),
    re.compile(r"no data (?:is )?sent to third parties", re.I),
    re.compile(r"ogni PR richiede", re.I),
]


@pytest.mark.parametrize("path", [DOC, SECURITY, README], ids=lambda p: p.name)
def test_no_claim_this_repository_cannot_back(path):
    text = path.read_text(encoding="utf-8")
    for pattern in FORBIDDEN:
        assert not pattern.search(text), f"{path.name} says {pattern.search(text).group(0)!r}"


# --- Question 1: the licence gate the document cites --------------------------


def test_the_licence_gate_matches_the_names_packages_actually_declare():
    """Without --partial-match, pip-licenses fails only on a licence string equal
    to "GPL", and no package declares that: they declare classifiers such as
    "GNU Lesser General Public License v2 or later (LGPLv2+)". Measured in
    mailradar on 2026-10-11 with chardet 5.2.0 installed: `--fail-on="GPL;AGPL;LGPL"`
    exited 0, the same with --partial-match exited 1."""
    licences = (WORKFLOWS / "licenses.yml").read_text(encoding="utf-8")
    gate = next(line for line in licences.splitlines() if "--fail-on" in line)
    assert "--partial-match" in gate


# --- The lockfile the document points to ------------------------------------


def test_quality_keeps_the_lockfile_in_step_with_pyproject(doc):
    """ReleaseRadar had no uv.lock until 2026-10-11. A lockfile shown to a
    customer as the list of dependencies has to be the current one, so the check
    that keeps it current came with it."""
    assert (ROOT / "uv.lock").is_file()
    quality = (WORKFLOWS / "quality.yml").read_text(encoding="utf-8")
    assert re.search(r"uv lock --check", quality)
    assert "`uv.lock`" in doc


def test_httpx_is_what_the_document_says_reaches_github():
    """The one network library the package imports is httpx: the user agent the
    document names is that library's."""
    imported = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
    assert {"requests", "urllib3", "aiohttp", "socket"}.isdisjoint(imported)
    assert "httpx" in imported
