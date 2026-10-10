"""ReleaseRadar CLI — track GitHub repo lifecycles from issue to release."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from . import __version__
from . import db as _db
from . import github as gh
from .sync import NotTracked, sync_all, sync_repo

app = typer.Typer(
    name="releaseradar",
    help="Track GitHub repo lifecycles — from issue open to release published.",
    no_args_is_help=True,
)
console = Console()

_TOKEN_ENV = "GITHUB_TOKEN"

# Printed through rich, where `[mcp]` is a style tag and disappears: the one
# install command in the message is the one that would vanish without escaping.
MCP_MISSING = r"the MCP server needs the `mcp` package: pip install 'releaseradar\[mcp]'."

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


def _fail(message: str) -> None:
    rprint(f"[red]Error:[/red] {message}")
    raise typer.Exit(1)


def _split(repo: str) -> tuple[str, str]:
    owner, sep, name = repo.strip().partition("/")
    if not sep or not owner or not name or "/" in name:
        _fail("repo must be in owner/repo format, e.g. torvalds/linux.")
    return owner, name


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
    repo: Annotated[str, typer.Argument(help="owner/repo to stop tracking")],
) -> None:
    """Stop tracking a repository and remove all its data."""
    owner, name = _split(repo)
    database = _db.open_db(_db_path())
    if database.remove_repo(owner, name):
        rprint(f"[yellow]✓[/yellow] Stopped tracking [bold]{owner}/{name}[/bold]")
    else:
        _fail(f"{owner}/{name} was not being tracked.")


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

    for row in repos:
        summary = database.repo_summary(row["owner"], row["name"])
        latest = summary.get("latest_release")
        synced = row["last_synced_at"]
        table.add_row(
            f"{row['owner']}/{row['name']}",
            str(summary.get("open_issues", "—")),
            str(summary.get("open_prs", "—")),
            latest["tag_name"] if latest else "[dim]none yet[/dim]",
            synced[:16].replace("T", " ") if synced else "[dim]never[/dim]",
        )
    console.print(table)


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
            with console.status(f"Syncing {owner}/{name}…"):
                events = sync_repo(owner, name, token=_token(), db_path=_db_path(), on_warning=warnings.append)
        except (NotTracked, gh.GitHubError) as exc:
            _fail(str(exc))
        _print_warnings(warnings)
        _print_events({f"{owner}/{name}": events})
        return

    database = _db.open_db(_db_path())
    if not database.list_repos():
        rprint("[dim]Nothing to sync. Add a repo with [bold]releaseradar track[/bold].[/dim]")
        return
    with console.status("Syncing all tracked repos…"):
        results, errors = sync_all(token=_token(), db_path=_db_path(), on_warning=warnings.append)
    _print_warnings(warnings)
    for key, message in errors.items():
        rprint(f"[red]✗ {key}:[/red] {message}")
    # "Nothing new" is about the repositories that were synced; with none of them
    # it would be said about the failures.
    if results:
        _print_events(results)
    if errors:
        raise typer.Exit(1)


def _print_warnings(warnings: list[str]) -> None:
    for message in warnings:
        rprint(f"[yellow]![/yellow] {message}")


def _print_events(results: dict[str, list[dict]]) -> None:
    total = sum(len(v) for v in results.values())
    if total == 0:
        rprint("[dim]Nothing new.[/dim]")
        return

    for repo_key, events in results.items():
        if not events:
            continue
        rprint(f"\n[bold cyan]{repo_key}[/bold cyan]")
        for e in events:
            icon = _ICONS.get(e["kind"], "•")
            if e["kind"] == "release_published":
                rprint(f"  {icon} [bold green]{e['tag']}[/bold green] — {e['name']}")
            else:
                rprint(f"  {icon} #{e.get('number', '')} {e['title'][:72]}")


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
        rprint(f"  Latest      : [green]{rel['tag_name']}[/green] ({rel['published_at'][:10]})")
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
        table.add_row(r["seen_at"][:16], f"{r['owner']}/{r['name']}", r["kind"], ref, notified)
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
