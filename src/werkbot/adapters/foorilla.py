"""foorilla: server-rendered HTML fragments (Django + htmx), found with scripts/probe_network.py.

    GET https://foorilla.com/hiring/jobs/?job_search=working+student&page=N
    -> <li class="list-group-item"> per job:
         <a class="terminal-title" hx-get="/hiring/jobs/<slug>-<id>/">Title</a>
         <div class="terminal-meta"><small>2h ago</small></div>
         <small class="text-warning-emphasis">[EN]</small>            (experience level)
         <small class="text-body-secondary">[Internship][Part Time]</small>
         <div class="text-end"><small>Bratislava, Slovakia <span>[R]</span></small></div>
       The last item carries hx-get="/hiring/jobs/?page=N+1" when more pages exist.

The site's own filters (Part-time / English / Germany) live in the browser session, not the
URL, so we use its quick search (job_search) and filter by location on our side.
The list has no company name; that comes later with the job details.
"""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.adapters.html import parse_html, text_of
from werkbot.models import Job
from werkbot.text import matches_keyword

BASE = "https://foorilla.com"
JOB_ID = re.compile(r"-(\d+)/?$")
AGE = re.compile(r"(\d+)\s*(mo|y|w|d|h|m)\b")
AGE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


@register
class FoorillaAdapter(Adapter):
    name = "foorilla"

    def fetch(self) -> list[Job]:
        params = self.source.params
        searches = params.get("search_terms", ["working student", "werkstudent"])
        locations = params.get("location_keywords", [])
        max_pages = int(params.get("max_pages", 5))

        jobs: dict[str, Job] = {}
        skipped = 0
        with self.client() as client:
            client.headers["HX-Request"] = "true"  # ask for the fragment, like the site does
            for term in searches:
                for page in range(1, max_pages + 1):
                    query = {"job_search": term, **({"page": page} if page > 1 else {})}
                    try:
                        html = self.get(client, self.source.url, **query).text
                    except httpx.HTTPError as exc:
                        self.warn(f"'{term}' stopped at page {page} ({exc.__class__.__name__})")
                        break

                    page_jobs = parse_jobs(html)
                    for job in page_jobs:
                        if locations and not matches_keyword(job.location or "", locations):
                            skipped += 1
                            continue
                        jobs.setdefault(job.source_job_id, job)

                    logger.info(
                        "foorilla '{}' page {}: {} listed, {} kept so far",
                        term,
                        page,
                        len(page_jobs),
                        len(jobs),
                    )
                    if page == 1 and not page_jobs and "list-group" in html:
                        self.warn("results loaded but no job items found; the HTML changed")
                    if not page_jobs or f"page={page + 1}" not in html:
                        break
                    self.pause()

        if skipped:
            logger.info("foorilla: {} jobs outside the configured locations skipped", skipped)
        return list(jobs.values())


def parse_jobs(html: str, now: datetime | None = None) -> list[Job]:
    now = now or datetime.now(UTC)
    jobs = []
    for item in parse_html(html).css("li.list-group-item"):
        link = item.css_first("a.terminal-title")
        path = link.attributes.get("hx-get") if link else None
        if not path or not path.startswith("/hiring/jobs/") or "?" in path:
            continue
        title = re.sub(r"^(Feat\.\s*)?(Featured\s*)?", "", text_of(link)).strip()
        meta = item.css("small.text-body-secondary")
        job_types = re.findall(r"\[([^\]]+)\]", " ".join(text_of(m) for m in meta))
        place = item.css_first(".text-end small")
        place_text = text_of(place)
        match = JOB_ID.search(path)
        jobs.append(
            Job(
                title=title,
                company="Unknown",  # not in the list; filled in later from the job page
                url=urljoin(BASE, path),
                source="foorilla",
                source_job_id=match.group(1) if match else path,
                location=re.sub(r"\s*\[(R|WH)\]\s*$", "", place_text) or None,
                remote="[R]" in place_text,
                date_posted=_age_to_date(text_of(item.css_first(".terminal-meta small")), now),
                job_types=job_types,
            )
        )
    return jobs


def _age_to_date(text: str, now: datetime) -> datetime | None:
    """'2h ago' / '6d ago' / '1mo ago' -> approximate date."""
    m = AGE.search(text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit == "mo":
        return now - timedelta(days=30 * n)
    if unit == "y":
        return now - timedelta(days=365 * n)
    return now - timedelta(**{AGE_UNITS[unit]: n})
