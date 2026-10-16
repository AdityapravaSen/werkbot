"""Fraunhofer: SAP SuccessFactors career site, HTML results table (Group B).

    <span class="paginationLabel">Ergebnisse <b>1 – 10</b> von <b>10</b></span>
    <tr class="data-row">
      <a class="jobTitle-link" href="/job/Kaiserslautern-STUDENT-...-67663/1446740233/">Title</a>
      <span class="jobShifttype">Kaiserslautern</span>
      <span class="jobFacility">IESE - Experimentelles Software Engineering</span>
More pages: add &startrow=10, 20, ...  The posted date is only on the detail page.
"""

import re
from urllib.parse import urljoin

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.adapters.html import parse_html, text_of
from werkbot.models import Job

BASE = "https://jobs.fraunhofer.de"
TOTAL = re.compile(r"(?:von|of)\s*(?:<b>)?\s*(\d+)", re.I)
JOB_ID = re.compile(r"/(\d+)/?$")


@register
class FraunhoferAdapter(Adapter):
    name = "fraunhofer"

    def fetch(self) -> list[Job]:
        max_pages = int(self.source.params.get("max_pages", 10))
        jobs: dict[str, Job] = {}
        startrow = 0

        with self.client() as client:
            for page in range(1, max_pages + 1):
                params = {"startrow": startrow} if startrow else {}
                try:
                    html = self.get(client, self.source.url, **params).text
                except httpx.HTTPError as exc:
                    self.warn(f"stopped at page {page} ({exc.__class__.__name__}); partial")
                    break

                tree = parse_html(html)
                rows = tree.css("tr.data-row")
                for row in rows:
                    job = _to_job(row)
                    if job:
                        jobs.setdefault(job.source_job_id, job)

                label = tree.css_first(".paginationLabel")
                match = TOTAL.search(label.html if label else "")
                total = int(match.group(1)) if match else None
                logger.info(
                    "fraunhofer page {}: {} rows, {} of {} total", page, len(rows), len(jobs), total
                )
                if page == 1 and not rows and total:
                    self.warn(f"page says {total} jobs but no table rows found; the HTML changed")

                startrow += len(rows)
                if not rows or total is None or startrow >= total:
                    break
                self.pause()

        return list(jobs.values())


def _to_job(row) -> Job | None:
    link = row.css_first("a.jobTitle-link")
    href = link.attributes.get("href") if link else None
    if not href:
        return None
    facility = text_of(row.css_first(".jobFacility"))
    institute = facility.split(" - ")[0].strip() if facility else ""
    match = JOB_ID.search(href)
    return Job(
        title=text_of(link),
        company=f"Fraunhofer {institute}".strip(),
        url=urljoin(BASE, href),
        source="fraunhofer",
        source_job_id=match.group(1) if match else href,
        location=text_of(row.css_first(".jobShifttype")) or None,
        job_types=[facility] if facility else [],
    )
