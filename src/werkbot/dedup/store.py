"""Save a run's jobs to SQLite without creating duplicates (PLAN.md sections 3.3 and 3.4).

For each group of duplicates from this run:
  1. Look for the job in the database, with the same four levels as within a run.
  2. Found: update it (better link, longer description, earliest date, last_seen_at).
     Not found: insert it as 'new'.
  3. Store every source link in job_sources (one row per URL).
Then mark jobs not seen for `expire_after_days` as 'expired'.

Statuses:  new -> active (seen again on a later day) -> expired (not seen for 14 days)
           expired -> reposted (it came back) -> active
           filtered_out (set by Phase 3) is never changed here.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from rapidfuzz import fuzz

from werkbot.dedup.match import (
    DescriptionCheck,
    Keys,
    fuzzy_reason,
    group_jobs,
    similar_descriptions,
)
from werkbot.dedup.merge import Merged, combine_german_required, earliest, merge, rank
from werkbot.models import Job


@dataclass
class SaveResult:
    received: int = 0  # jobs given to save_jobs
    groups: int = 0  # after merging duplicates within this run
    new: int = 0
    seen_again: int = 0  # matched a job already in the database
    reposted: int = 0  # expired jobs that came back
    links_added: int = 0
    expired: int = 0
    # Groups of more than one job: (jobs, reasons). For `werkbot dedup --dry-run`.
    merged_groups: list[tuple[list[Job], list[str]]] = field(default_factory=list)
    # Matches with the database: (this run's main job, database id, reason)
    matched: list[tuple[Job, int, str]] = field(default_factory=list)


def timestamp(value: datetime) -> str:
    """Same format as SQLite's datetime('now'): UTC, 'YYYY-MM-DD HH:MM:SS'."""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def save_jobs(
    conn: sqlite3.Connection,
    jobs: list[Job],
    kinds: dict[str, str],
    aliases=(),
    now: datetime | None = None,
    expire_after_days: int = 14,
    confirm: DescriptionCheck = similar_descriptions,
    dry_run: bool = False,
    boards: dict[str, str] | None = None,
) -> SaveResult:
    """Merge and save jobs in one transaction. With dry_run nothing is written.

    kinds:  source name -> employer / board / aggregator (which link wins)
    boards: source name -> site, so two searches on one site count as one board
    """
    now = now or datetime.now(UTC)
    stamp = timestamp(now)
    boards = boards or {}
    keys = [Keys.of(job, aliases, boards) for job in jobs]
    groups = group_jobs(jobs, keys, confirm)
    result = SaveResult(received=len(jobs), groups=len(groups))

    try:
        for group in groups:
            members = [jobs[i] for i in group.indexes]
            member_keys = [keys[i] for i in group.indexes]
            if len(members) > 1:
                result.merged_groups.append((members, group.reasons))
            merged = merge(members, kinds)
            best = _best_keys(merged.main, members, member_keys)

            job_id, why = _find_existing(conn, members, member_keys, best, stamp, confirm, boards)
            if job_id is None:
                job_id = _insert(conn, merged, members, member_keys, best, stamp)
                result.new += 1
            else:
                result.matched.append((merged.main, job_id, why))
                result.seen_again += 1
                _update(conn, job_id, merged, member_keys, kinds)
                if _touch(conn, job_id, stamp) == "expired":
                    result.reposted += 1

            added, other_jobs = _link_sources(conn, job_id, members, member_keys, stamp)
            result.links_added += added
            for other in other_jobs:  # a link already stored under another job
                _touch(conn, other, stamp)

        result.expired = _expire(conn, now, expire_after_days)
    except Exception:
        conn.rollback()
        raise
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return result


# ---------- finding a job that is already stored ----------


def _find_existing(
    conn, members, member_keys, best, stamp, confirm, boards
) -> tuple[int | None, str]:
    for k in member_keys:  # level 1
        row = conn.execute("SELECT job_id FROM job_sources WHERE url_hash = ?", (k.url_hash,))
        if hit := row.fetchone():
            return hit[0], "same URL"
    for k in member_keys:  # level 2
        if k.source_id:
            row = conn.execute(
                "SELECT job_id FROM job_sources WHERE source = ? AND source_job_id = ?", k.source_id
            )
            if hit := row.fetchone():
                return hit[0], "same source ID"
    for k in member_keys:  # level 3
        if k.fingerprint:
            row = conn.execute("SELECT id FROM jobs WHERE fingerprint = ?", (k.fingerprint,))
            if hit := row.fetchone():
                return hit[0], "same company + title + city"
    if not best.company:
        return None, ""
    # Level 4. Jobs inserted earlier in this run are skipped: they were already compared
    # with this group (and kept apart) inside the run.
    candidates = conn.execute(
        "SELECT id, title_norm, city, cities, remote, description,"
        " (SELECT group_concat(source, '|') FROM job_sources WHERE job_id = jobs.id) AS sources"
        " FROM jobs WHERE company_norm = ? AND first_seen_at < ?",
        (best.company, stamp),
    ).fetchall()
    best_id, best_score, best_why = None, -1.0, ""
    for row in candidates:
        stored = Keys(
            url_hash="",
            source_id=None,
            company=best.company,
            title=row["title_norm"],
            city=row["city"] or "",
            cities=frozenset(c for c in row["cities"].split("|") if c),
            remote=bool(row["remote"]),
            fingerprint=None,
            boards=frozenset(boards.get(x, x) for x in (row["sources"] or "").split("|") if x),
        )
        # Every job in the group must match the stored one, not just one of them.
        reasons = [
            fuzzy_reason(k, stored, job.description, row["description"], confirm)
            for job, k in zip(members, member_keys, strict=True)
        ]
        if all(reasons):
            score = fuzz.token_set_ratio(best.title, stored.title)
            if score > best_score:
                best_id, best_score, best_why = row["id"], score, reasons[0]
    return best_id, best_why


