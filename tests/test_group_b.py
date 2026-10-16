"""Tests for connecticum, e-fellows, Fraunhofer, workingstudentjobs and germantechjobs.

The HTML fixtures in tests/fixtures/ are trimmed copies of the REAL pages (2026-10-16),
so these tests check our parsers against the sites' actual markup.
"""

from pathlib import Path

import httpx

from werkbot.adapters import REGISTRY
from werkbot.config import Settings
from werkbot.models import Source

FIXTURES = Path(__file__).parent / "fixtures"
SETTINGS = Settings(request_delay_s=0, max_retries=2, _env_file=None)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make(adapter: str, url: str, handler, **params):
    source = Source(name=adapter, group="B", adapter=adapter, url=url, params=params)
    return REGISTRY[adapter](source, SETTINGS, transport=httpx.MockTransport(handler))


def serve(html: str, requests: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, text=html)

    return handler


# ---------- connecticum ----------


def test_connecticum_real_cards():
    html = fixture("connecticum_list.html").replace('rel="next"', 'rel="nofollow"')  # one page
    jobs = make("connecticum", "https://www.connecticum.de/jobs?x=1", serve(html)).fetch()

    assert len(jobs) == 3
    job = jobs[0]
    assert job.title == "Werkstudent:in Datenanalyse und Digitalisierung"
    assert job.company == "Deutsche Bahn AG"
    assert job.location == "Mainz"
    assert job.source_job_id == "1862399"
    assert job.job_types == ["Werkstudentenjob"]
    assert job.date_posted is not None and job.date_posted.strftime("%d.%m.%Y") == "16.10.2026"


def test_connecticum_follows_next_link():
    page1 = fixture("connecticum_list.html")  # contains rel="next" -> page=2
    page2 = page1.replace('rel="next"', 'rel="nofollow"').replace("1862399", "9999999")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=page2 if request.url.params.get("page") == "2" else page1)

    jobs = make("connecticum", "https://www.connecticum.de/jobs?x=1", handler).fetch()

    assert len(requests) == 2
    assert "9999999" in {j.source_job_id for j in jobs}


def test_connecticum_warns_when_cards_disappear():
    html = "<html><body>Seite 1 von 5 <div>new layout</div></body></html>"
    adapter = make("connecticum", "https://www.connecticum.de/jobs", serve(html))

    assert adapter.fetch() == []
    assert adapter.warnings


# ---------- e-fellows ----------


def test_efellows_real_next_data():
    jobs = make(
        "efellows", "https://jobs.e-fellows.net/jobs/it/x", serve(fixture("efellows_list.html"))
    ).fetch()

    assert len(jobs) == 3
    job = jobs[0]
    assert job.title == "Werkstudent UX Apps (w/m/d)"
    assert job.company == "United Internet AG"
    assert job.location == "München"
    assert str(job.url) == "https://jobs.e-fellows.net/job/werkstudent-ux-apps-wmd-15280"
    assert job.date_posted is not None and job.date_posted.isoformat().startswith("2026-08-27")
    assert "IT" in job.job_types


def test_efellows_stops_at_result_count():
    requests: list[httpx.Request] = []
    make(
        "efellows", "https://jobs.e-fellows.net/x", serve(fixture("efellows_list.html"), requests)
    ).fetch()

    assert len(requests) == 1  # result_count 3, all 3 on page 1


def test_efellows_warns_without_next_data():
    adapter = make("efellows", "https://jobs.e-fellows.net/x", serve("<html>redesign</html>"))

    assert adapter.fetch() == []
    assert adapter.warnings


# ---------- Fraunhofer ----------


def test_fraunhofer_real_table():
    jobs = make(
        "fraunhofer",
        "https://jobs.fraunhofer.de/search/?q=",
        serve(fixture("fraunhofer_list.html")),
    ).fetch()

    assert len(jobs) == 3
    job = jobs[0]
    assert job.title.startswith("STUDENT ASSISTANT (all genders)")
    assert job.company == "Fraunhofer IESE"
    assert job.location == "Kaiserslautern"
    assert job.source_job_id == "1446740233"
    assert str(job.url).startswith("https://jobs.fraunhofer.de/job/Kaiserslautern-STUDENT")
    assert jobs[2].company == "Fraunhofer ITWM"


