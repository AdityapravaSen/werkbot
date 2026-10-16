"""softgarden career sites: public JSON-LD job feed (Group A).

Every softgarden career site publishes  https://<company>.career.softgarden.de/jobs.feed.json
Format (checked live, Oct 2026):
    {"numberOfItems": 76,
     "dataFeedElement": [{"item": {"title", "url", "datePosted", "description" (HTML),
                                   "identifier": {"value"}, "employmentType",
                                   "hiringOrganization": {"name"},
                                   "jobLocation": {"address": {"addressLocality", ...}}}}]}

The feed has no category field, so the website's "IT / Engineering" filter is
approximated with `title_keywords`. `locations` keeps only those cities.
"""

from datetime import datetime

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job
from werkbot.text import html_to_text, matches_keyword


@register
class SoftgardenFeedAdapter(Adapter):
    name = "softgarden_feed"

    def fetch(self) -> list[Job]:
        params = self.source.params
        locations = [loc.lower() for loc in params.get("locations", [])]
        title_keywords = params.get("title_keywords", [])

        try:
            with self.client() as client:
                payload = self.get(client, self.source.url).json()
        except httpx.HTTPError as exc:
            self._warn(f"feed could not be loaded ({exc.__class__.__name__})")
            return []

        elements = payload.get("dataFeedElement") or []
        total = payload.get("numberOfItems")
        if total and not elements:
            self._warn(
                f"feed reports {total} jobs but none were readable; keys: {sorted(payload)}. "
                "The adapter needs updating."
            )

        jobs: dict[str, Job] = {}
        skipped_location = skipped_keyword = 0
        for element in elements:
            item = element.get("item") or element
            job = _to_job(item, source=self.source.name)
            if job is None:
                continue
            if locations and (job.location or "").lower() not in locations:
                skipped_location += 1
                continue
            if title_keywords and not matches_keyword(job.title, title_keywords):
                skipped_keyword += 1
                continue
            jobs.setdefault(job.source_job_id, job)

        logger.info(
            "{}: {} in feed, {} kept ({} other location, {} no title keyword)",
            self.source.name,
            len(elements),
            len(jobs),
            skipped_location,
            skipped_keyword,
        )
        return list(jobs.values())

    def _warn(self, message: str) -> None:
        logger.warning("{}: {}", self.source.name, message)
        self.warnings.append(message)


def _to_job(item: dict, source: str) -> Job | None:
    title, url = item.get("title"), item.get("url")
    if not title or not url:
        return None
    ident = item.get("identifier") or {}
    org = item.get("hiringOrganization") or {}
    employment = item.get("employmentType") or []
    return Job(
        title=title.strip(),
        company=(org.get("name") or ident.get("name") or "Unknown").strip(),
        url=url,
        source=source,
        source_job_id=str(ident.get("value") or url),
        location=_locality(item.get("jobLocation")),
        description=html_to_text(item.get("description")),
        date_posted=_parse_datetime(item.get("datePosted")),
        job_types=[employment] if isinstance(employment, str) else list(employment),
    )


def _locality(location) -> str | None:
    """jobLocation can be one place or a list of places."""
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return None
    return (location.get("address") or {}).get("addressLocality")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None