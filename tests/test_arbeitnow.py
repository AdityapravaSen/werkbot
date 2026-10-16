"""Tests for the Arbeitnow adapter, using a fake HTTP layer (no real network)."""

from datetime import UTC, datetime, timedelta

import httpx

from werkbot.adapters.arbeitnow import ArbeitnowAdapter
from werkbot.config import Settings
from werkbot.models import Source, SourceGroup

NOW = datetime.now(UTC)


def _ts(days_ago: float) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def _item(slug, title, days_ago, **extra):
    return {
        "slug": slug,
        "company_name": "Acme GmbH",
        "title": title,
        "description": "<p>Hello <b>world</b></p><ul><li>Python</li><li>SQL</li></ul>",
        "remote": False,
        "url": f"https://www.arbeitnow.com/jobs/companies/acme/{slug}",
        "tags": [],
        "job_types": [],
        "location": "Berlin",
        "created_at": _ts(days_ago),
        **extra,
    }


PAGES = {
    1: {
        "data": [
            _item("ws-ml-1", "Working Student Machine Learning (m/w/d)", 1),
            _item("senior-dev-2", "Senior Backend Developer", 1),
            _item("ws-data-3", "Data Analyst", 2, job_types=["Werkstudent"]),
        ],
        "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
    },
    2: {
        "data": [
            _item("ws-old-4", "Werkstudent Software Engineering", 30),  # too old
            _item("ws-old-5", "Working Student IT", 40),  # too old
        ],
        "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=3"},
    },
    3: {"data": [_item("never-reached", "Working Student", 1)], "links": {"next": None}},
}


def _make_adapter(requested_pages: list[int]) -> ArbeitnowAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        requested_pages.append(page)
        return httpx.Response(200, json=PAGES.get(page, {"data": []}))

    source = Source(
        name="arbeitnow",
        group=SourceGroup.API,
        adapter="arbeitnow",
        url="https://www.arbeitnow.com/api/job-board-api",
    )
    settings = Settings(request_delay_s=0, max_age_days=7, _env_file=None)
    return ArbeitnowAdapter(source, settings, transport=httpx.MockTransport(handler))


def test_keeps_only_recent_matching_jobs():
    requested: list[int] = []
    jobs = _make_adapter(requested).fetch()

    assert [j.source_job_id for j in jobs] == ["ws-ml-1", "ws-data-3"]
    # Page 2 is entirely older than 7 days, so page 3 is never requested
    assert requested == [1, 2]


def test_job_fields_are_mapped():
    job = _make_adapter([]).fetch()[0]

    assert job.title == "Working Student Machine Learning (m/w/d)"
    assert job.company == "Acme GmbH"
    assert job.source == "arbeitnow"
    assert job.location == "Berlin"
    assert job.date_posted is not None and job.date_posted.tzinfo is not None
    assert "<" not in job.description  # HTML stripped
    assert "Python" in job.description and "SQL" in job.description


def test_stops_when_no_next_link():
    requested: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(int(request.url.params.get("page", 1)))
        return httpx.Response(
            200, json={"data": [_item("a", "Working Student", 1)], "links": {"next": None}}
        )

    source = Source(
        name="arbeitnow",
        group=SourceGroup.API,
        adapter="arbeitnow",
        url="https://www.arbeitnow.com/api/job-board-api",
    )
    settings = Settings(request_delay_s=0, _env_file=None)
    jobs = ArbeitnowAdapter(source, settings, transport=httpx.MockTransport(handler)).fetch()

    assert len(jobs) == 1
    assert requested == [1]


def test_retries_on_server_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [], "links": {"next": None}})

    source = Source(
        name="arbeitnow",
        group=SourceGroup.API,
        adapter="arbeitnow",
        url="https://www.arbeitnow.com/api/job-board-api",
    )
    settings = Settings(request_delay_s=0, _env_file=None)
    jobs = ArbeitnowAdapter(source, settings, transport=httpx.MockTransport(handler)).fetch()

    assert jobs == []
    assert calls["n"] == 2


def test_rate_limit_keeps_partial_results():
    """A 429 that never clears must not throw away the pages already fetched."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "data": [_item("ws-1", "Working Student Data", 1)],
                    "links": {"next": "https://www.arbeitnow.com/api/job-board-api?page=2"},
                },
            )
        return httpx.Response(429, headers={"Retry-After": "0"})

    source = Source(
        name="arbeitnow",
        group=SourceGroup.API,
        adapter="arbeitnow",
        url="https://www.arbeitnow.com/api/job-board-api",
    )
    settings = Settings(request_delay_s=0, max_retries=2, _env_file=None)
    adapter = ArbeitnowAdapter(source, settings, transport=httpx.MockTransport(handler))
    jobs = adapter.fetch()

    assert [j.source_job_id for j in jobs] == ["ws-1"]
    assert adapter.warnings and "page 2" in adapter.warnings[0]


def test_retry_after_header_is_obeyed():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"data": [], "links": {"next": None}})

    source = Source(
        name="arbeitnow",
        group=SourceGroup.API,
        adapter="arbeitnow",
        url="https://www.arbeitnow.com/api/job-board-api",
    )
    settings = Settings(request_delay_s=0, _env_file=None)
    adapter = ArbeitnowAdapter(source, settings, transport=httpx.MockTransport(handler))

    assert adapter.fetch() == []
    assert calls["n"] == 3
    assert adapter.warnings == []
