"""SQLite storage. The schema follows section 3.5 of the plan (deduplication)."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint    TEXT    NOT NULL UNIQUE,      -- sha256(company + title + city), normalized
    title          TEXT    NOT NULL,
    company        TEXT    NOT NULL,
    city           TEXT,
    remote         INTEGER NOT NULL DEFAULT 0,
    description    TEXT    NOT NULL DEFAULT '',
    language       TEXT,
    date_posted    TEXT,                          -- ISO 8601, earliest seen across sources
    first_seen_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    status         TEXT    NOT NULL DEFAULT 'new' -- new | reposted | expired | filtered_out
                   CHECK (status IN ('new', 'reposted', 'expired', 'filtered_out', 'active')),
    main_url       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS job_sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source         TEXT    NOT NULL,
    source_job_id  TEXT,
    url            TEXT    NOT NULL,
    url_hash       TEXT    NOT NULL UNIQUE,       -- sha256(normalized url)
    seen_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_job_id)
);

CREATE TABLE IF NOT EXISTS scores (
    job_id         INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    fit_score      REAL    NOT NULL,
    reason         TEXT    NOT NULL DEFAULT '',
    scored_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    job_id         INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status         TEXT    NOT NULL DEFAULT 'saved'
                   CHECK (status IN ('saved', 'applied', 'interview', 'rejected', 'offer')),
    applied_at     TEXT,
    notes          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS page_watch (
    source         TEXT    PRIMARY KEY,
    url            TEXT    NOT NULL,
    content_hash   TEXT    NOT NULL,
    checked_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    changed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);
"""

# Run after every scrape: any row returned means dedup let something through.
DUPLICATE_CHECKS = {
    "duplicate fingerprints": (
        "SELECT fingerprint, COUNT(*) FROM jobs GROUP BY fingerprint HAVING COUNT(*) > 1"
    ),
    "duplicate url hashes": (
        "SELECT url_hash, COUNT(*) FROM job_sources GROUP BY url_hash HAVING COUNT(*) > 1"
    ),
}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def find_duplicates(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Return any duplicates that slipped past dedup (should always be empty)."""
    found = {}
    for name, query in DUPLICATE_CHECKS.items():
        rows = [tuple(r) for r in conn.execute(query).fetchall()]
        if rows:
            found[name] = rows
    return found
