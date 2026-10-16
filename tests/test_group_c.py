"""Tests for DFKI (JSON feed) and foorilla (htmx HTML fragments).

Fixtures are trimmed copies of the real responses captured with scripts/probe_network.py
(2026-10-16); DFKI recruiter names/emails were replaced with placeholders.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx

from werkbot.adapters import REGISTRY
from werkbot.adapters.foorilla import parse_jobs
from werkbot.config import Settings
from werkbot.models import Source

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = Settings(request_delay_s=0, max_retries=2, _env_file=None)


def make(adapter: str, url: str, handler, **params):
    source = Source(name=adapter, group="C", adapter=adapter, url=url, params=params)
    return REGISTRY[adapter](source, SETTINGS, transport=httpx.MockTransport(handler))


# ---------- DFKI ----------

DFKI_URL = "https://jobs.dfki.de/utf8/ic_job_feeds.feed_engine"


def test_dfki_real_feed():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=(FIXTURES / "dfki_feed.json").read_text(encoding="utf-8"))

    jobs = make("dfki", DFKI_URL, handler, category_codes=["23427-662551"]).fetch()

    assert len(jobs) == 2
    job = jobs[0]
    assert job.title.startswith("Student Assistant (m/f/o) for semantic visual fact checking")
    assert job.company == "DFKI – Speech and Language Technology"
    assert job.location == "Berlin"
    assert job.source_job_id == "620743"
    assert str(job.url).startswith("https://jobs.dfki.de/de/vacancy/studentische-hilfskraft")
    assert job.date_posted == datetime(2026, 10, 6, tzinfo=UTC)
    assert "Wissenschaftl. Hilfskraft" in job.job_types
    assert requests[0].url.params.get_list("p_category_code_arr") == ["23427-662551"]
    assert requests[0].url.params["p_web_site_id"] == "6635"


def test_dfki_warns_on_unexpected_format():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"vacancies": [], "total": 5})

    adapter = make("dfki", DFKI_URL, handler)

    assert adapter.fetch() == []
    assert adapter.warnings


# ---------- foorilla ----------

FOORILLA_URL = "https://foorilla.com/hiring/jobs/"
NOW = datetime(2026, 10, 16, 21, 30, tzinfo=UTC)


def fixture_html() -> str:
    return (FIXTURES / "foorilla_jobs.html").read_text(encoding="utf-8")


def test_foorilla_parses_real_items():
    jobs = parse_jobs(fixture_html(), now=NOW)

    assert len(jobs) == 5  # 4 jobs + the item that also triggers page 2
    first = jobs[0]
    assert first.title == "Engineering & AI Systems Lead"  # "Featured" label removed
    assert first.remote is True
    assert str(first.url).startswith("https://foorilla.com/hiring/jobs/engineering-ai-systems")
    assert first.source_job_id == "3578233"

    part_time = next(j for j in jobs if "Part Time" in j.job_types)
    assert part_time.job_types == ["Internship", "Part Time"]
    assert part_time.location == "Bratislava, Bratislavsky kraj, Slovakia"
    assert part_time.date_posted is not None and part_time.date_posted.hour == 16  # "5h ago"


def test_foorilla_searches_and_filters_locations():
    requests: list[httpx.Request] = []
    html = fixture_html().replace("Vienna, Austria", "Kaiserslautern, Germany")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=html if request.url.params.get("page") is None else "")

    adapter = make(
        "foorilla",
        FOORILLA_URL,
        handler,
        search_terms=["working student"],
        location_keywords=["Germany"],
    )
    jobs = adapter.fetch()

    assert [j.location for j in jobs] == ["Kaiserslautern, Germany"]
    assert requests[0].url.params["job_search"] == "working student"
    assert requests[0].headers["HX-Request"] == "true"
    assert requests[1].url.params["page"] == "2"  # fixture contains the page-2 trigger


def test_foorilla_warns_when_items_disappear():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='<ul class="list-group"><li>new layout</li></ul>')

    adapter = make("foorilla", FOORILLA_URL, handler, search_terms=["x"])

    assert adapter.fetch() == []
    assert adapter.warnings