def test_fraunhofer_pages_with_startrow():
    page = fixture("fraunhofer_list.html").replace("von <b>10</b>", "von <b>6</b>")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get("startrow") == "3":
            return httpx.Response(200, text=page.replace("1446740233", "1111111111"))
        return httpx.Response(200, text=page)

    jobs = make("fraunhofer", "https://jobs.fraunhofer.de/search/?q=", handler).fetch()

    assert [r.url.params.get("startrow") for r in requests] == [None, "3"]
    assert "1111111111" in {j.source_job_id for j in jobs}


# ---------- workingstudentjobs ----------


def test_workingstudentjobs_real_payload():
    jobs = make(
        "workingstudentjobs",
        "https://www.workingstudentjobs.de/",
        serve(fixture("workingstudentjobs_list.html")),
    ).fetch()

    assert len(jobs) == 3
    job = jobs[0]
    assert job.company == "Fraunhofer IESE"
    assert job.location == "Kaiserslautern"
    assert job.german_required is False
    assert job.job_types == ["Working Student"]
    assert job.date_posted is not None and job.date_posted.isoformat().startswith("2026-10-15")
    assert str(job.url).startswith("https://www.workingstudentjobs.de/jobs/fraunhofer-iese-")


def test_workingstudentjobs_ignores_trending_sidebar_links():
    sidebar = '<a href="/jobs/kubermatic-werkstudent-in-finance-controlling-w-m-d-f9ed107f">x</a>'
    html = fixture("workingstudentjobs_list.html").replace("<body>", "<body>" + sidebar)
    jobs = make("workingstudentjobs", "https://www.workingstudentjobs.de/", serve(html)).fetch()

    assert len(jobs) == 3
    assert not any("kubermatic" in j.source_job_id for j in jobs)


def test_workingstudentjobs_warns_when_payload_changes():
    html = "<html><p>Showing 1-14 of 14</p><div>new layout</div></html>"
    adapter = make("workingstudentjobs", "https://www.workingstudentjobs.de/", serve(html))

    assert adapter.fetch() == []
    assert adapter.warnings and "14 jobs" in adapter.warnings[0]


# ---------- germantechjobs (JSON API) ----------

GTJ = [
    {  # field names as returned by https://germantechjobs.de/api/jobsLight (2026-10-16)
        "_id": "a1",
        "jobUrl": "Acme-GmbH-Working-Student-Python",
        "name": "Working Student Python (m/w/d)",
        "company": "Acme GmbH",
        "actualCity": "Kaiserslautern",
        "cityCategory": "Kaiserslautern",
        "workplace": "hybrid",
        "language": "English",
        "jobType": "Part-Time",
        "expLevel": "Junior",
        "activeFrom": "2026-10-15T13:39:51.250+02:00",
        "redirectJobUrl": "https://acme.example/apply",
    },
    {
        "_id": "a2",
        "jobUrl": "Big-Corp-Developer",
        "name": "Developer (m/w/d)",
        "company": "Big Corp",
        "actualCity": "Espelkamp",
        "workplace": "office",
        "language": "German",
        "jobType": "Full-Time",
        "activeFrom": "2026-10-14T10:00:00.000+02:00",
    },
    {
        "_id": "a3",
        "jobUrl": "Remote-Co-Werkstudent-Data",
        "name": "Werkstudent Data (m/w/d)",
        "company": "Remote Co",
        "actualCity": "Berlin",
        "workplace": "remote",
        "language": "German",
        "jobType": "Part-Time",
        "activeFrom": "2026-10-13T10:00:00.000+02:00",
    },
]


def _gtj(**params):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=GTJ)

    return make("germantechjobs", "https://germantechjobs.de/api/jobsLight", handler, **params)


def test_germantechjobs_filters_type_and_language():
    jobs = _gtj(job_types=["part-time"], languages=["English"]).fetch()

    assert [j.source_job_id for j in jobs] == ["a1"]
    job = jobs[0]
    assert str(job.url) == "https://germantechjobs.de/jobs/Acme-GmbH-Working-Student-Python"
    assert str(job.apply_url) == "https://acme.example/apply"
    assert job.german_required is False
    assert job.location == "Kaiserslautern"


def test_germantechjobs_without_filters_keeps_all():
    jobs = _gtj().fetch()

    assert len(jobs) == 3
    assert jobs[1].german_required is True
    assert jobs[2].remote is True


def test_germantechjobs_warns_on_unexpected_format():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": []})

    adapter = make("germantechjobs", "https://germantechjobs.de/api/jobsLight", handler)

    assert adapter.fetch() == []
    assert adapter.warnings
