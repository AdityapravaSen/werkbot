"""connecticum: HTML job cards (Group B).

Each job (checked live, Oct 2026):
    <article class="cnn3-c-jobcard ..." id="job1862399">
      <p class="cnn3-c-jobcard__title"><a href="https://www.connecticum.de/jobs/...-1862399">Title</a>
      <p class="cnn3-c-jobcard__company"><a ...>Company</a>
      <div class="cnn3-c-jobcard__meta">
        <a ...>Werkstudentenjob</a> <a href=".../p296-mainz-jobs">Mainz</a>
      <span class="cnn3-c-jobcard__date">16.10.2026</span>
Pages are linked with <a rel="next" href="...&page=2...">.
"""

import re
from datetime import UTC, datetime

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.adapters.html import parse_html, text_of
from werkbot.models import Job

LOCATION_LINK = re.compile(r"/p\d+-[^/]+-jobs")


@register
class ConnecticumAdapter(Adapter):
    name = "connecticum"

    def fetch(self) -> list[Job]:
        max_pages = int(self.source.params.get("max_pages", 15))
        url: str | None = self.source.url
        jobs: dict[str, Job] = {}

        with self.client() as client:
            for page in range(1, max_pages + 1):
                if not url:
                    break
                try:
                    html = self.get(client, url).text
                except httpx.HTTPError as exc:
                    self.warn(f"stopped at page {page} ({exc.__class__.__name__}); partial")
                    break

                tree = parse_html(html)
                cards = tree.css("article.cnn3-c-jobcard")
                for card in cards:
                    job = _to_job(card)
                    if job:
                        jobs.setdefault(job.source_job_id, job)

                logger.info(
                    "connecticum page {}: {} cards, {} jobs so far", page, len(cards), len(jobs)
                )
                if page == 1 and not cards and "Seite 1 von" in html:
                    self.warn("results page loaded but no job cards found; the HTML changed")

                next_link = tree.css_first('a[rel="next"]')
                url = next_link.attributes.get("href") if next_link else None
                if url:
                    self.pause()

        return list(jobs.values())


def _to_job(card) -> Job | None:
    link = card.css_first(".cnn3-c-jobcard__title a")
    if link is None or not link.attributes.get("href"):
        return None
    job_id = (card.attributes.get("id") or "").removeprefix("job") or link.attributes["href"]

    location, job_types = None, []
    for meta in card.css(".cnn3-c-jobcard__meta a"):
        if LOCATION_LINK.search(meta.attributes.get("href") or ""):
            location = text_of(meta)
        else:
            job_types.append(text_of(meta))

    return Job(
        title=text_of(link),
        company=text_of(card.css_first(".cnn3-c-jobcard__company")) or "Unknown",
        url=link.attributes["href"],
        source="connecticum",
        source_job_id=job_id,
        location=location,
        date_posted=_parse_date(text_of(card.css_first(".cnn3-c-jobcard__date"))),
        job_types=job_types,
    )


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y").replace(tzinfo=UTC)
    except ValueError:
        return None
