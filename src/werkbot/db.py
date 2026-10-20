"""SQLite storage. The schema follows section 3.5 of the plan (deduplication)."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint    TEXT    NOT NULL UNIQUE,      -- sha256(company + title + city), normalized
    title          TEXT    NOT NULL,
    company        TEXT    NOT NULL,
    city           TEXT,                          -- normalized first city, 'remote' or ''
    remote         INTEGER NOT NULL DEFAULT 0,
    description    TEXT    NOT NULL DEFAULT '',
    language       TEXT,
    date_posted    TEXT,                          -- ISO 8601, earliest seen across sources
    first_seen_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    status         TEXT    NOT NULL DEFAULT 'new' -- new | reposted | expired | filtered_out
                   CHECK (status IN ('new', 'reposted', 'expired', 'filtered_out', 'active')),
    main_url       TEXT    NOT NULL,
    -- Phase 2 (dedup)
    main_source    TEXT    NOT NULL DEFAULT '',   -- source of main_url (decides link priority)
    apply_url      TEXT,
    location       TEXT,                          -- as the main source wrote it
    company_norm   TEXT    NOT NULL DEFAULT '',   -- normalized, for fuzzy matching across runs
    title_norm     TEXT    NOT NULL DEFAULT '',
    cities         TEXT    NOT NULL DEFAULT '',   -- all normalized cities, '|'-separated
    job_types      TEXT    NOT NULL DEFAULT '[]', -- JSON list
    german_required INTEGER,                      -- 1 / 0 / NULL = unknown
    reposted_at    TEXT,                          -- when an expired job came back
    -- Phase 3 (job page details)
    details_status TEXT,                          -- ok | no_description | failed
    details_at     TEXT
);

CREATE TABLE IF NOT EXISTS job_sources (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source         TEXT    NOT NULL,
    source_job_id  TEXT,
    url            TEXT    NOT NULL,
    url_hash       TEXT    NOT NULL UNIQUE,       -- sha256(normalized url)
    seen_at        TEXT    NOT NULL DEFAULT (datetime('now')),  -- last time this link was seen
    data           TEXT    NOT NULL DEFAULT '{}',  -- the full record from this source (JSON)
    UNIQUE (source, source_job_id)
);

-- Phase 3: the filter decision for each job (recomputed whenever filters run)
CREATE TABLE IF NOT EXISTS job_filters (
    job_id         INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    decision       TEXT    NOT NULL CHECK (decision IN ('keep', 'review', 'drop')),
    reasons        TEXT    NOT NULL DEFAULT '[]', -- JSON list, the deciding ones first
    work_mode      TEXT,                          -- onsite | hybrid | remote
    distance_km    REAL,
    places         TEXT    NOT NULL DEFAULT '',
    german_level   TEXT,                          -- none | plus | good | required | fluent
    ad_language    TEXT,                          -- en | de | NULL
    kinds          TEXT    NOT NULL DEFAULT '[]',
    is_tech        INTEGER,
    filtered_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Phase 4 uses each filter's own decision for the penalties
    location_decision TEXT,
    role_decision     TEXT,
    language_decision TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    job_id         INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    fit_score      REAL    NOT NULL,
    reason         TEXT    NOT NULL DEFAULT '',
    scored_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    -- Phase 4: the parts of the score (0..1; semantic/coverage NULL = not available)
    semantic       REAL,
    coverage       REAL,
    penalty        REAL,
    method         TEXT,                          -- requirements from: llm | keywords
    details        TEXT    NOT NULL DEFAULT '{}'  -- JSON: every requirement and penalty
);

-- Phase 4: what each ad asks for, as read by the LLM. Keyed by the ad's text, so an ad
-- costs one request ever, also after a merge or when another board has the same text.
CREATE TABLE IF NOT EXISTS job_requirements (
    ad_hash        TEXT    PRIMARY KEY,           -- sha256(title + description)
    method         TEXT    NOT NULL,              -- llm | failed
    model          TEXT    NOT NULL DEFAULT '',
    prompt_version INTEGER NOT NULL,
    data           TEXT    NOT NULL DEFAULT '{}',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
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

"""

# Created after the column migration, because some use Phase 2 columns.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_company_norm ON jobs(company_norm);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_sources_job ON job_sources(job_id);
"""

# Columns added after Phase 0. A database created earlier gets them with ALTER TABLE.
ADDED_COLUMNS = {
    "jobs": {
        "main_source": "TEXT NOT NULL DEFAULT ''",
        "apply_url": "TEXT",
        "location": "TEXT",
        "company_norm": "TEXT NOT NULL DEFAULT ''",
        "title_norm": "TEXT NOT NULL DEFAULT ''",
        "cities": "TEXT NOT NULL DEFAULT ''",
        "job_types": "TEXT NOT NULL DEFAULT '[]'",
        "german_required": "INTEGER",
        "reposted_at": "TEXT",
        "details_status": "TEXT",
        "details_at": "TEXT",
    },
    "job_sources": {"data": "TEXT NOT NULL DEFAULT '{}'"},
    "job_filters": {
        "location_decision": "TEXT",
        "role_decision": "TEXT",
        "language_decision": "TEXT",
    },
    "scores": {
        "semantic": "REAL",
        "coverage": "REAL",
        "penalty": "REAL",
        "method": "TEXT",
        "details": "TEXT NOT NULL DEFAULT '{}'",
    },
}

# Run after every scrape: any row returned means dedup let something through.
DUPLICATE_CHECKS = {
    "duplicate fingerprints": (
        "SELECT fingerprint, COUNT(*) FROM jobs GROUP BY fingerprint HAVING COUNT(*) > 1"
    ),
    "duplicate url hashes": (
        "SELECT url_hash, COUNT(*) FROM job_sources GROUP BY url_hash HAVING COUNT(*) > 1"
    ),
    "jobs without a source link": (
        "SELECT id, title FROM jobs WHERE id NOT IN (SELECT job_id FROM job_sources)"
    ),
}


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path) -> None:
    """Create missing tables and columns. Safe to run on every start."""
    conn = connect(path)
    try:
        with conn:
            conn.executescript(SCHEMA)
            for table, columns in ADDED_COLUMNS.items():
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, ddl in columns.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            conn.executescript(INDEXES)
    finally:
        conn.close()


def find_duplicates(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Return any duplicates that slipped past dedup (should always be empty)."""
    found = {}
    for name, query in DUPLICATE_CHECKS.items():
        rows = [tuple(r) for r in conn.execute(query).fetchall()]
        if rows:
            found[name] = rows
    return found
