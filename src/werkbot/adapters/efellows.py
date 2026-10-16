"""e-fellows.net jobs: data embedded by Next.js in <script id="__NEXT_DATA__"> (Group B).

    props.pageProps.data.jobs = {"result_count": 14, "pages": [job, ...]}   (10 per page)
    job = {"id", "title", "organization", "url": {"path"}, "published": ISO date,
           "address": ["München"], "remoteOptions": [...], "occupationalField": [...]}

Reading this JSON is more stable than the HTML, whose CSS class names change on every deploy.
"""

from datetime import datetime
from urllib.parse import urljoin

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.adapters.html import next_data
from werkbot.models import Job

BASE = "https://jobs.e-fellows.net"


@register
class EFellowsAdapter(Adapter):
    name = "efellows"

    def fetch(self) -> list[Job]:
        max_pages = int(self.source.params.get("max_pages", 10))
        jobs: dict[str, Job] = {}

        with self.client() as client:
            for page in range(1, max_pages + 1):
                try:
                    html = self.get(client, self.source.url, page=page).text
                except httpx.HTTPError as exc:
                    self.warn(f"stopped at page {page} ({exc.__class__.__name__}); partial")
                    break

                data = next_data(html)
                try:
                    result = data["props"]["pageProps"]["data"]["jobs"]
                except (TypeError, KeyError):
                    self.warn("page has no __NEXT_DATA__ job list; the site changed")
                    break

                items = result.get("pages") or []
                for item in items:
                    job = _to_job(item)
                    if job:
                        jobs.setdefault(job.source_job_id, job)

                total = result.get("result_count")
                logger.info(
                    "efellows page {}: {} jobs, {} of {} total", page, len(items), len(jobs), total
                )
                if not items or (total is not None and len(jobs) >= total):
                    break
                self.pause()

        return list(jobs.values())


def _to_job(item: dict) -> Job | None:
    path = (item.get("url") or {}).get("path") or item.get("urlNoPrefix")
    if not item.get("title") or not path:
        return None
    published = item.get("published")
    return Job(
        title=item["title"].strip(),
        company=(item.get("organization") or "Unknown").strip(),
        url=urljoin(BASE, path),
        source="efellows",
        source_job_id=str(item.get("id") or path),
        location=", ".join(item.get("address") or []) or None,
        remote=bool(item.get("remoteOptions")),
        date_posted=datetime.fromisoformat(published) if published else None,
        job_types=[f["label"] for f in item.get("occupationalField") or [] if f.get("label")],
    )
