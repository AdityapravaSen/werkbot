"""Phase 3: reading descriptions from job pages (trimmed copies of real pages)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from werkbot import db
from werkbot.config import Settings
from werkbot.dedup.normalize import load_aliases
from werkbot.dedup.store import save_jobs
from werkbot.details import extract_details, fetch_details, jobs_needing_details, site_of
from werkbot.filters.config import DetailRules
from werkbot.models import Job

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
ALIASES = load_aliases(ROOT / "data" / "company_aliases.yaml")


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_json_ld_job_posting():
    d = extract_details(page("workingstudentjobs_detail.html"))
    assert d.method == "jsonld"
    assert d.company == "Kubermatic"
    assert d.description.startswith("Über uns\nBei Kubermatic")
    assert len(d.description) > 2000
    assert d.remote is True  # jobLocationType TELECOMMUTE
    assert "PART_TIME" in d.employment_types
    assert d.date_posted == datetime(2026, 10, 8, 9, 20, 11, 393000, tzinfo=UTC)


def test_json_ld_e_fellows():
    d = extract_details(page("efellows_detail.html"))
    assert (d.method, d.company) == ("jsonld", "United Internet AG")
    assert "Werkstudent UX Apps" in d.description


def test_microdata_fraunhofer():
    d = extract_details(page("fraunhofer_detail.html"))
    assert d.method == "microdata"
    assert d.description.startswith("In the European research project CEADS")


def test_line_breaks_in_the_html_source_are_spaces():
    """Pages (and editors) wrap long paragraphs; only block elements start a new line."""
    html = (
        '<div itemprop="description"><p>In the European\r\n   research project</p>'
        "<ul>\n<li>One</li>\n<li>Two</li></ul></div>"
    )
    assert extract_details(html).description == "In the European research project\nOne\nTwo"


def test_page_text_is_only_used_if_it_is_about_the_job():
    """Without structured data the page's main text is the last resort, but only if it
    mentions the job (not a cookie banner or a list of other jobs)."""
    body = "<p>" + "Wir verwenden Cookies, um unsere Website zu verbessern. " * 10 + "</p>"
    html = f"<html><body><article>{body}</article></body></html>"
    d = extract_details(html, "https://example.com/job/1", title="Werkstudent Data Science")
    assert (d.method, d.description) == ("none", "")
    about = "<p>" + "Als Werkstudent Data Science baust du Modelle in Python. " * 10 + "</p>"
    html = f"<html><body><article>{about}</article></body></html>"
    d = extract_details(html, "https://example.com/job/1", title="Werkstudent Data Science")
    assert d.method == "text" and "Modelle" in d.description


def test_wearedevelopers_pages_are_skipped():
    """403 for programs, and no Markdown version of its external ads (checked Oct 2026)."""
    from werkbot.filters.config import load_filter_config

    rules = load_filter_config(ROOT / "data" / "filters.yaml").details
    assert rules.per_site["wearedevelopers.com"].skip


def test_connecticum_pages_have_no_ad_text():
    """connecticum's job page only lists facts and other jobs, so filters.yaml skips it."""
    from werkbot.filters.config import load_filter_config

    rules = load_filter_config(ROOT / "data" / "filters.yaml").details
    assert rules.per_site["connecticum.de"].skip
    assert "Ihre Aufgaben" not in page("connecticum_detail.html")


def test_site_of():
    assert site_of("https://de.linkedin.com/jobs/view/1") == "linkedin.com"
    assert site_of("https://www.connecticum.de/jobs/x") == "connecticum.de"


# ---------- fetching and storing ----------


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "werkbot.db"
    db.init_db(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def foorilla_job(n: int, title="Working Student Analytics Engineering (f/m/x)") -> Job:
    return Job(
        title=title,
        company="Unknown",
        url=f"https://foorilla.com/hiring/jobs/job-{n}/",
        source="foorilla",
        source_job_id=str(n),
        location="Berlin (Zentrale)",
    )


def json_ld_page(company: str, description: str) -> str:
    data = {"@type": "JobPosting", "title": "x", "description": description,
            "hiringOrganization": {"@type": "Organization", "name": company}}  # fmt: skip
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head></html>'
    )


RULES = DetailRules(delay_s=0, min_description_chars=50)
NOW = datetime(2026, 10, 20, tzinfo=UTC)


def test_fetch_stores_the_description_and_merges_a_foorilla_duplicate(conn):
    """foorilla has no company in its list. Once the job page names it, the job turns out
    to be a duplicate of the same job from Arbeitnow and is merged into it."""
    arbeitnow = Job(
        title="Working Student Analytics Engineering (f/m/x)",
        company="Thermondo",
        url="https://www.arbeitnow.com/jobs/companies/thermondo/working-student-x",
        source="arbeitnow",
        source_job_id="x",
        location="Berlin (Zentrale)",
    )
    save_jobs(conn, [arbeitnow, foorilla_job(1)], {}, ALIASES)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2

    text = "We are looking for a working student in analytics engineering. " * 5
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=json_ld_page("Thermondo GmbH", text))
    )
    foorilla_id = conn.execute("SELECT id FROM jobs WHERE main_source = 'foorilla'").fetchone()[0]
    rows = jobs_needing_details(conn, RULES, [foorilla_id], NOW)
    stats = fetch_details(conn, rows, RULES, Settings(), ALIASES, transport, sleep=lambda s: None)

    assert (stats.ok, stats.companies_found, stats.merged) == (1, 1, 1)
    jobs = conn.execute("SELECT company, description FROM jobs").fetchall()
    assert len(jobs) == 1 and jobs[0]["company"] == "Thermondo"
    assert jobs[0]["description"].startswith("We are looking")
    assert conn.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0] == 2
    assert db.find_duplicates(conn) == {}


