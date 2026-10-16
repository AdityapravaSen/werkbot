"""Command-line interface: `werkbot <command>`."""

import json
import sys

import typer
from loguru import logger
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from werkbot import __version__, db
from werkbot.adapters import REGISTRY
from werkbot.config import get_settings
from werkbot.models import Job
from werkbot.sources import load_sources

app = typer.Typer(help="Find English-speaking Werkstudent jobs and tailor your applications.")
console = Console()


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every HTTP request."),
) -> None:
    """Runs before every command: set how much logging is shown."""
    logger.remove()
    logger.add(
        sys.stderr, level="DEBUG" if verbose else "INFO", format="{time:HH:mm:ss} | {message}"
    )


@app.command()
def version() -> None:
    """Show the installed version."""
    console.print(f"werkbot {__version__}")


@app.command("init-db")
def init_db() -> None:
    """Create the SQLite database and tables (safe to run again)."""
    settings = get_settings()
    db.init_db(settings.db_path)
    console.print(f"[green]Database ready:[/green] {settings.db_path}")


@app.command()
def sources(all: bool = typer.Option(False, "--all", help="Include disabled sources.")) -> None:
    """List the configured job sources."""
    settings = get_settings()
    table = Table("Name", "Group", "Adapter", "Enabled", "Adapter built")
    for s in load_sources(settings.sources_path, include_disabled=all):
        built = "yes" if s.adapter in REGISTRY else "[yellow]not yet[/yellow]"
        table.add_row(s.name, s.group.value, s.adapter, "yes" if s.enabled else "no", built)
    console.print(table)


@app.command()
def scrape(
    source: list[str] = typer.Option(  # noqa: B008
        None, "--source", "-s", help="Run only these sources (repeat -s for several)."
    ),
    show: int = typer.Option(0, "--show", help="Print the first N jobs as a table."),
) -> None:
    """Scrape enabled sources and save the raw results to output/raw/<source>.json."""
    settings = get_settings()
    enabled = load_sources(settings.sources_path)
    unknown = set(source or []) - {s.name for s in enabled}
    if unknown:
        console.print(f"[red]No enabled source named: {', '.join(sorted(unknown))}[/red]")
        console.print("See all names with: werkbot sources")
        raise typer.Exit(1)
    selected = [s for s in enabled if not source or s.name in source]

    raw_dir = settings.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    all_jobs: list[Job] = []
    failed = 0

    for s in selected:
        adapter_cls = REGISTRY.get(s.adapter)
        if adapter_cls is None:
            console.print(f"[yellow]skip[/yellow] {s.name}: adapter '{s.adapter}' not built yet")
            continue
        adapter = adapter_cls(s, settings)
        try:
            jobs = adapter.fetch()
        except Exception as exc:  # one broken site must not stop the others
            failed += 1
            console.print(f"[red]fail[/red] {s.name}: {type(exc).__name__}: {exc}")
            continue
        out = raw_dir / f"{s.name}.json"
        out.write_text(
            json.dumps([j.model_dump(mode="json") for j in jobs], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        all_jobs.extend(jobs)
        status = "[yellow]partial[/yellow]" if adapter.warnings else "[green]ok[/green]     "
        console.print(f"{status} {s.name}: {len(jobs)} jobs → {out}")
        for warning in adapter.warnings:
            console.print(f"          [yellow]↳ {warning}[/yellow]")

    console.print(f"\n[bold]{len(all_jobs)} jobs total[/bold], {failed} source(s) failed")

    if show and all_jobs:
        table = Table("Posted", "Title (Ctrl+click to open)", "Company", "Location", "Source")
        for j in all_jobs[:show]:
            posted = j.date_posted.strftime("%Y-%m-%d") if j.date_posted else "?"
            where = "remote" if j.remote else (j.location or "?")
            title = f"[link={j.url}]{escape(j.title)}[/link]"
            table.add_row(posted, title, escape(j.company), escape(where), j.source)
        console.print(table)
        console.print(
            "[dim]All fields incl. links and descriptions: output/raw/<source>.json[/dim]"
        )


@app.command("check-db")
def check_db() -> None:
    """Fail if any duplicates slipped into the database."""
    settings = get_settings()
    with db.connect(settings.db_path) as conn:
        problems = db.find_duplicates(conn)
    if problems:
        for name, rows in problems.items():
            console.print(f"[red]{name}:[/red] {rows[:5]}")
        raise typer.Exit(1)
    console.print("[green]No duplicates found.[/green]")


if __name__ == "__main__":
    app()
