"""Phase 3: running all filters over the database."""

import csv
import json
from pathlib import Path

import pytest

from werkbot import db
from werkbot.dedup.normalize import load_aliases
from werkbot.dedup.store import save_jobs
from werkbot.filters.config import load_filter_config
from werkbot.filters.places import Gazetteer
from werkbot.filters.run import Filters, export_csv, load_jobs, run_filters
from werkbot.models import Job
from werkbot.sources import load_sources

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_filter_config(ROOT / "data" / "filters.yaml")
SOURCES = {s.name: s for s in load_sources(ROOT / "data" / "sources.yaml", include_disabled=True)}
ENGLISH = (
    "We are looking for a working student to support our data team. You will build data "
    "pipelines in Python and work closely with our machine learning engineers. We offer "
    "flexible working hours, a friendly team and a modern office close to the station. "
)


def job(n, title, location, source="arbeitnow", description=ENGLISH, **extra) -> Job:
    return Job(title=title, company=f"Company {n}", url=f"https://{source}.example/{n}",
               source=source, source_job_id=str(n), location=location,
               description=description, **extra)  # fmt: skip


JOBS = [
    job(1, "Werkstudent Data Science (m/w/d)", "Mannheim"),
    job(2, "Werkstudent Marketing (m/w/d)", "Mannheim"),  # not tech
    job(3, "Working Student Software Engineering", "Berlin"),  # too far
    job(4, "Working Student Software Engineering", "Remote"),  # remote: fine
    job(5, "Werkstudent Softwareentwicklung", "Karlsruhe",
        description=ENGLISH + "Requirements: fluent German and English."),  # fluent German
    job(6, "Werkstudent Softwareentwicklung", "Karlsruhe",
        description=ENGLISH + "German is a plus."),
    job(7, "Working Student Data Engineering", "München, Germany (Remote available)",
        source="wearedevelopers"),  # its remote flag means hybrid: review outside the area
    job(8, "Werkstudent Backend", "Saarbrücken", description=""),  # no description yet
]  # fmt: skip


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "werkbot.db"
    db.init_db(path)
    connection = db.connect(path)
    jobs = [j.model_copy(update={"remote": j.source == "wearedevelopers"}) for j in JOBS]
    save_jobs(connection, jobs, {}, load_aliases(ROOT / "data" / "company_aliases.yaml"))
    yield connection
    connection.close()


@pytest.fixture
def filters():
    return Filters(CONFIG, Gazetteer.load(ROOT / "data" / "places.tsv"), SOURCES)


def decisions(conn) -> dict[str, tuple[str, str]]:
    return {
        r["main_url"].rsplit("/", 1)[1]: (r["decision"], json.loads(r["reasons"])[0])
        for r in conn.execute(
            "SELECT j.main_url, f.decision, f.reasons"
            " FROM job_filters f JOIN jobs j ON j.id = f.job_id"
        )
    }


def test_every_job_gets_a_decision_with_its_reason(conn, filters):
    counts = run_filters(conn, filters)
    d = decisions(conn)

    assert d["1"][0] == "keep"
    assert d["2"] == ("drop", "role: not tech: marketing")
    assert d["3"][0] == "drop" and d["3"][1].startswith("location: too far: Berlin")
    assert d["4"][0] == "keep"
    assert d["5"][0] == "drop" and d["5"][1].startswith("language: German fluent")
    assert d["6"][0] == "keep"
    assert d["7"][0] == "review" and d["7"][1].startswith("location: hybrid")
    assert d["8"] == ("review", "language: no description yet to check the language")
    assert counts == {"keep": 3, "review": 2, "drop": 3}


def test_running_again_replaces_the_results(conn, filters):
    run_filters(conn, filters)
    run_filters(conn, filters)
    assert conn.execute("SELECT COUNT(*) FROM job_filters").fetchone()[0] == len(JOBS)


def test_prefilter_without_language_for_choosing_pages_to_fetch(conn, filters):
    """Before fetching job pages, only location and role are checked."""
    verdicts = {j.main_url.rsplit("/", 1)[1]: filters.judge(j, with_language=False)
                for j in load_jobs(conn)}  # fmt: skip
    assert verdicts["5"].decision == "keep"  # German is only checked after fetching
    assert verdicts["8"].decision == "keep"
    assert verdicts["3"].decision == "drop"


def test_csv_export(conn, filters, tmp_path):
    run_filters(conn, filters)
    path = tmp_path / "filtered.csv"
    assert export_csv(conn, path) == len(JOBS)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert [r["decision"] for r in rows] == sorted(
        (r["decision"] for r in rows), key=["keep", "review", "drop"].index
    )
    first = rows[0]
    assert first["decision"] == "keep" and first["link"].startswith("https://")
