"""Merge rules (PLAN.md section 3.3): duplicates become one record, nothing is thrown away.

- Main URL: the employer's own site > job boards > aggregators (`kind` in sources.yaml).
- Description: the longest one.
- Date posted: the earliest one.
- Every source and link is still stored (job_sources), so the output can say
  "seen on: Fraunhofer, connecticum".
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from werkbot.models import Job

KIND_RANK = {"employer": 0, "board": 1, "aggregator": 2}


def rank(source: str, kinds: dict[str, str]) -> int:
    """Lower is better. Unknown sources count as job boards."""
    return KIND_RANK.get(kinds.get(source, "board"), KIND_RANK["board"])


def pick_main(jobs: list[Job], kinds: dict[str, str]) -> Job:
    """The record whose link and title we show: best source kind, then one with a
    description, then the order the sources ran in."""
    return min(
        enumerate(jobs), key=lambda p: (rank(p[1].source, kinds), not p[1].description, p[0])
    )[1]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def earliest(*dates: datetime | None) -> datetime | None:
    known = [_utc(d) for d in dates if d is not None]
    return min(known) if known else None


def combine_german_required(*values: bool | None) -> bool | None:
    """If any source says German is required, it is. Unknown only if no source knows."""
    if any(v is True for v in values):
        return True
    if any(v is False for v in values):
        return False
    return None


@dataclass
class Merged:
    """One job after merging its duplicates."""

    main: Job
    description: str
    date_posted: datetime | None
    remote: bool
    german_required: bool | None
    job_types: list[str]
    apply_url: str | None
    location: str | None


def merge(jobs: list[Job], kinds: dict[str, str]) -> Merged:
    main = pick_main(jobs, kinds)
    job_types: list[str] = []
    for job in jobs:
        job_types += [t for t in job.job_types if t not in job_types]
    others = [j for j in jobs if j is not main]
    return Merged(
        main=main,
        description=max((j.description for j in jobs), key=len),
        date_posted=earliest(*(j.date_posted for j in jobs)),
        remote=any(j.remote for j in jobs),
        german_required=combine_german_required(*(j.german_required for j in jobs)),
        job_types=job_types,
        apply_url=next(
            (str(j.apply_url) for j in [main, *others] if j.apply_url is not None), None
        ),
        location=next((j.location for j in [main, *others] if j.location), None),
    )
