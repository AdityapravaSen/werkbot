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
    filter_jobs: bool = typer.Option(
        True, "--filter/--no-filter", help="Run the filters after saving (werkbot filter)."
    ),
    details: bool = typer.Option(
        True, "--details/--no-details", help="Fetch missing descriptions before filtering."
    ),
    score: bool = typer.Option(
        True, "--score/--no-score", help="Score the jobs after filtering (werkbot score)."
    ),
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
    if save and filter_jobs and db_ok:
        _run_filters(settings, details=details, show=0)
        if score:
            _run_scoring(settings, show=10)

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


@app.command("filter")
def filter_command(
    details: bool = typer.Option(
        True, "--details/--no-details", help="Fetch missing descriptions from job pages first."
    ),
    show: int = typer.Option(20, "--show", help="Print the first N kept jobs."),
    retry_details: bool = typer.Option(
        False, "--retry-details", help="Also retry job pages that failed in the last days."
    ),
) -> None:
    """Apply data/filters.yaml (location, role, language) to the jobs in the database."""
    _run_filters(get_settings(), details=details, show=show, retry=retry_details)


def _run_filters(settings: Settings, details: bool, show: int, retry: bool = False) -> None:
    from datetime import UTC, datetime

    from werkbot.details import fetch_details, jobs_needing_details
    from werkbot.filters.config import load_filter_config
    from werkbot.filters.places import Gazetteer
    from werkbot.filters.run import Filters, export_csv, load_jobs, run_filters, top_reasons

    config = load_filter_config(settings.filters_path)
    sources = {s.name: s for s in load_sources(settings.sources_path, include_disabled=True)}
    try:
        filters = Filters(config, Gazetteer.load(settings.places_path), sources)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Filter settings problem:[/red] {exc}")
        raise typer.Exit(1) from exc

    db.init_db(settings.db_path)
    conn = db.connect(settings.db_path)
    try:
        if details:
            # Only fetch pages for jobs that can still pass (location and role, from the list)
            passing = [j.id for j in load_jobs(conn) if filters.judge(j, False).decision != "drop"]
            rows = jobs_needing_details(conn, config.details, passing, datetime.now(UTC), retry)
            if rows:
                todo = min(len(rows), config.details.max_per_run)
                console.print(
                    f"\nFetching job pages for {todo} of {len(rows)} jobs without a "
                    "description (polite pauses; this takes a few minutes) ..."
                )
                stats = fetch_details(
                    conn,
                    rows,
                    config.details,
                    settings,
                    load_aliases(settings.company_aliases_path),
                )
                console.print(
                    f"Job pages: {stats.ok} descriptions, {stats.no_description} without one, "
                    f"{stats.failed} failed, {stats.companies_found} companies found "
                    f"({stats.merged} merged as duplicates)"
                )
                for why, n in stats.fail_reasons.most_common(6):
                    console.print(f"  [dim]{n} × {escape(why)}[/dim]")
                if stats.skipped_sites:
                    console.print(f"[yellow]Rate-limited, retried next run:[/yellow] "
                                  f"{', '.join(sorted(stats.skipped_sites))}")  # fmt: skip
                logger.info("DETAILS {}", stats)

        counts = run_filters(conn, filters)
        csv_path = settings.output_dir / "filtered.csv"
        export_csv(conn, csv_path)
        console.print(
            f"\n[bold]Filters:[/bold] [green]{counts['keep']} keep[/green], "
            f"[yellow]{counts['review']} review[/yellow], [red]{counts['drop']} drop[/red]"
        )
        logger.info("FILTER {}", dict(counts))
        for decision, color in (("review", "yellow"), ("drop", "red")):
            reasons = top_reasons(conn, decision)
            if reasons:
                text = ", ".join(f"{reason} ({n})" for reason, n in reasons)
                console.print(f"  [{color}]{decision}[/{color}] mostly: {escape(text)}")
        if show:
            _show_kept(conn, show)
        console.print(f"[dim]Every decision with its reasons: {csv_path}[/dim]")
    finally:
        conn.close()


def _show_kept(conn, limit: int) -> None:
    rows = conn.execute(
        """SELECT j.title, j.company, f.places, f.distance_km, f.work_mode, f.german_level,
                  j.main_url
           FROM job_filters f JOIN jobs j ON j.id = f.job_id
           WHERE f.decision = 'keep' AND j.status != 'expired'
           ORDER BY j.first_seen_at DESC, f.distance_km LIMIT ?""",
        (limit,),
    ).fetchall()
    if not rows:
        return
    table = Table("Title (Ctrl+click to open)", "Company", "Where", "Mode", "German")
    for r in rows:
        where = f"{r['places'] or '?'}" + (
            f" ({r['distance_km']:.0f} km)" if r["distance_km"] else ""
        )
        title = f"[link={r['main_url']}]{escape(r['title'])}[/link]"
        table.add_row(title, escape(r["company"]), escape(where), r["work_mode"] or "",
                      r["german_level"] or "")  # fmt: skip
    console.print(table)


@app.command("score")
def score_command(
    show: int = typer.Option(20, "--show", help="Print the N best-scoring jobs."),
    llm: bool = typer.Option(
        True, "--llm/--no-llm", help="Read requirements with the LLM (else skill words only)."
    ),
    embeddings: bool = typer.Option(
        True, "--embeddings/--no-embeddings", help="Use the local embedding model if installed."
    ),
) -> None:
    """Fit score (0-100) for every job that passed the filters, using data/scoring.yaml."""
    _run_scoring(get_settings(), show=show, use_llm=llm, use_embeddings=embeddings)


