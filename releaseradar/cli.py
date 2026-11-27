"""ReleaseRadar CLI — track GitHub repo lifecycles from issue to release."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich import print as rprint
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from . import db as _db
from . import github as gh
from .sync import UNTRACK_INVALID, NotTracked, name_is_valid, shown_repo, sync_all, sync_repo, untrack_invalid

app = typer.Typer(
    name="releaseradar",
    help="Track GitHub repo lifecycles — from issue open to release published.",
    no_args_is_help=True,
)
console = Console()

_TOKEN_ENV = "GITHUB_TOKEN"

# Printed through rich, where `[mcp]` is a style tag and disappears: the one
# install command in the message is the one that would vanish without escaping.
MCP_MISSING = "the MCP server needs the `mcp` package: pip install 'releaseradar[mcp]'."

_ICONS = {
    "issue_opened": "🐛",
    "issue_closed": "✅",
    "pr_opened": "🔀",
    "pr_merged": "🟣",
    "pr_closed": "❌",
    "release_published": "🚀",
}


def _token() -> str | None:
    return os.environ.get(_TOKEN_ENV)


def _db_path() -> Path:
    path = os.environ.get("RELEASERADAR_DB")
    return Path(path) if path else _db._DEFAULT_DB


def _fail(message: str) -> NoReturn:
    """Exit 1 with `message`, printed as text: it quotes names, GitHub's answers and
    exceptions, none of which is markup (#55)."""
    rprint(f"[red]Error:[/red] {escape(message)}")
    raise typer.Exit(1)


def _split(repo: str, hint: str = "") -> tuple[str, str]:
    """owner/repo by GitHub's rules, or exit 1 saying what is wrong with it."""
    try:
        return gh.split_repo(repo)
    except gh.InvalidRepoName as exc:
        # The refusal quotes the name, and `[/b]` in it would be markup to rich: _fail escapes it.
        _fail(f"{exc} {hint}".rstrip())


# ── version ──────────────────────────────────────────────────────────────────

def _version_callback(value: bool) -> None:
    if value:
        rprint(f"[bold]releaseradar[/bold] {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True,
                     help="Show version and exit."),
    ] = None,
) -> None:
    pass


# ── track ─────────────────────────────────────────────────────────────────────

@app.command()
def track(
    repo: Annotated[str, typer.Argument(help="owner/repo to start tracking (e.g. torvalds/linux)")],
) -> None:
    """Start tracking a GitHub repository."""
    owner, name = _split(repo)
    try:
        with console.status(f"Checking {owner}/{name}…"):
            exists = gh.repo_exists(owner, name, _token())
    except gh.GitHubError as exc:
        _fail(str(exc))
    if not exists:
        _fail(f"{owner}/{name} not found or not accessible.")

    database = _db.open_db(_db_path())
    database.add_repo(owner, name)
    rprint(f"[green]✓[/green] Now tracking [bold]{owner}/{name}[/bold]")
    rprint("  Run [bold]releaseradar sync[/bold] to fetch the current state.")


# ── untrack ───────────────────────────────────────────────────────────────────

@app.command()
def untrack(
    repo: Annotated[str | None, typer.Argument(help="owner/repo to stop tracking")] = None,
    invalid: Annotated[
        bool,
        typer.Option("--invalid", help="Remove every stored repository whose name GitHub could not have given"),
    ] = False,
) -> None:
    """Stop tracking a repository and remove all its data."""
    if invalid and repo:
        _fail("give owner/repo or --invalid, not both.")
    if invalid:
        _untrack_invalid()
        return
    if not repo:
        _fail("give the owner/repo to stop tracking, or --invalid for the stored names GitHub could not have given.")
    owner, name = _split(repo, hint=f"A name an earlier version stored like this is removed with `{UNTRACK_INVALID}`.")
    if not _db.open_db(_db_path()).remove_repo(owner, name):
        _fail(f"{owner}/{name} was not being tracked.")
    rprint(f"[yellow]✓[/yellow] Stopped tracking [bold]{owner}/{name}[/bold]")


def _untrack_invalid() -> None:
    """Earlier versions stored names as typed, `../..` and `psf/requests#x` among
    them, and a NUL or a newline cannot be typed back to remove them by name.
    They are found by the name rules instead, and each one removed is printed
    as its repr, so what was in it is visible and none of it is markup."""
    removed = untrack_invalid(_db_path())
    if not removed:
        rprint("[dim]No invalid repositories: every stored name is one GitHub could have given.[/dim]")
        return
    for shown in removed:
        rprint(f"[yellow]✓[/yellow] Removed {escape(shown)} and all its data")
    noun = "repository" if len(removed) == 1 else "repositories"
    rprint(f"Removed {len(removed)} {noun} whose names GitHub could not have given.")


