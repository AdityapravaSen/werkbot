"""Phase 2: saving to SQLite across runs (upsert, link priority, expiry, reposts)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from werkbot import db
from werkbot.dedup.normalize import load_aliases
from werkbot.dedup.store import save_jobs
from werkbot.models import Job

ROOT = Path(__file__).resolve().parents[1]
ALIASES = load_aliases(ROOT / "data" / "company_aliases.yaml")
KINDS = {"fraunhofer": "employer", "connecticum": "aggregator", "arbeitnow": "board"}
DAY_1 = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)


def changed(job: Job, fields: dict) -> Job:
    """A validated copy of a job with some fields changed (model_copy skips validation)."""
    return Job.model_validate({**job.model_dump(), **fields})


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "werkbot.db"
    db.init_db(path)
    connection = db.connect(path)
    yield connection
    connection.close()


@pytest.fixture
def real() -> dict[str, Job]:
    """Real Fraunhofer + connecticum listings (see tests/fixtures)."""
    path = ROOT / "tests" / "fixtures" / "fraunhofer_vs_connecticum.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row.pop("tag"): Job.model_validate(row) for row in data["jobs"]}


def save(conn, jobs, now=DAY_1, **kw):
    return save_jobs(conn, jobs, KINDS, aliases=ALIASES, now=now, **kw)


def rows(conn, sql, *args):
    return [tuple(r) for r in conn.execute(sql, args).fetchall()]


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_run_merges_duplicates_and_keeps_every_link(conn, real):
    jobs = list(real.values())
    result = save(conn, jobs)

    assert result.received == 17
    assert result.groups == result.new == 11  # see test_dedup for which ones merge
    assert count(conn, "jobs") == 11
    assert count(conn, "job_sources") == 17  # no link is lost
    assert db.find_duplicates(conn) == {}

    # The emulator fuzzing job: one row, the Fraunhofer link first, both boards listed
    (job_id, main_url, main_source, status) = rows(
        conn,
        "SELECT id, main_url, main_source, status FROM jobs WHERE title LIKE '%emulator%'",
    )[0]
    assert main_source == "fraunhofer" and "jobs.fraunhofer.de" in main_url
    assert status == "new"
    sources = rows(conn, "SELECT source FROM job_sources WHERE job_id = ? ORDER BY source", job_id)
    assert sources == [("connecticum",), ("fraunhofer",)]


def test_running_twice_creates_nothing_new(conn, real):
    save(conn, list(real.values()))
    again = save(conn, list(real.values()), now=DAY_1 + timedelta(hours=2))

    assert again.new == 0 and again.links_added == 0
    assert again.seen_again == 11
    assert count(conn, "jobs") == 11 and count(conn, "job_sources") == 17
    # Same day: still "new" (the daily digest shows jobs first seen today)
    assert rows(conn, "SELECT DISTINCT status FROM jobs") == [("new",)]
    assert db.find_duplicates(conn) == {}


def test_next_day_new_jobs_become_active(conn, real):
    save(conn, [real["F3"]])
    save(conn, [real["F3"]], now=DAY_1 + timedelta(days=1))

    assert rows(conn, "SELECT status, first_seen_at FROM jobs") == [
        ("active", "2026-10-19 06:00:00")
    ]


def test_aggregator_first_then_employer_upgrades_the_link(conn, real):
    connecticum = changed(real["C4"], {"description": "Long text " * 50})
    fraunhofer = changed(
        real["F3"], {"description": "Short", "date_posted": datetime(2026, 10, 1, tzinfo=UTC)}
    )
    save(conn, [connecticum])
    result = save(conn, [fraunhofer], now=DAY_1 + timedelta(days=1))

    assert result.new == 0 and result.links_added == 1
    assert result.matched[0][2] == "same company + title + city"
    main_source, description, date_posted = rows(
        conn, "SELECT main_source, description, date_posted FROM jobs"
    )[0]
    assert main_source == "fraunhofer"
    assert description.startswith("Long text")  # the longer description is kept
    assert date_posted.startswith("2026-10-01")


def test_aggregator_later_does_not_replace_the_employer_link(conn, real):
    save(conn, [real["F3"]])
    save(conn, [real["C4"]], now=DAY_1 + timedelta(days=1))

    assert rows(conn, "SELECT main_source FROM jobs") == [("fraunhofer",)]
    assert count(conn, "job_sources") == 2


def test_fuzzy_match_against_a_job_from_an_earlier_run(conn, real):
    save(conn, [real["F2"]])  # "... Security/Hacking Tools - C/C++"
    result = save(conn, [real["C2"]], now=DAY_1 + timedelta(days=1))  # without " - C/C++"

    assert result.new == 0
    assert result.matched[0][2].startswith("similar title")
    assert count(conn, "jobs") == 1


def test_same_board_new_id_with_a_similar_title_is_a_new_job(conn, real):
    """Fuzzy matching across runs is also only for other boards."""
    save(conn, [real["C2"]])
    other = changed(
        real["C2"],
        {
            "title": real["C2"].title + " - C/C++",
            "url": "https://www.connecticum.de/jobs/another-1999999",
            "source_job_id": "1999999",
        },
    )
    result = save(conn, [other], now=DAY_1 + timedelta(days=1))

    assert result.new == 1 and count(conn, "jobs") == 2


def test_job_not_seen_for_14_days_expires_and_comes_back_as_reposted(conn, real):
    save(conn, [real["F3"]])
    later = save(conn, [real["F4"]], now=DAY_1 + timedelta(days=15))
    assert later.expired == 1
    assert rows(conn, "SELECT status FROM jobs WHERE title LIKE '%emulator%'") == [("expired",)]

    back = save(conn, [real["C4"]], now=DAY_1 + timedelta(days=20))  # listed again elsewhere
    assert back.reposted == 1 and back.new == 0
    assert rows(conn, "SELECT status FROM jobs WHERE title LIKE '%emulator%'") == [("reposted",)]

    save(conn, [real["C4"]], now=DAY_1 + timedelta(days=21))
    assert rows(conn, "SELECT status FROM jobs WHERE title LIKE '%emulator%'") == [("active",)]


def test_filtered_out_jobs_keep_their_status(conn, real):
    save(conn, [real["F3"]])
    conn.execute("UPDATE jobs SET status = 'filtered_out'")
    conn.commit()
    save(conn, [real["F3"]], now=DAY_1 + timedelta(days=30))

    assert rows(conn, "SELECT status FROM jobs") == [("filtered_out",)]


def test_same_source_id_with_a_new_url_updates_the_link(conn, real):
    save(conn, [real["C4"]])
    moved = changed(real["C4"], {"url": "https://www.connecticum.de/jobs/new-slug-1848375"})
    result = save(conn, [moved], now=DAY_1 + timedelta(days=1))

    assert result.new == 0 and result.matched[0][2] == "same source ID"
    assert rows(conn, "SELECT url FROM job_sources") == [
        ("https://www.connecticum.de/jobs/new-slug-1848375",)
    ]
    assert db.find_duplicates(conn) == {}


def test_tracking_parameters_do_not_create_a_second_job(conn, real):
    save(conn, [real["C4"]])
    tracked = changed(
        real["C4"], {"url": str(real["C4"].url) + "?utm_source=newsletter", "source_job_id": None}
    )
    result = save(conn, [tracked], now=DAY_1 + timedelta(hours=1))

    assert result.matched[0][2] == "same URL"
    assert count(conn, "jobs") == 1 and count(conn, "job_sources") == 1


def test_unknown_company_jobs_are_not_merged(conn):
    def foorilla(n):
        return Job(
            title="Working Student Marketing",
            company="Unknown",
            url=f"https://foorilla.com/hiring/jobs/{n}/",
            source="foorilla",
            source_job_id=str(n),
            location="Berlin",
        )

    save(conn, [foorilla(1)])
    save(conn, [foorilla(2)], now=DAY_1 + timedelta(days=1))

    assert count(conn, "jobs") == 2
    assert rows(conn, "SELECT fingerprint LIKE 'url:%' FROM jobs") == [(1,), (1,)]


def test_dry_run_writes_nothing(conn, real):
    result = save(conn, list(real.values()), dry_run=True)

    assert result.new == 11
    assert count(conn, "jobs") == 0 and count(conn, "job_sources") == 0


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Point the CLI at a temporary output folder and database."""
    from werkbot.config import get_settings

    monkeypatch.setenv("WERKBOT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("WERKBOT_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WERKBOT_SOURCES_PATH", str(ROOT / "data" / "sources.yaml"))
    monkeypatch.setenv("WERKBOT_COMPANY_ALIASES_PATH", str(ROOT / "data" / "company_aliases.yaml"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_cli_scrape_saves_to_the_database(cli_env, monkeypatch, real):
    from typer.testing import CliRunner

    from werkbot.adapters import REGISTRY
    from werkbot.cli import app

    class FakeFraunhofer:  # stands in for the real adapter, no network
        def __init__(self, source, settings):
            self.warnings = []

        def fetch(self):
            return [j for j in real.values() if j.source == "fraunhofer"]

    monkeypatch.setitem(REGISTRY, "fraunhofer", FakeFraunhofer)
    first = CliRunner().invoke(app, ["scrape", "-s", "fraunhofer", "--no-filter"])
    second = CliRunner().invoke(app, ["scrape", "-s", "fraunhofer", "--no-details"])
    skipped = CliRunner().invoke(app, ["scrape", "-s", "fraunhofer", "--no-save"])

    assert first.exit_code == second.exit_code == skipped.exit_code == 0, first.output
    assert "7 jobs -> 6" in first.output and "6 new" in first.output  # F4 = F5
    assert "0 new" in second.output and "6 seen before" in second.output
    assert "Database" not in skipped.output
    # The second run also filtered (offline: no job pages fetched)
    assert "Filters:" in second.output and "Filters:" not in first.output
    assert (cli_env / "output" / "filtered.csv").exists()
    assert (cli_env / "output" / "raw" / "fraunhofer.json").exists()


def test_cli_dedup_reads_raw_files(cli_env, real):
    from typer.testing import CliRunner

    from werkbot.cli import app

    raw = cli_env / "output" / "raw"
    raw.mkdir(parents=True)
    for source in ("fraunhofer", "connecticum"):
        jobs = [j.model_dump(mode="json") for j in real.values() if j.source == source]
        (raw / f"{source}.json").write_text(json.dumps(jobs), encoding="utf-8")
    dry = CliRunner().invoke(app, ["dedup", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "17 jobs -> 11" in dry.output and "nothing saved" in dry.output

    first = CliRunner().invoke(app, ["dedup"])
    second = CliRunner().invoke(app, ["dedup"])
    assert first.exit_code == second.exit_code == 0, first.output + second.output
    assert "11 new" in first.output
    assert "0 new" in second.output and "11 seen before" in second.output