def _run_scoring(
    settings: Settings, show: int, use_llm: bool = True, use_embeddings: bool = True
) -> None:
    from werkbot.llm.client import LLM, LLMUnavailable
    from werkbot.scoring.config import load_scoring_config
    from werkbot.scoring.embed import EmbeddingsUnavailable, SentenceEmbedder
    from werkbot.scoring.requirements import Extractor
    from werkbot.scoring.resume import ResumeMissing, load_resume
    from werkbot.scoring.score import build_semantic, export_scores_csv, jobs_to_score, score_jobs
    from werkbot.scoring.skills import Vocabulary

    try:
        config = load_scoring_config(settings.scoring_path)
        vocab = Vocabulary.load(settings.skills_path)
        resume = load_resume(settings.resume_path)
    except ResumeMissing as exc:
        console.print(f"\n[yellow]Scoring skipped:[/yellow] {exc}")
        return
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]Scoring settings problem:[/red] {exc}")
        raise typer.Exit(1) from exc

    db.init_db(settings.db_path)
    conn = db.connect(settings.db_path)
    try:
        if not conn.execute("SELECT 1 FROM job_filters LIMIT 1").fetchone():
            console.print("[yellow]No filter results yet. Run: werkbot filter[/yellow]")
            return
        old = conn.execute(
            "SELECT COUNT(*) FROM job_filters WHERE location_decision IS NULL"
        ).fetchone()[0]
        if old:
            console.print(
                f"[yellow]{old} filter results are from before Phase 4[/yellow] (no location / "
                "role penalties for them). Run [bold]werkbot filter[/bold] once, then score again."
            )
        llms = []
        if use_llm and config.llm.enabled:
            # WERKBOT_LLM_MODEL can list several, tried in order ("gemini/...,groq/...")
            for model in [m.strip() for m in settings.llm_model.split(",") if m.strip()]:
                try:
                    llms.append(LLM.from_settings(
                        model, config.llm.requests_per_minute, config.llm.timeout_s
                    ))  # fmt: skip
                except LLMUnavailable as exc:
                    console.print(f"[yellow]Skipping LLM:[/yellow] {exc}")
            if not llms:
                console.print("[yellow]No LLM:[/yellow] using skill words only.")
        semantic = None
        if use_embeddings:
            try:
                embedder = SentenceEmbedder(config.semantic.model, config.semantic.prefix)
                semantic = build_semantic(embedder, resume, vocab, config)
                console.print(
                    f"[dim]Semantic scale ({config.semantic.model}): unrelated ads "
                    f"{semantic.scale.low:.3f} = 0, ideal ad {semantic.scale.high:.3f} = 1[/dim]"
                )
            except EmbeddingsUnavailable as exc:
                console.print(f"[yellow]No semantic part:[/yellow] {exc}")
        extractor = Extractor(conn, vocab, config.llm, llms)
        todo = len(jobs_to_score(conn))
        if llms:
            names = ", then ".join(m.model for m in llms)
            console.print(
                f"\nScoring {todo} jobs (new ads are read by {names}; at most "
                f"{config.llm.requests_per_minute:g} per minute and {config.llm.max_per_run} "
                "per run, best candidates first) ..."
            )
        stats = score_jobs(conn, resume, vocab, config, extractor, semantic)
        csv_path = settings.output_dir / "scores.csv"
        export_scores_csv(conn, csv_path)
        e = extractor.stats
        console.print(
            f"\n[bold]Scores:[/bold] {stats.scored} jobs | requirements: {e['asked']} read by "
            f"the LLM now, {e['saved']} saved from before, {e['keywords']} from skill words"
            + (f", {e['failed']} the LLM couldn't answer" if e["failed"] else "")
            + (f", {e['errors']} server errors (retried next run)" if e["errors"] else "")
        )
        for switch in extractor.switched:
            console.print(f"[yellow]Switched model:[/yellow] {escape(switch)}")
        if extractor.stopped:
            console.print(f"[yellow]LLM stopped for this run:[/yellow] {escape(extractor.stopped)}"
                          " (the rest is read next run)")  # fmt: skip
        logger.info("SCORE {} {}", stats, dict(e))
        if show:
            _show_scores(conn, show, config.show.min_score)
        console.print(f"[dim]All scores with their parts: {csv_path}[/dim]")
    finally:
        conn.close()


def _show_scores(conn, limit: int, min_score: float) -> None:
    rows = conn.execute(
        """SELECT s.fit_score, s.reason, j.title, j.company, j.main_url, f.decision
           FROM scores s JOIN jobs j ON j.id = s.job_id JOIN job_filters f ON f.job_id = j.id
           WHERE s.fit_score >= ? ORDER BY s.fit_score DESC, j.first_seen_at DESC LIMIT ?""",
        (min_score, limit),
    ).fetchall()
    if not rows:
        return
    table = Table("Score", "Title (Ctrl+click to open)", "Company", "Why")
    for r in rows:
        title = f"[link={r['main_url']}]{escape(r['title'])}[/link]"
        if r["decision"] == "review":
            title += " [yellow](review)[/yellow]"
        table.add_row(f"{r['fit_score']:.0f}", title, escape(r["company"]), escape(r["reason"]))
    console.print(table)


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
