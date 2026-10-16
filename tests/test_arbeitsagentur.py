"""Tests for the Arbeitsagentur adapter, using a fake API (no real network).

The fake responses copy the real API format captured on 2026-10-15.
"""

import base64

import httpx

from werkbot.adapters.arbeitsagentur import ArbeitsagenturAdapter
from werkbot.config import Settings
from werkbot.models import Source, SourceGroup

BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"


def _hit(ref, title, ort="Kaiserslautern", homeoffice=False):
    """One search result in the current (2026) format."""
    return {
        "stellenangebotsart": "PRAKTIKUM_TRAINEE",
        "stellenangebotsTitel": title,
        "stellenlokationen": [
            {"adresse": {"plz": "67655", "ort": ort, "region": "RHEINLAND_PFALZ"}}
        ],
        "homeofficemoeglich": homeoffice,
        "veroeffentlichungszeitraum": {"von": "2026-10-13"},
        "datumErsteVeroeffentlichung": "2026-10-12",
        "hauptberuf": "Informatiker/in",
        "firma": "Acme GmbH",
        "referenznummer": ref,
    }


SEARCH = {
    1: {
        "ergebnisliste": [_hit("10000-1", "Werkstudent Data (m/w/d)"), _hit("10000-2", "HiWi")],
        "maxErgebnisse": "3",
    },
    2: {
        # the same job again on page 2 must not be added twice
        "ergebnisliste": [_hit("10000-3", "Werkstudent IT"), _hit("10000-1", "duplicate")],
        "maxErgebnisse": "3",
    },
}


def _adapter(handler, **params) -> ArbeitsagenturAdapter:
    source = Source(
        name="arbeitsagentur",
        group=SourceGroup.API,
        adapter="arbeitsagentur",
        url=BASE,
        params={"was": "Werkstudent", "wo": "Kaiserslautern", "size": 2, **params},
    )
    settings = Settings(request_delay_s=0, max_retries=2, _env_file=None)
    return ArbeitsagenturAdapter(source, settings, transport=httpx.MockTransport(handler))


def _api(requests: list[httpx.Request], details_status: int = 200, search=SEARCH):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/pc/v6/jobs"):
            page = int(request.url.params["page"])
            return httpx.Response(200, json=search.get(page, {"ergebnisliste": []}))
        if "/pc/v4/jobdetails/" in request.url.path:
            if details_status != 200:
                return httpx.Response(details_status, headers={"Retry-After": "0"})
            ref = base64.b64decode(request.url.path.rsplit("/", 1)[1]).decode()
            return httpx.Response(
                200,
                json={
                    "stellenangebotsBeschreibung": f"<p>Description for {ref}</p>",
                    "homeofficemoeglich": True,
                },
            )
        return httpx.Response(404)

    return handler


def test_search_pages_and_dedups_by_referenznummer():
    requests: list[httpx.Request] = []
    jobs = _adapter(_api(requests), fetch_details=False).fetch()

    assert [j.source_job_id for j in jobs] == ["10000-1", "10000-2", "10000-3"]
    assert jobs[0].title == "Werkstudent Data (m/w/d)"  # first version kept
    search_pages = [r.url.params["page"] for r in requests if r.url.path.endswith("/jobs")]
    assert search_pages == ["1", "2"]  # stops once page * size >= maxErgebnisse


def test_sends_api_key_and_query():
    requests: list[httpx.Request] = []
    _adapter(_api(requests), fetch_details=False, umkreis=200).fetch()

    first = requests[0]
    assert first.headers["X-API-Key"] == "jobboerse-jobsuche"
    assert first.url.params["was"] == "Werkstudent"
    assert first.url.params["umkreis"] == "200"
    assert "fetch_details" not in first.url.params  # our own settings are not sent


def test_job_fields_and_details():
    job = _adapter(_api([])).fetch()[0]

    assert job.company == "Acme GmbH"
    assert job.location == "Kaiserslautern"
    assert str(job.url) == "https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-1"
    assert job.date_posted is not None and job.date_posted.day == 12  # first publication
    assert job.description == "Description for 10000-1"  # base64 ID decoded correctly
    assert job.remote is True  # homeofficemoeglich from details


def test_details_rate_limit_keeps_jobs():
    adapter = _adapter(_api([], details_status=429))
    jobs = adapter.fetch()

    assert len(jobs) == 3  # jobs kept, just without descriptions
    assert all(j.description == "" for j in jobs)
    assert any("rate-limited" in w for w in adapter.warnings)


def test_old_format_still_works():
    old = {
        1: {
            "stellenangebote": [
                {
                    "refnr": "OLD-1",
                    "titel": "Werkstudent Old",
                    "arbeitgeber": "Old GmbH",
                    "arbeitsort": {"ort": "Mannheim"},
                    "aktuelleVeroeffentlichungsdatum": "2026-10-10",
                }
            ],
            "maxErgebnisse": "1",
        }
    }
    job = _adapter(_api([], search=old), fetch_details=False).fetch()[0]

    assert (job.source_job_id, job.title, job.company, job.location) == (
        "OLD-1",
        "Werkstudent Old",
        "Old GmbH",
        "Mannheim",
    )


def test_warns_when_format_changes_again():
    unknown = {1: {"somethingNew": [{"x": 1}], "maxErgebnisse": "189"}}
    adapter = _adapter(_api([], search=unknown), fetch_details=False)

    assert adapter.fetch() == []
    assert adapter.warnings and "189 jobs but none were readable" in adapter.warnings[0]
