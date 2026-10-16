"""LinkedIn via python-jobspy (Group E).

python-jobspy reads LinkedIn's public, logged-out job search pages. There is no free
official job-search API. Keep it light: a couple of searches a day, never logged in.

Install the optional extra first:  uv sync --group dev --extra linkedin

Each source in sources.yaml is one search, e.g.
    params: {search_term: working student, location: "Kaiserslautern, Germany",
             distance: 100, hours_old: 168, results_wanted: 50}
`distance` is in MILES (100 miles ~ 160 km).
"""

import math
from datetime import UTC, date, datetime

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job

# Parameters passed straight to jobspy.scrape_jobs (anything else in params is ours)
JOBSPY_PARAMS = {
    "search_term",
    "location",
    "distance",
    "is_remote",
    "job_type",
    "hours_old",
    "results_wanted",
    "linkedin_fetch_description",
    "offset",
}


@register
class LinkedInAdapter(Adapter):
    name = "linkedin"

    def fetch(self) -> list[Job]:
        query = {k: v for k, v in self.source.params.items() if k in JOBSPY_PARAMS}
        try:
            rows = self._scrape(**query)
        except ImportError:
            self.warn("python-jobspy is not installed; run: uv sync --group dev --extra linkedin")
            return []
        except Exception as exc:  # jobspy raises various errors when LinkedIn blocks us
            self.warn(f"LinkedIn search failed ({exc.__class__.__name__}: {exc})")
            return []

        jobs: dict[str, Job] = {}
        for row in rows:
            job = _to_job(_clean(row), source=self.source.name)
            if job:
                jobs.setdefault(job.source_job_id, job)
        return list(jobs.values())

    def _scrape(self, **query) -> list[dict]:
        """Run jobspy and return plain dicts. Tests replace this method."""
        from jobspy import scrape_jobs  # optional dependency, imported only when used

        df = scrape_jobs(site_name=["linkedin"], verbose=0, **query)
        return df.to_dict("records")


def _clean(row: dict) -> dict:
    """pandas fills missing values with NaN; turn those into None."""
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}


def _to_job(row: dict, source: str) -> Job | None:
    title, url = row.get("title"), row.get("job_url")
    if not title or not url:
        return None
    job_type = row.get("job_type")
    return Job(
        title=str(title).strip(),
        company=str(row.get("company") or "Unknown").strip(),
        url=url,
        apply_url=row.get("job_url_direct") or None,
        source=source,
        source_job_id=str(row.get("id") or url),
        location=row.get("location") or None,
        remote=bool(row.get("is_remote")),
        description=str(row.get("description") or ""),
        date_posted=_to_datetime(row.get("date_posted")),
        job_types=[str(job_type)] if job_type else [],
    )


def _to_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).replace(tzinfo=UTC)
        except ValueError:
            return None
    return None
