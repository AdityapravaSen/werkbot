"""workingstudentjobs.de: job data embedded in the Next.js page payload (Group B).

The HTML also contains a "Trending this week" sidebar with unrelated jobs, so we
don't scrape links. Instead we read the result objects from the page payload
(self.__next_f.push), which contain only the search results:

    {"id": uuid, "created_at": "$D2026-10-06T07:12:13.000Z", "title", "slug",
     "is_working_student", "is_english_required", "is_german_required",
     "working_mode": "hybrid", "company": {"display_name"},
     "primary_location": {"city_name", ...}, "skills": [...]}

The page shows "Showing 1-14 of 14"; more pages are requested with ?page=N.
"""

import re
from datetime import datetime

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.adapters.html import json_objects, next_flight_payload
from werkbot.models import Job

JOB_PAGE = "https://www.workingstudentjobs.de/jobs/{slug}"
JOB_OBJECT = r'\{"id":"[0-9a-f-]{36}","created_at"'
SHOWING = re.compile(r"Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)")


@register
class WorkingStudentJobsAdapter(Adapter):
    name = "workingstudentjobs"

    def fetch(self) -> list[Job]:
        max_pages = int(self.source.params.get("max_pages", 10))
        jobs: dict[str, Job] = {}

        with self.client() as client:
            for page in range(1, max_pages + 1):
                params = {"page": page} if page > 1 else {}
                try:
                    html = self.get(client, self.source.url, **params).text
                except httpx.HTTPError as exc:
                    self.warn(f"stopped at page {page} ({exc.__class__.__name__}); partial")
                    break

                before = len(jobs)
                found = 0
                for obj in json_objects(next_flight_payload(html), JOB_OBJECT):
                    job = _to_job(obj)
                    if job:
                        found += 1
                        jobs.setdefault(job.source_job_id, job)

                showing = SHOWING.search(html)
                shown_to, total = (int(showing[2]), int(showing[3])) if showing else (None, None)
                logger.info(
                    "workingstudentjobs page {}: {} jobs, {} of {} total",
                    page,
                    found,
                    len(jobs),
                    total,
                )
                if page == 1 and not found and total:
                    self.warn(
                        f"page says {total} jobs but none were readable; the page data changed"
                    )
                if total is None or shown_to is None or shown_to >= total:
                    break
                if len(jobs) == before:
                    self.warn(f"page {page} added no new jobs; paging may have changed")
                    break
                self.pause()

        return list(jobs.values())


def _to_job(obj: dict) -> Job | None:
    slug, title = obj.get("slug"), obj.get("title")
    if not slug or not title:
        return None
    company = (obj.get("company") or {}).get("display_name") or "Unknown"
    place = obj.get("primary_location") or {}
    created = str(obj.get("first_seen_at") or obj.get("created_at") or "").removeprefix("$D")
    job_types = [
        label
        for flag, label in [
            ("is_working_student", "Working Student"),
            ("is_internship", "Internship"),
        ]
        if obj.get(flag)
    ]
    german = obj.get("is_german_required")
    return Job(
        title=title.strip(),
        company=company.strip(),
        url=JOB_PAGE.format(slug=slug),
        source="workingstudentjobs",
        source_job_id=slug,
        location=place.get("city_name"),
        remote=obj.get("working_mode") == "remote",
        date_posted=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        job_types=job_types,
        german_required=german if isinstance(german, bool) else None,
    )