def test_failed_pages_are_retried_only_after_some_days(conn):
    save_jobs(conn, [foorilla_job(1)], {}, ALIASES)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    rows = jobs_needing_details(conn, RULES, [job_id], datetime.now(UTC))
    stats = fetch_details(conn, rows, RULES, Settings(), ALIASES, transport, sleep=lambda s: None)

    assert stats.failed == 1
    assert jobs_needing_details(conn, RULES, [job_id], datetime.now(UTC)) == []
    later = datetime(2099, 1, 1, tzinfo=UTC)
    assert len(jobs_needing_details(conn, RULES, [job_id], later)) == 1


def test_a_rate_limiting_site_is_skipped_for_the_rest_of_the_run(conn):
    save_jobs(conn, [foorilla_job(n, title=f"Working Student {n}") for n in range(3)], {}, ALIASES)
    ids = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    rows = jobs_needing_details(conn, RULES, ids, NOW)
    stats = fetch_details(
        conn, rows, RULES, Settings(), ALIASES, httpx.MockTransport(handler), sleep=lambda s: None
    )
    assert len(calls) == 1 and stats.skipped_sites == {"foorilla.com"}


def test_per_site_limits_and_skipped_sites(conn):
    rules = DetailRules.model_validate(
        {
            "delay_s": 0,
            "per_site": {"foorilla.com": {"max_per_run": 2}, "connecticum.de": {"skip": True}},
        }
    )  # fmt: skip
    jobs = [foorilla_job(n, title=f"Working Student {n}") for n in range(4)]
    jobs.append(Job(title="Werkstudent", company="DB", url="https://www.connecticum.de/jobs/a-1",
                    source="connecticum", source_job_id="1", location="Mainz"))  # fmt: skip
    save_jobs(conn, jobs, {}, ALIASES)
    ids = [r[0] for r in conn.execute("SELECT id FROM jobs")]
    rows = jobs_needing_details(conn, rules, ids, NOW)
    assert all("connecticum" not in r["main_url"] for r in rows)  # skipped site: not even tried

    calls = []
    transport = httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, text="<p>x</p>")
    )
    fetch_details(conn, rows, rules, Settings(), ALIASES, transport, sleep=lambda s: None)
    assert len(calls) == 2  # max_per_run for foorilla.com


def test_site_headers_failure_reasons_and_retrying_now(conn):
    """foorilla needs HX-Request to send the ad; failures are counted by reason; with
    retry_all, pages that failed a moment ago are tried again."""
    rules = DetailRules.model_validate(
        {"delay_s": 0, "per_site": {"foorilla.com": {"headers": {"HX-Request": "true"}}}}
    )
    save_jobs(conn, [foorilla_job(1)], {}, ALIASES)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
    seen = []

    def handler(request):
        seen.append(request.headers.get("HX-Request"))
        return httpx.Response(404)

    rows = jobs_needing_details(conn, rules, [job_id], NOW)
    stats = fetch_details(conn, rows, rules, Settings(), ALIASES, httpx.MockTransport(handler),
                          sleep=lambda s: None)  # fmt: skip
    assert seen == ["true"]
    assert stats.fail_reasons == {"foorilla.com: HTTP 404": 1}
    later = datetime.now(UTC)
    assert jobs_needing_details(conn, rules, [job_id], later) == []
    assert len(jobs_needing_details(conn, rules, [job_id], later, retry_all=True)) == 1


def test_foorilla_has_the_htmx_header_in_the_config():
    from werkbot.filters.config import load_filter_config

    rules = load_filter_config(ROOT / "data" / "filters.yaml").details
    assert rules.per_site["foorilla.com"].headers == {"HX-Request": "true"}


WAD_MD = """# Working Student IT Helpdesk

**Company:** Isar Aerospace
**Location:** München, Germany

## Description

Your tasks: set up laptops and accounts, support users in the office and on the shop
floor, troubleshoot Windows, Microsoft 365, printers and networks. [Apply](https://x.example)

Your profile: enrolled in IT or computer science, fluent English, German is a plus.

## Related videos

- [Some talk](https://www.wearedevelopers.com/videos/1)
"""


def test_a_site_can_be_read_as_markdown_from_another_url(conn):
    """`rewrite` + `format: markdown`, e.g. a site's Markdown version of its job pages.
    (Written for WeAreDevelopers' /jobs/<id>.md, which turned out to cover only its own
    ads; kept for sites that offer the same.)"""
    rules = DetailRules.model_validate({"delay_s": 0, "per_site": {"wearedevelopers.com": {
        "rewrite": [r"^/jobs/(?:ext/)?(\d+)[^/]*/?$", r"/jobs/\1.md"],
        "format": "markdown"}}})  # fmt: skip
    link = "https://www.wearedevelopers.com/jobs/ext/3157252-working-student-it-helpdesk"
    job = Job(title="Working Student IT Helpdesk", company="Isar Aerospace", url=link,
              source="wearedevelopers", source_job_id="3157252", location="München")  # fmt: skip
    save_jobs(conn, [job], {}, ALIASES)
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200, text=WAD_MD)

    rows = jobs_needing_details(conn, rules, [1], NOW)
    stats = fetch_details(conn, rows, rules, Settings(), ALIASES, httpx.MockTransport(handler),
                          sleep=lambda s: None)  # fmt: skip
    assert asked == ["https://www.wearedevelopers.com/jobs/3157252.md"]
    description = conn.execute("SELECT description FROM jobs").fetchone()[0]
    assert stats.ok == 1 and description.startswith("Working Student IT Helpdesk")
    assert "Your profile: enrolled in IT" in description and "Apply" in description
    assert "](" not in description and "Some talk" not in description  # no links, no extras
