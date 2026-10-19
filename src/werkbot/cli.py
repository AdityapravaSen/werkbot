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
from werkbot.config import Settings, get_settings
from werkbot.dedup.normalize import load_aliases
from werkbot.dedup.store import save_jobs
from werkbot.models import Job, Source, SourceGroup
from werkbot.sources import load_sources

app = typer.Typer(help="Find English-speaking Werkstudent jobs and tailor your applications.")


def _utf8_streams() -> None:
    """Windows consoles and pipes default to cp1252, which can't print many job titles
    (e.g. Chinese, or arrows). Switch to UTF-8 and never crash on an odd character."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_streams()
console = Console()


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every HTTP request."),
) -> None:
    """Runs before every command: set up logging to the screen and to a daily log file."""
    logger.remove()
    logger.add(
        sys.stderr, level="DEBUG" if verbose else "INFO", format="{time:HH:mm:ss} | {message}"
    )
    log_dir = get_settings().output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "werkbot_{time:YYYY-MM-DD}.log",
        level="DEBUG",  # the file always has full detail, including every request
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message}",
        encoding="utf-8",
        retention="14 days",
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
    save: bool = typer.Option(True, "--save/--no-save", help="Merge duplicates into the DB."),
) -> None:
    """Scrape enabled sources, save raw results to output/raw/, merge them into the DB."""
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

    summary: list[tuple[str, str, str]] = []  # (source, status, detail)

    for s in selected:
        adapter_cls = REGISTRY.get(s.adapter)
        if adapter_cls is None:
            console.print(f"[yellow]skip[/yellow] {s.name}: adapter '{s.adapter}' not built yet")
            summary.append((s.name, "skip", "adapter not built"))
            continue
        adapter = adapter_cls(s, settings)
        try:
            jobs = adapter.fetch()
            out = raw_dir / f"{s.name}.json"
            out.write_text(
                json.dumps([j.model_dump(mode="json") for j in jobs], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # one broken site must not stop the others
            failed += 1
            logger.exception("{} failed", s.name)  # full traceback goes to the log file
            console.print(f"[red]fail[/red]    {s.name}: {type(exc).__name__}: {exc}")
            summary.append((s.name, "fail", f"{type(exc).__name__}: {exc}"[:80]))
            continue
        all_jobs.extend(jobs)
        status = "partial" if adapter.warnings else "ok"
        color = "yellow" if adapter.warnings else "green"
        console.print(f"[{color}]{status:<7}[/{color}] {s.name}: {len(jobs)} jobs -> {out}")
        for warning in adapter.warnings:
            console.print(f"          [yellow]- {warning}[/yellow]")
        summary.append((s.name, status, f"{len(jobs)} jobs"))
        logger.info("RESULT {}: {} ({} jobs) {}", s.name, status, len(jobs), adapter.warnings or "")

    if summary:
        table = Table("Source", "Status", "Result")
        colors = {"ok": "green", "partial": "yellow", "fail": "red", "skip": "dim"}
        for name, status, detail in summary:
            table.add_row(name, f"[{colors[status]}]{status}[/{colors[status]}]", escape(detail))
        console.print()
        console.print(table)
    console.print(f"\n[bold]{len(all_jobs)} jobs total[/bold], {failed} source(s) failed")
    logger.info("TOTAL {} jobs from {} sources, {} failed", len(all_jobs), len(summary), failed)
    db_ok = _save_to_db(all_jobs, settings) if save else True

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
    console.print(f"[dim]Full log: {settings.output_dir / 'logs'}[/dim]")
    if not db_ok:
        raise typer.Exit(1)


@app.command()
def dedup(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Only show what would be merged; write nothing."
    ),
    groups: int = typer.Option(10, "--groups", help="Show up to N merged groups."),
) -> None:
    """Merge the jobs in output/raw/*.json into the database (no scraping)."""
    settings = get_settings()
    known = {s.name: s for s in load_sources(settings.sources_path, include_disabled=True)}
    jobs: list[Job] = []
    for path in sorted((settings.output_dir / "raw").glob("*.json")):
        source = known.get(path.stem)
        if source is None:
            console.print(f"[yellow]skip[/yellow] {path.name}: not in sources.yaml")
            continue
        if source.group == SourceGroup.WATCH:
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        jobs += [Job.model_validate(row) for row in rows]
        console.print(f"read {path.name}: {len(rows)} jobs")
    if not jobs:
        console.print("[yellow]No raw results found. Run: werkbot scrape[/yellow]")
        raise typer.Exit(1)
    if not _save_to_db(jobs, settings, dry_run=dry_run, show_groups=groups):
        raise typer.Exit(1)


def _save_to_db(
    jobs: list[Job], settings: Settings, dry_run: bool = False, show_groups: int = 0
) -> bool:
    """Merge duplicates, save, print the counts. False if the duplicate check fails."""
    sources = {s.name: s for s in load_sources(settings.sources_path, include_disabled=True)}
    jobs = [j for j in jobs if not _is_page_watch(j, sources)]  # alerts, not job postings
    kinds = {name: s.kind for name, s in sources.items()}
    db.init_db(settings.db_path)
    conn = db.connect(settings.db_path)
    try:
        result = save_jobs(
            conn,
            jobs,
            kinds,
            aliases=load_aliases(settings.company_aliases_path),
            expire_after_days=settings.expire_after_days,
            dry_run=dry_run,
            boards={name: s.adapter for name, s in sources.items()},  # one site, one board
        )
        problems = {} if dry_run else db.find_duplicates(conn)
    finally:
        conn.close()

    merged = result.received - result.groups
    console.print(
        f"\n[bold]Database{' (dry run, nothing saved)' if dry_run else ''}:[/bold] "
        f"{result.received} jobs -> {result.groups} after merging {merged} duplicates | "
        f"[green]{result.new} new[/green], {result.seen_again} seen before "
        f"({result.reposted} reposted), {result.links_added} links added, "
        f"{result.expired} expired"
    )
    logger.debug(  # the screen already shows this; the log file keeps it
        "DB {} jobs, {} merged in run, {} new, {} seen before, {} reposted, {} expired{}",
        result.received,
        merged,
        result.new,
        result.seen_again,
        result.reposted,
        result.expired,
        " (dry run)" if dry_run else "",
    )
    for members, reasons in result.merged_groups[:show_groups]:
        table = Table("Source", "Title", "Company", "Location", show_header=False, box=None)
        for j in members:
            table.add_row(j.source, escape(j.title), escape(j.company), escape(j.location or "?"))
        console.print(table)
        console.print(f"  [dim]{escape('; '.join(reasons))}[/dim]")
    if show_groups and len(result.merged_groups) > show_groups:
        console.print(f"  [dim]... and {len(result.merged_groups) - show_groups} more groups[/dim]")

    if problems:
        for name, rows in problems.items():
            console.print(f"[red]Duplicate check failed - {name}:[/red] {rows[:5]}")
            logger.error("Duplicate check failed - {}: {}", name, rows[:20])
        return False
    return True


def _is_page_watch(job: Job, sources: dict[str, Source]) -> bool:
    source = sources.get(job.source)
    return source is not None and source.group == SourceGroup.WATCH


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
