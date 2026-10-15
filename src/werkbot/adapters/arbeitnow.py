"""Arbeitnow: public JSON API (Group A).

API: https://www.arbeitnow.com/api/job-board-api?page=N
Each page returns {"data": [...], "links": {"next": ...}, ...}.
The API has no search parameter, so we page through everything (newest first)
and keep jobs that match the keywords and are within `max_age_days`.
"""

from datetime import UTC, datetime, timedelta

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job
from werkbot.text import contains_any, html_to_text

DEFAULT_KEYWORDS = ["working student", "werkstudent", "studentische", "student assistant"]


@register
class ArbeitnowAdapter(Adapter):
    name = "arbeitnow"

    def fetch(self) -> list[Job]:
        keywords = self.source.params.get("keywords", DEFAULT_KEYWORDS)
        max_pages = int(self.source.params.get("max_pages", 50))
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.max_age_days)

        jobs: list[Job] = []
        with self.client() as client:
            for page in range(1, max_pages + 1):
                try:
                    payload = self.get(client, self.source.url, page=page).json()
                except httpx.HTTPError as exc:
                    # Keep what we already have instead of losing every page.
                    msg = f"stopped at page {page} ({exc.__class__.__name__}); results are partial"
                    logger.warning("arbeitnow: {}", msg)
                    self.warnings.append(msg)
                    break
                items = payload.get("data") or []
                if not items:
                    break

                all_too_old = True
                for item in items:
                    posted = _parse_timestamp(item.get("created_at"))
                    if posted is None or posted >= cutoff:
                        all_too_old = False
                    else:
                        continue  # older than max_age_days
                    if _matches(item, keywords):
                        jobs.append(_to_job(item, posted))

                logger.info(
                    "arbeitnow page {}: {} items, {} matches so far", page, len(items), len(jobs)
                )

                # Results are newest first: once a whole page is too old, stop.
                if all_too_old:
                    break
                links = payload.get("links")
                if isinstance(links, dict) and not links.get("next"):
                    break  # last page
                self.pause()

        return jobs


def _matches(item: dict, keywords: list[str]) -> bool:
    haystack = " ".join(
        [item.get("title") or "", *(item.get("tags") or []), *(item.get("job_types") or [])]
    )
    return contains_any(haystack, keywords)


def _parse_timestamp(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _to_job(item: dict, posted: datetime | None) -> Job:
    return Job(
        title=item["title"].strip(),
        company=(item.get("company_name") or "Unknown").strip(),
        url=item["url"],
        source="arbeitnow",
        source_job_id=item.get("slug"),
        location=(item.get("location") or "").strip() or None,
        remote=bool(item.get("remote")),
        description=html_to_text(item.get("description")),
        date_posted=posted,
        job_types=list(item.get("job_types") or []),
    )