# ── list ──────────────────────────────────────────────────────────────────────

@app.command(name="list")
def list_repos() -> None:
    """List all tracked repositories."""
    database = _db.open_db(_db_path())
    repos = database.list_repos()
    if not repos:
        rprint("[dim]No repositories tracked yet. Use [bold]releaseradar track owner/repo[/bold].[/dim]")
        return

    table = Table(title="Tracked repositories", show_header=True)
    table.add_column("Repository", style="bold cyan")
    table.add_column("Open issues", justify="right")
    table.add_column("Open PRs", justify="right")
    table.add_column("Latest release")
    table.add_column("Last sync")

    invalid = 0
    for row in repos:
        summary = database.repo_summary(row["owner"], row["name"])
        latest = summary.get("latest_release")
        synced = row["last_synced_at"]
        name = escape(shown_repo(row["owner"], row["name"]))
        if not name_is_valid(row["owner"], row["name"]):
            invalid += 1
            name += " [red](invalid name)[/red]"
        table.add_row(
            name,
            str(summary.get("open_issues", "—")),
            str(summary.get("open_prs", "—")),
            latest["tag_name"] if latest else "[dim]none yet[/dim]",
            synced[:16].replace("T", " ") if synced else "[dim]never[/dim]",
        )
    console.print(table)
    if invalid:
        many = invalid > 1
        rprint(f"[yellow]![/yellow] {invalid} stored {'names are not names' if many else 'name is not a name'} "
               f"GitHub could have given and cannot be synced; remove {'them' if many else 'it'} with "
               f"[bold]{UNTRACK_INVALID}[/bold].")


# ── sync ──────────────────────────────────────────────────────────────────────

@app.command()
def sync(
    repo: Annotated[
        str | None,
        typer.Argument(help="owner/repo to sync (omit to sync all tracked repos)"),
    ] = None,
) -> None:
    """Fetch what changed on GitHub since the last sync and record the events."""
    warnings: list[str] = []
    if repo:
        owner, name = _split(repo)
        try:
            with console.status(f"Syncing {owner}/{name}…") as status:
                events = sync_repo(owner, name, token=_token(), db_path=_db_path(), on_warning=warnings.append,
                                   on_progress=lambda line: status.update(escape(line)))
        except (NotTracked, gh.GitHubError) as exc:
            _fail(str(exc))
        _print_warnings(warnings)
        _print_events({f"{owner}/{name}": events})
        return

    database = _db.open_db(_db_path())
    if not database.list_repos():
        rprint("[dim]Nothing to sync. Add a repo with [bold]releaseradar track[/bold].[/dim]")
        return
    with console.status("Syncing all tracked repos…") as status:
        results, errors = sync_all(token=_token(), db_path=_db_path(), on_warning=warnings.append,
                                   on_progress=lambda line: status.update(escape(line)))
    _print_warnings(warnings)
    for key, message in errors.items():
        rprint(f"[red]✗ {escape(key)}:[/red] {escape(message)}")
    # "Nothing new" is about the repositories that were synced; with none of them
    # it would be said about the failures.
    if results:
        _print_events(results)
    if errors:
        raise typer.Exit(1)


def _print_warnings(warnings: list[str]) -> None:
    for message in warnings:
        rprint(f"[yellow]![/yellow] {escape(message)}")


def _print_events(results: dict[str, list[dict]]) -> None:
    total = sum(len(v) for v in results.values())
    if total == 0:
        rprint("[dim]Nothing new.[/dim]")
        return

    for repo_key, events in results.items():
        if not events:
            continue
        rprint(f"\n[bold cyan]{escape(repo_key)}[/bold cyan]")
        for e in events:
            icon = _ICONS.get(e["kind"], "•")
            if e["kind"] == "release_published":
                # Tags, names and titles come from GitHub, written by anyone (#55)
                rprint(f"  {icon} [bold green]{escape(e['tag'])}[/bold green] — {escape(e['name'])}")
            else:
                rprint(f"  {icon} #{e.get('number', '')} {escape(e['title'][:72])}")


# ── status ────────────────────────────────────────────────────────────────────

