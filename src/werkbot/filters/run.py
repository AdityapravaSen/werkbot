"""Apply the location, role and language filters to the jobs in the database."""

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from werkbot.filters.config import Decision, FilterConfig
from werkbot.filters.language import LanguageResult, judge_language
from werkbot.filters.location import LocationFilter, LocationResult, WorkMode, work_mode
from werkbot.filters.places import Gazetteer
from werkbot.filters.role import RoleResult, judge_role
from werkbot.models import Job, Source

_ORDER: dict[str, int] = {"keep": 0, "review": 1, "drop": 2}
_MODE_ORDER: dict[str, int] = {"onsite": 0, "hybrid": 1, "remote": 2}


@dataclass
class StoredJob:
    """One job from the database with every board's version of it."""

    id: int
    title: str
    company: str
    location: str | None
    description: str
    main_source: str
    main_url: str
    versions: list[Job] = field(default_factory=list)


@dataclass
class Verdict:
    decision: Decision
    reasons: list[str]
    location: LocationResult
    role: RoleResult
    language: LanguageResult | None = None


def load_jobs(conn, ids: list[int] | None = None) -> list[StoredJob]:
    """Every job that is not expired (or only `ids`)."""
    where = "status != 'expired'"
    args: tuple = ()
    if ids is not None:
        where += f" AND id IN ({','.join('?' * len(ids))})"
        args = tuple(ids)
    jobs = {
        r["id"]: StoredJob(
            r["id"],
            r["title"],
            r["company"],
            r["location"],
            r["description"] or "",
            r["main_source"],
            r["main_url"],
        )  # fmt: skip
        for r in conn.execute(
            f"SELECT id, title, company, location, description, main_source, main_url"
            f" FROM jobs WHERE {where} ORDER BY id",
            args,
        )
    }
    for r in conn.execute("SELECT job_id, data FROM job_sources ORDER BY id"):
        job = jobs.get(r["job_id"])
        if job is None:
            continue
        try:
            job.versions.append(Job.model_validate(json.loads(r["data"] or "{}")))
        except ValueError:
            logger.debug("job {}: unreadable source record skipped", job.id)
    return list(jobs.values())


class Filters:
    def __init__(self, config: FilterConfig, gazetteer: Gazetteer, sources: dict[str, Source]):
        self.config = config
        self.location = LocationFilter(config.location, gazetteer)
        self.sources = sources

    def judge(self, job: StoredJob, with_language: bool = True) -> Verdict:
        versions = job.versions or []
        description = max([job.description, *(v.description for v in versions)], key=len)

        mode, evidence = self._work_mode(job, versions)
        locations = [job.location, *(v.location for v in versions)]
        loc = self.location.judge(list(dict.fromkeys(locations)), mode, evidence)

        role = self._role(job, versions, description)

        lang = None
        if with_language:
            texts = [(job.title, job.description), *((v.title, v.description) for v in versions)]
            flag = any(v.german_required for v in versions)
            lang = judge_language(texts, self.config.language, flag)
            if lang.level == "none" and len(description) < 200:
                lang.decision, lang.reason = "review", "no description yet to check the language"

        parts = [("location", loc.decision, loc.reason), ("role", role.decision, role.reason)]
        if lang is not None:
            parts.append(("language", lang.decision, lang.reason))
        parts.sort(key=lambda p: -_ORDER[p[1]])
        decision: Decision = max((p[1] for p in parts), key=lambda d: _ORDER[d])
        reasons = [f"{name}: {reason}" for name, _, reason in parts]
        return Verdict(decision, reasons, loc, role, lang)

    def _work_mode(self, job: StoredJob, versions: list[Job]) -> tuple[WorkMode, str]:
        found = [work_mode(job.title, job.location, job.description)]
        for v in versions:
            means = self.sources[v.source].remote_means if v.source in self.sources else "remote"
            found.append(work_mode(v.title, v.location, v.description, v.remote, means))
        # An explicit "kein Home Office" anywhere wins; otherwise the most remote version
        for mode, evidence in found:
            if mode == "onsite" and evidence:
                return mode, evidence
        return max(found, key=lambda f: _MODE_ORDER[f[0]])

    def _role(self, job: StoredJob, versions: list[Job], description: str) -> RoleResult:
        """The most favourable result over all boards' versions of the job: one board's
        title may say "Werkstudent (m/w/d)", another's "Working Student Software"."""
        rules = self.config.role
        candidates = [(job.title, [], job.main_source)]
        candidates += [(v.title, v.job_types, v.source) for v in versions]
        unique = {(t, tuple(types), src) for t, types, src in candidates}
        results = [judge_role(t, list(types), description, src, rules) for t, types, src in unique]
        return min(results, key=lambda r: _ORDER[r.decision])


