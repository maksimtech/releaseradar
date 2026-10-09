"""ReleaseRadar CLI — track GitHub repo lifecycles from issue to release."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from . import __version__
from . import db as _db
from . import github as gh
from .sync import sync_all, sync_repo

app = typer.Typer(
    name="releaseradar",
    help="Track GitHub repo lifecycles — from issue open to release published.",
    no_args_is_help=True,
)
console = Console()

_TOKEN_ENV = "GITHUB_TOKEN"


def _token() -> str | None:
    return os.environ.get(_TOKEN_ENV)


def _db_path() -> Path:
    path = os.environ.get("RELEASERADAR_DB")
    return Path(path) if path else _db._DEFAULT_DB


# ── version ──────────────────────────────────────────────────────────────────

def _version_callback(value: bool) -> None:
    if value:
        rprint(f"[bold]releaseradar[/bold] {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
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
    if "/" not in repo:
        rprint("[red]Error:[/red] repo must be in owner/repo format.")
        raise typer.Exit(1)
    owner, name = repo.split("/", 1)

    with console.status(f"Checking {owner}/{name}…"):
        if not gh.repo_exists(owner, name, _token()):
            rprint(f"[red]Error:[/red] {owner}/{name} not found or not accessible.")
            raise typer.Exit(1)

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
    if "/" not in repo:
        rprint("[red]Error:[/red] repo must be in owner/repo format.")
        raise typer.Exit(1)
    owner, name = repo.split("/", 1)
    database = _db.open_db(_db_path())
    if database.remove_repo(owner, name):
        rprint(f"[yellow]✓[/yellow] Stopped tracking [bold]{owner}/{name}[/bold]")
    else:
        rprint(f"[red]Error:[/red] {owner}/{name} was not being tracked.")
        raise typer.Exit(1)


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
    table.add_column("Tracked since")

    for row in repos:
        summary = database.repo_summary(row["owner"], row["name"])
        latest = summary.get("latest_release")
        table.add_row(
            f"{row['owner']}/{row['name']}",
            str(summary.get("open_issues", "—")),
            str(summary.get("open_prs", "—")),
            latest["tag_name"] if latest else "[dim]none yet[/dim]",
            row["added_at"][:10],
        )
    console.print(table)


# ── sync ──────────────────────────────────────────────────────────────────────

@app.command()
def sync(
    repo: Annotated[
        Optional[str],
        typer.Argument(help="owner/repo to sync (omit to sync all)"),
    ] = None,
) -> None:
    """Fetch the latest state from GitHub and record any changes."""
    if repo:
        if "/" not in repo:
            rprint("[red]Error:[/red] repo must be in owner/repo format.")
            raise typer.Exit(1)
        owner, name = repo.split("/", 1)
        with console.status(f"Syncing {owner}/{name}…"):
            events = sync_repo(owner, name, token=_token(), db_path=_db_path())
        _print_events({f"{owner}/{name}": events})
    else:
        database = _db.open_db(_db_path())
        repos = database.list_repos()
        if not repos:
            rprint("[dim]Nothing to sync. Add a repo with [bold]releaseradar track[/bold].[/dim]")
            return
        with console.status("Syncing all tracked repos…"):
            results = sync_all(token=_token(), db_path=_db_path())
        _print_events(results)


def _print_events(results: dict[str, list[dict]]) -> None:
    total = sum(len(v) for v in results.values())
    if total == 0:
        rprint("[dim]Nothing new.[/dim]")
        return

    _ICONS = {
        "issue_opened": "🐛",
        "issue_closed": "✅",
        "pr_opened": "🔀",
        "pr_merged": "🟣",
        "pr_closed": "❌",
        "release_published": "🚀",
    }

    for repo_key, events in results.items():
        if not events:
            continue
        rprint(f"\n[bold cyan]{repo_key}[/bold cyan]")
        for e in events:
            icon = _ICONS.get(e["kind"], "•")
            if e["kind"] == "release_published":
                rprint(f"  {icon} [bold green]{e['tag']}[/bold green] — {e['name']}")
            else:
                num = e.get("number", "")
                rprint(f"  {icon} #{num} {e['title'][:72]}")


# ── status ────────────────────────────────────────────────────────────────────

@app.command()
def status(
    repo: Annotated[str, typer.Argument(help="owner/repo to inspect")],
) -> None:
    """Show open issues, PRs and latest release for a tracked repo."""
    if "/" not in repo:
        rprint("[red]Error:[/red] repo must be in owner/repo format.")
        raise typer.Exit(1)
    owner, name = repo.split("/", 1)
    database = _db.open_db(_db_path())

    summary = database.repo_summary(owner, name)
    if not summary:
        rprint(f"[red]{owner}/{name}[/red] is not being tracked.")
        raise typer.Exit(1)

    rprint(f"\n[bold]{owner}/{name}[/bold]")
    rprint(f"  Open issues : [yellow]{summary['open_issues']}[/yellow]")
    rprint(f"  Open PRs    : [blue]{summary['open_prs']}[/blue]")
    if summary["latest_release"]:
        rel = summary["latest_release"]
        rprint(f"  Latest      : [green]{rel['tag_name']}[/green] ({rel['published_at'][:10]})")
    else:
        rprint("  Latest      : [dim]no release yet[/dim]")


# ── events ────────────────────────────────────────────────────────────────────

@app.command()
def events(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of events to show")] = 20,
    pending: Annotated[bool, typer.Option("--pending", help="Show only unnotified events")] = False,
) -> None:
    """Show the event log."""
    database = _db.open_db(_db_path())
    if pending:
        rows = database.pending_events()
    else:
        import sqlite3
        with _db._connect(database.path) as conn:
            rows = conn.execute(
                """SELECT e.*, r.owner, r.name
                   FROM events e JOIN repos r ON r.id = e.repo_id
                   ORDER BY e.seen_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()

    if not rows:
        rprint("[dim]No events.[/dim]")
        return

    table = Table(show_header=True)
    table.add_column("When", style="dim")
    table.add_column("Repo", style="cyan")
    table.add_column("Event")
    table.add_column("Ref")
    table.add_column("N", justify="center")

    for r in rows:
        notified = "✓" if r["notified"] else "○"
        ref = r["ref_tag"] or (f"#{r['ref_number']}" if r["ref_number"] else "—")
        table.add_row(
            r["seen_at"][:16],
            f"{r['owner']}/{r['name']}",
            r["kind"],
            ref,
            notified,
        )
    console.print(table)