def _best_keys(main: Job, members: list[Job], member_keys: list[Keys]) -> Keys:
    """The main job's keys, unless its company is unknown and another source knows it."""
    ordered = sorted(zip(members, member_keys, strict=True), key=lambda p: p[0] is not main)
    return next((k for _, k in ordered if k.company), ordered[0][1])


# ---------- writing ----------


def _insert(conn, merged: Merged, members, member_keys, best: Keys, stamp: str) -> int:
    main = merged.main
    company = next(
        (j.company for j, k in zip(members, member_keys, strict=True) if k == best), main.company
    )
    cities = sorted(set().union(*(k.cities for k in member_keys)))
    cursor = conn.execute(
        """INSERT INTO jobs (fingerprint, title, company, city, remote, description, date_posted,
               first_seen_at, last_seen_at, status, main_url, main_source, apply_url, location,
               company_norm, title_norm, cities, job_types, german_required)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            best.fingerprint or f"url:{best.url_hash}",
            main.title,
            company,
            best.city,
            int(merged.remote),
            merged.description,
            merged.date_posted.isoformat() if merged.date_posted else None,
            stamp,
            stamp,
            str(main.url),
            main.source,
            merged.apply_url,
            merged.location,
            best.company,
            best.title,
            "|".join(cities),
            json.dumps(merged.job_types, ensure_ascii=False),
            _to_int(merged.german_required),
        ),
    )
    return cursor.lastrowid


def _update(conn, job_id: int, merged: Merged, member_keys: list[Keys], kinds) -> None:
    """Apply the merge rules between the stored job and this run's version."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    main = merged.main
    better_link = rank(main.source, kinds) < rank(row["main_source"], kinds)

    values = {
        "remote": int(bool(row["remote"]) or merged.remote),
        "german_required": _to_int(
            combine_german_required(_to_bool(row["german_required"]), merged.german_required)
        ),
        "job_types": json.dumps(
            _union(json.loads(row["job_types"] or "[]"), merged.job_types), ensure_ascii=False
        ),
        "cities": "|".join(
            sorted(
                {c for c in row["cities"].split("|") if c}.union(*(k.cities for k in member_keys))
            )
        ),
    }
    if len(merged.description) > len(row["description"]):
        values["description"] = merged.description
    first = earliest(_parse(row["date_posted"]), merged.date_posted)
    if first is not None:
        values["date_posted"] = first.isoformat()
    if better_link:
        values |= {
            "main_url": str(main.url),
            "main_source": main.source,
            "title": main.title,
            "location": merged.location or row["location"],
        }
        if merged.apply_url:
            values["apply_url"] = merged.apply_url
    else:
        if not row["apply_url"] and merged.apply_url:
            values["apply_url"] = merged.apply_url
        if not row["location"] and merged.location:
            values["location"] = merged.location

    assignments = ", ".join(f"{name} = :{name}" for name in values)
    conn.execute(f"UPDATE jobs SET {assignments} WHERE id = :id", {**values, "id": job_id})


def _touch(conn, job_id: int, stamp: str) -> str:
    """Mark a job as seen now and move its status on. Returns the status before."""
    before = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    # In an UPDATE, every expression on the right sees the row as it was before.
    conn.execute(
        """UPDATE jobs SET
               last_seen_at = :now,
               status = CASE
                   WHEN status = 'expired' THEN 'reposted'
                   WHEN status = 'new' AND date(first_seen_at) < date(:now) THEN 'active'
                   WHEN status = 'reposted' AND date(reposted_at) < date(:now) THEN 'active'
                   ELSE status END,
               reposted_at = CASE WHEN status = 'expired' THEN :now ELSE reposted_at END
           WHERE id = :id""",
        {"now": stamp, "id": job_id},
    )
    return before


def _link_sources(conn, job_id, members, member_keys, stamp) -> tuple[int, set[int]]:
    """One job_sources row per link. Returns (rows added, other jobs that own a link)."""
    added, others = 0, set()
    for job, k in zip(members, member_keys, strict=True):
        data = job.model_dump_json()
        row = conn.execute(
            "SELECT id, job_id FROM job_sources WHERE url_hash = ?", (k.url_hash,)
        ).fetchone()
        same_url = row is not None
        if row is None and k.source_id:
            row = conn.execute(
                "SELECT id, job_id FROM job_sources WHERE source = ? AND source_job_id = ?",
                k.source_id,
            ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO job_sources"
                " (job_id, source, source_job_id, url, url_hash, seen_at, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, job.source, job.source_job_id, str(job.url), k.url_hash, stamp, data),
            )
            added += 1
            continue
        if same_url:
            conn.execute(
                "UPDATE job_sources SET seen_at = ?, data = ? WHERE id = ?",
                (stamp, data, row["id"]),
            )
        else:  # same source ID, the URL changed: keep the newest URL
            conn.execute(
                "UPDATE job_sources SET seen_at = ?, data = ?, url = ?, url_hash = ? WHERE id = ?",
                (stamp, data, str(job.url), k.url_hash, row["id"]),
            )
        if row["job_id"] != job_id:
            others.add(row["job_id"])
    return added, others


def _expire(conn, now: datetime, days: int) -> int:
    cursor = conn.execute(
        "UPDATE jobs SET status = 'expired'"
        " WHERE last_seen_at < ? AND status NOT IN ('expired', 'filtered_out')",
        (timestamp(now - timedelta(days=days)),),
    )
    return cursor.rowcount


# ---------- small helpers ----------


def _union(a: list, b: list) -> list:
    return a + [x for x in b if x not in a]


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _to_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)
