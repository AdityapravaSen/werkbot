"""WeAreDevelopers: Markdown version of the job search (Group A).

    GET https://www.wearedevelopers.com/jobs.md?country=DE&q=working+student&page=N

Format (checked live, Oct 2026), 24 jobs per page:

    204 jobs found matching "working student"

    ## Working Student Security Infrastructure Systems

    - **Company:** Airbus Deutschland GmbH
    - **Location:** München, Germany
    - **Contract:** Temporary contract
    - **Published:** July 18, 2026
    - [View job](https://www.wearedevelopers.com/jobs/ext/1325535-working-student-...)
    - [Apply](https://de.indeed.com/viewjob?jk=...)

    ... [Next page](https://www.wearedevelopers.com/jobs?country=DE&page=3&q=working+student)

We read Markdown instead of the HTML cards because it is plain text and far less
likely to break when the site's design changes. The list has no description;
the job page link is kept so details can be fetched later.
"""

import re
from datetime import UTC, datetime

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job

FIELD = re.compile(r"^-\s*\*\*(?P<key>[^*:]+):\*\*\s*(?P<value>.+)$")
LINK = re.compile(r"^-\s*\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\)")
JOB_ID = re.compile(r"/jobs/(?:ext/)?(\d+)")
TOTAL = re.compile(r"(\d[\d.,]*)\s+jobs? found", re.I)


@register
class WeAreDevelopersAdapter(Adapter):
    name = "wearedevelopers"

    def fetch(self) -> list[Job]:
        params = self.source.params
        max_pages = int(params.get("max_pages", 15))
        query = {k: v for k, v in params.items() if k in {"q", "country"}}

        jobs: dict[str, Job] = {}
        with self.client() as client:
            for page in range(1, max_pages + 1):
                try:
                    text = self.get(client, self.source.url, **query, page=page).text
                except httpx.HTTPError as exc:
                    self._warn(f"stopped at page {page} ({exc.__class__.__name__}); partial")
                    break

                page_jobs = parse_markdown(text)
                for job in page_jobs:
                    jobs.setdefault(job.source_job_id, job)

                total = _total(text)
                logger.info(
                    "wearedevelopers page {}: {} jobs, {} of {} total",
                    page,
                    len(page_jobs),
                    len(jobs),
                    total,
                )
                if page == 1 and total and not page_jobs:
                    self._warn(
                        f"page says {total} jobs but none were readable; "
                        "the Markdown format changed. The adapter needs updating."
                    )
                if not page_jobs or "[Next page]" not in text:
                    break
                self.pause()

        return list(jobs.values())

    def _warn(self, message: str) -> None:
        logger.warning("wearedevelopers: {}", message)
        self.warnings.append(message)


def parse_markdown(text: str) -> list[Job]:
    """Split the page on '## ' headings; each block is one job."""
    jobs = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        lines = [line.strip() for line in block.strip().splitlines()]
        title = lines[0].strip() if lines else ""
        fields: dict[str, str] = {}
        links: dict[str, str] = {}
        for line in lines[1:]:
            if m := FIELD.match(line):
                fields[m["key"].strip().lower()] = m["value"].strip()
            elif m := LINK.match(line):
                links[m["label"].strip().lower()] = m["url"].replace("&amp;", "&")

        view_url = links.get("view job")
        if not title or not view_url:
            continue  # e.g. the "Countries" list at the end of the page
        id_match = JOB_ID.search(view_url)
        location = fields.get("location")
        jobs.append(
            Job(
                title=title,
                company=fields.get("company") or "Unknown",
                url=view_url,
                apply_url=links.get("apply"),
                source="wearedevelopers",
                source_job_id=id_match.group(1) if id_match else view_url,
                location=location,
                remote="remote" in (location or "").lower()
                or "remote" in fields.get("workplace", "").lower(),
                date_posted=_parse_date(fields.get("published")),
                job_types=[fields["contract"]] if fields.get("contract") else [],
            )
        )
    return jobs


def _parse_date(value: str | None) -> datetime | None:
    """'July 18, 2026' -> datetime."""
    if not value:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _total(text: str) -> int | None:
    m = TOTAL.search(text)
    return int(re.sub(r"[.,]", "", m.group(1))) if m else None