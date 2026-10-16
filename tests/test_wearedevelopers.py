"""Tests for the WeAreDevelopers adapter. Fake pages copy the real Markdown (2026-10-16)."""

import httpx

from werkbot.adapters.wearedevelopers import WeAreDevelopersAdapter, parse_markdown
from werkbot.config import Settings
from werkbot.models import Source, SourceGroup

PAGE_1 = """# Developer Jobs

Region: Germany

3 jobs found matching "working student"

## Working Student Security Infrastructure Systems

- **Company:** Airbus Deutschland GmbH
- **Location:** München, Germany
- **Contract:** Temporary contract
- **Published:** July 18, 2026
- [View job](https://www.wearedevelopers.com/jobs/ext/1325535-working-student-security)
- [Apply](https://de.indeed.com/viewjob?jk=1a30d230db50bf9a)

## Working Student : IT Security

- **Company:** OHB Group
- **Location:** Remote, Germany
- **Contract:** Internship / Graduate position
- **Published:** September 2, 2026
- [View job](https://www.wearedevelopers.com/jobs/ext/2680723-working-student-it-security)
- [Apply](https://www.careerjet.de/jobad/de7538dc)

[Next page](https://www.wearedevelopers.com/jobs?country=DE&amp;page=2&amp;q=working+student)

## Countries

- [Germany](https://www.wearedevelopers.com/jobs?country=DE)
- [Spain](https://www.wearedevelopers.com/jobs?country=ES)
"""

PAGE_2 = """3 jobs found matching "working student"

## Working Student Data Engineering

- **Company:** Acme GmbH
- **Location:** Kaiserslautern, Germany
- **Published:** October 1, 2026
- [View job](https://www.wearedevelopers.com/jobs/ext/9999-working-student-data)
- [Apply](https://acme.example/apply)

## Working Student : IT Security

- **Company:** OHB Group
- **Location:** Remote, Germany
- [View job](https://www.wearedevelopers.com/jobs/ext/2680723-working-student-it-security)
"""


def _adapter(pages: dict[int, str], requested: list[int]) -> WeAreDevelopersAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested.append(page)
        return httpx.Response(200, text=pages.get(page, "0 jobs found"))

    source = Source(
        name="wearedevelopers",
        group=SourceGroup.API,
        adapter="wearedevelopers",
        url="https://www.wearedevelopers.com/jobs.md",
        params={"q": "working student", "country": "DE", "delay_s": 0},
    )
    settings = Settings(request_delay_s=0, max_retries=2, _env_file=None)
    return WeAreDevelopersAdapter(source, settings, transport=httpx.MockTransport(handler))


def test_parses_job_blocks():
    jobs = parse_markdown(PAGE_1)

    assert len(jobs) == 2  # the "Countries" block at the end is ignored
    job = jobs[0]
    assert job.title == "Working Student Security Infrastructure Systems"
    assert job.company == "Airbus Deutschland GmbH"
    assert job.location == "München, Germany"
    assert job.source_job_id == "1325535"
    assert str(job.apply_url) == "https://de.indeed.com/viewjob?jk=1a30d230db50bf9a"
    assert job.date_posted is not None and (job.date_posted.month, job.date_posted.day) == (7, 18)
    assert job.job_types == ["Temporary contract"]
    assert job.remote is False
    assert jobs[1].remote is True  # "Remote, Germany"


def test_follows_next_page_and_dedups():
    requested: list[int] = []
    jobs = _adapter({1: PAGE_1, 2: PAGE_2}, requested).fetch()

    assert [j.source_job_id for j in jobs] == ["1325535", "2680723", "9999"]
    assert requested == [1, 2]  # page 2 has no "Next page" link, so stop


def test_warns_when_format_changes():
    requested: list[int] = []
    adapter = _adapter({1: '204 jobs found matching "x"\n\n<div>new layout</div>'}, requested)

    assert adapter.fetch() == []
    assert adapter.warnings and "204 jobs but none were readable" in adapter.warnings[0]