@app.command()
def status(
    repo: Annotated[str, typer.Argument(help="owner/repo to inspect")],
) -> None:
    """Show open issues, PRs and latest release for a tracked repo."""
    owner, name = _split(repo)
    database = _db.open_db(_db_path())

    summary = database.repo_summary(owner, name)
    if not summary:
        _fail(f"{owner}/{name} is not being tracked.")

    rprint(f"\n[bold]{owner}/{name}[/bold]")
    rprint(f"  Open issues : [yellow]{summary['open_issues']}[/yellow]")
    rprint(f"  Open PRs    : [blue]{summary['open_prs']}[/blue]")
    if summary["latest_release"]:
        rel = summary["latest_release"]
        rprint(f"  Latest      : [green]{escape(rel['tag_name'])}[/green] ({rel['published_at'][:10]})")
    else:
        rprint("  Latest      : [dim]no release yet[/dim]")
    synced = summary.get("last_synced_at")
    rprint(f"  Last sync   : {synced.replace('T', ' ') if synced else '[dim]never[/dim]'}")


# ── events ────────────────────────────────────────────────────────────────────

def _events_table(rows) -> Table:
    table = Table(show_header=True)
    table.add_column("When", style="dim")
    table.add_column("Repo", style="cyan")
    table.add_column("Event")
    table.add_column("Ref")
    table.add_column("N", justify="center")
    for r in rows:
        notified = "✓" if r["notified"] else "○"
        ref = r["ref_tag"] or (f"#{r['ref_number']}" if r["ref_number"] else "—")
        table.add_row(r["seen_at"][:16], escape(f"{r['owner']}/{r['name']}"), r["kind"], escape(ref), notified)
    return table


@app.command()
def events(
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, help="Number of events to show")] = 20,
    pending: Annotated[bool, typer.Option("--pending", help="Show only unnotified events")] = False,
) -> None:
    """Show the event log, newest first."""
    database = _db.open_db(_db_path())
    rows = database.pending_events() if pending else database.recent_events(limit)
    if not rows:
        rprint("[dim]No events.[/dim]")
        return
    console.print(_events_table(rows))


# ── notify ────────────────────────────────────────────────────────────────────

@app.command()
def notify(
    as_json: Annotated[bool, typer.Option("--json", help="Print the events as a JSON array")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print without marking them notified")] = False,
) -> None:
    """Print the events nobody has been told about yet, then mark them notified.

    Pipe it into whatever delivers: `releaseradar notify --json | ...`. Nothing
    printed means nothing new; the exit code is 0 either way.
    """
    database = _db.open_db(_db_path())
    rows = database.pending_events()
    if not rows:
        if as_json:
            print("[]")
        else:
            rprint("[dim]Nothing to notify.[/dim]")
        return
    if as_json:
        print(json.dumps([
            {
                "id": r["id"], "repo": f"{r['owner']}/{r['name']}", "kind": r["kind"],
                "ref_number": r["ref_number"], "ref_tag": r["ref_tag"], "seen_at": r["seen_at"],
                "payload": json.loads(r["payload"]) if r["payload"] else None,
            }
            for r in rows
        ], indent=2))
    else:
        console.print(_events_table(rows))
    if not dry_run:
        database.mark_notified([int(r["id"]) for r in rows])


# ── web ───────────────────────────────────────────────────────────────────────

@app.command()
def web(
    every: Annotated[float, typer.Option("--every", min=1, help="Minutes between syncs")] = 15,
    port: Annotated[int, typer.Option("--port", min=1, max=65535, help="Port on 127.0.0.1")] = 8765,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open the page in the browser")] = False,
    allow_host: Annotated[
        list[str] | None,
        typer.Option("--allow-host", help="Another Host the page may be reached under (repeatable), "
                                          "e.g. an alias or a tunnel's localhost:9000"),
    ] = None,
) -> None:
    """Open a local dashboard (127.0.0.1 only) that syncs every few minutes and shows what changed."""
    from .web import serve

    if not _db.open_db(_db_path()).list_repos():
        rprint("[dim]Nothing tracked yet. Add a repo with [bold]releaseradar track owner/repo[/bold]; "
               "the page will pick it up at the next sync.[/dim]")
    serve(db_path=_db_path(), token=_token(), every_minutes=every, port=port, open_browser=not no_browser,
          extra_hosts=allow_host or [])


# ── mcp ───────────────────────────────────────────────────────────────────────

@app.command()
def mcp() -> None:
    """Run the MCP server on stdio (for `claude mcp add releaseradar -- releaseradar mcp`)."""
    try:
        from .mcp_server import main as mcp_main
    except ImportError:
        _fail(MCP_MISSING)
    mcp_main()


if __name__ == "__main__":
    app()
