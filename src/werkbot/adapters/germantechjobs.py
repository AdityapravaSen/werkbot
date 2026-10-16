"""GermanTechJobs: public JSON API used by the site itself (moved to Group A).

    GET https://germantechjobs.de/api/jobsLight   -> [job, ...]  (all active jobs, one request)
    job = {"_id", "jobUrl" (slug), "name", "company", "actualCity", "cityCategory",
           "workplace" ("office"/"hybrid"/"remote"), "language" ("German"/"English"),
           "jobType" ("Full-Time", ...), "activeFrom" (ISO date), "redirectJobUrl", ...}

The website itself is a JavaScript app (the HTML is an empty shell), and its RSS
feed is stale, so the API is the only usable source. Filters run on our side.
"""

from collections import Counter
from datetime import datetime

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job

JOB_PAGE = "https://germantechjobs.de/jobs/{slug}"


@register
class GermanTechJobsAdapter(Adapter):
    name = "germantechjobs"

    def fetch(self) -> list[Job]:
        params = self.source.params
        want_types = [t.lower() for t in params.get("job_types", [])]
        want_langs = [lang.lower() for lang in params.get("languages", [])]

        try:
            with self.client() as client:
                items = self.get(client, self.source.url).json()
        except (httpx.HTTPError, ValueError) as exc:
            self.warn(f"API could not be loaded ({exc.__class__.__name__})")
            return []
        if not isinstance(items, list):
            self.warn(f"expected a list of jobs, got {type(items).__name__}; the API changed")
            return []

        # Logged so you can see which values exist and tune the filters in sources.yaml
        types = Counter(str(i.get("jobType")) for i in items)
        langs = Counter(str(i.get("language")) for i in items)
        logger.info(
            "germantechjobs: {} jobs; jobType {}; language {}", len(items), dict(types), dict(langs)
        )

        jobs: dict[str, Job] = {}
        for item in items:
            job_type = str(item.get("jobType") or "").lower()
            language = str(item.get("language") or "").lower()
            if want_types and not any(t in job_type for t in want_types):
                continue
            if want_langs and language not in want_langs:
                continue
            job = _to_job(item)
            if job:
                jobs.setdefault(job.source_job_id, job)

        if items and not jobs and not (want_types or want_langs):
            self.warn("API returned jobs but none were readable; the format changed")
        return list(jobs.values())


def _to_job(item: dict) -> Job | None:
    slug, title = item.get("jobUrl"), item.get("name")
    if not slug or not title:
        return None
    language = str(item.get("language") or "")
    active_from = item.get("activeFrom")
    return Job(
        title=title.strip(),
        company=(item.get("company") or "Unknown").strip(),
        url=JOB_PAGE.format(slug=slug),
        apply_url=item.get("redirectJobUrl") or None,
        source="germantechjobs",
        source_job_id=str(item.get("_id") or slug),
        location=item.get("actualCity") or item.get("cityCategory"),
        remote=str(item.get("workplace") or "").lower() == "remote",
        date_posted=datetime.fromisoformat(active_from) if active_from else None,
        job_types=[t for t in [item.get("jobType"), item.get("expLevel")] if t],
        german_required=(language.lower() == "german") if language else None,
    )
