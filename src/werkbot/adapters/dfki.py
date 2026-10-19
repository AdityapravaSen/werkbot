"""DFKI: JSON feed behind jobs.dfki.de (Hireserve ATS), found with scripts/probe_network.py.

    GET https://jobs.dfki.de/utf8/ic_job_feeds.feed_engine?p_web_site_id=6635&p_published_to=WWW
        &p_language=DEFAULT&p_direct=Y&p_format=MOBILE&p_include_exclude_from_list=N
        &p_category_code_arr=23427-662551&p_search=&p_summary=Y
    -> {"jobs": [job, ...], "total": 2}

    job = {"id", "title", "weblink", "publication": {"internet": {"publish_date", "closing_date"}},
           "classifications": {"class_24066": EN long title, "class_24025": DE long title,
                               "class_23805": job type, "class_23438": research area,
                               "class_23605": city}}

Filter codes (p_category_code_arr, can be repeated):
    23427-662551 Teilzeit · 23805-667250 Wissenschaftl. Hilfskraft · 23805-667248 Praktikum
    23605-661022 Kaiserslautern · 23605-661019 Saarbrücken · 23605-690510 Darmstadt
"""

from datetime import UTC, datetime

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job

FIXED_PARAMS = {
    "p_web_site_id": "6635",
    "p_published_to": "WWW",
    "p_language": "DEFAULT",
    "p_direct": "Y",
    "p_format": "MOBILE",
    "p_include_exclude_from_list": "N",
    "p_search": "",
    "p_summary": "Y",
}
TITLE_EN, TITLE_DE = "class_24066", "class_24025"
JOB_TYPE, AREA, CITY = "class_23805", "class_23438", "class_23605"


@register
class DfkiAdapter(Adapter):
    name = "dfki"

    def fetch(self) -> list[Job]:
        params = {
            **FIXED_PARAMS,
            "p_category_code_arr": self.source.params.get("category_codes", []),
        }
        try:
            with self.client() as client:
                payload = self.get(client, self.source.url, **params).json()
        except (httpx.HTTPError, ValueError) as exc:
            self.warn(f"feed could not be loaded ({exc.__class__.__name__})")
            return []

        items = payload.get("jobs") if isinstance(payload, dict) else None
        total = payload.get("total") if isinstance(payload, dict) else None
        if items is None or (total and not items):
            self.warn(f"feed reports {total} jobs but none were readable; the format changed")
            return []

        jobs: dict[str, Job] = {}
        for item in items:
            job = _to_job(item)
            if job:
                jobs.setdefault(job.source_job_id, job)
        logger.info("{}: {} jobs in feed (total {})", self.source.name, len(jobs), total)
        return list(jobs.values())


def _class_value(item: dict, key: str) -> str:
    values = ((item.get("classifications") or {}).get(key) or {}).get("values") or []
    return str(values[0].get("class_val") or "").strip() if values else ""


def _to_job(item: dict) -> Job | None:
    link = item.get("weblink")
    title = _class_value(item, TITLE_EN) or _class_value(item, TITLE_DE) or item.get("title")
    if not link or not title:
        return None
    area = _class_value(item, AREA)
    internet = (item.get("publication") or {}).get("internet") or {}
    return Job(
        title=title,
        company=f"DFKI – {area}" if area else "DFKI",
        url=link,
        source="dfki",
        source_job_id=str(item.get("id") or link),
        location=_class_value(item, CITY) or None,
        date_posted=_parse(internet.get("publish_date")),
        job_types=[t for t in [_class_value(item, JOB_TYPE), item.get("title")] if t],
    )


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) if value else None
    except ValueError:
        return None
