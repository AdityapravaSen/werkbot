"""Command-line interface: `werkbot <command>`."""

import typer
from rich.console import Console
from rich.table import Table

from werkbot import __version__, db
from werkbot.adapters import REGISTRY
from werkbot.config import get_settings
from werkbot.sources import load_sources

app = typer.Typer(help="Find English-speaking Werkstudent jobs and tailor your applications.")
console = Console()


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
    source: str = typer.Option(None, "--source", "-s", help="Run only this source."),
) -> None:
    """Scrape all enabled sources (adapters are added in Phase 1)."""
    settings = get_settings()
    selected = [s for s in load_sources(settings.sources_path) if source in (None, s.name)]
    if not selected:
        console.print(f"[red]No enabled source named {source!r}[/red]")
        raise typer.Exit(1)
    for s in selected:
        adapter_cls = REGISTRY.get(s.adapter)
        if adapter_cls is None:
            console.print(f"[yellow]skip[/yellow] {s.name}: adapter '{s.adapter}' not built yet")
            continue
        jobs = adapter_cls(s, settings).fetch()
        console.print(f"[green]ok[/green]   {s.name}: {len(jobs)} jobs")


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