def run_filters(conn, filters: Filters, jobs: list[StoredJob] | None = None) -> Counter:
    """Judge every stored job and save the result in job_filters. Returns the counts."""
    jobs = load_jobs(conn) if jobs is None else jobs
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    counts: Counter = Counter()
    for job in jobs:
        v = filters.judge(job)
        counts[v.decision] += 1
        lang = v.language
        conn.execute(
            """INSERT OR REPLACE INTO job_filters (job_id, decision, reasons, work_mode,
                   distance_km, places, german_level, ad_language, kinds, is_tech, filtered_at,
                   location_decision, role_decision, language_decision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.id,
                v.decision,
                json.dumps(v.reasons, ensure_ascii=False),
                v.location.mode,
                round(v.location.distance_km, 1) if v.location.distance_km is not None else None,
                ", ".join(v.location.places),
                lang.level if lang else None,
                lang.ad_language if lang else None,
                json.dumps(sorted(v.role.kinds)),
                None if v.role.tech is None else int(v.role.tech),
                now,
                v.location.decision,
                v.role.decision,
                lang.decision if lang else None,
            ),
        )
    conn.commit()
    return counts


def export_csv(conn, path: Path) -> int:
    """All filter results as a spreadsheet (opens in Excel with umlauts intact)."""
    rows = conn.execute(
        """SELECT f.decision, f.reasons, j.title, j.company, j.location, f.places,
                  f.distance_km, f.work_mode, f.german_level, f.ad_language, f.kinds,
                  j.main_url, j.first_seen_at, j.id,
                  (SELECT group_concat(source, ', ') FROM job_sources s WHERE s.job_id = j.id)
           FROM job_filters f JOIN jobs j ON j.id = f.job_id
           WHERE j.status != 'expired'
           ORDER BY CASE f.decision WHEN 'keep' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,
                    f.distance_km IS NULL, f.distance_km, j.id"""
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["decision", "reasons", "title", "company", "location", "found place", "km",
             "work mode", "German", "ad language", "kind", "link", "first seen", "id", "seen on"]
        )  # fmt: skip
        for r in rows:
            r = list(r)
            r[1] = " | ".join(json.loads(r[1]))
            r[10] = ", ".join(json.loads(r[10]))
            writer.writerow(r)
    return len(rows)


def top_reasons(conn, decision: str, limit: int = 8) -> list[tuple[str, int]]:
    """The most common first reason for a decision, shortened to its kind."""
    counts: Counter = Counter()
    for (reasons,) in conn.execute(
        "SELECT reasons FROM job_filters WHERE decision = ?", (decision,)
    ):
        first = json.loads(reasons)[0]
        counts[_short(first)] += 1
    return counts.most_common(limit)


def _short(reason: str) -> str:
    """'location: too far: Berlin 522 km' -> 'location: too far'"""
    name, _, rest = reason.partition(": ")
    if "non-tech (" in rest:
        return f"{name}: tech and non-tech words"
    rest = re.split(r"[:(',]", rest)[0].strip()
    return f"{name}: {rest}"
