"""Smoke tests for the project skeleton."""

from pathlib import Path

import pytest

from werkbot import db
from werkbot.models import SourceGroup
from werkbot.sources import load_sources

ROOT = Path(__file__).resolve().parents[1]


def test_sources_yaml_is_valid():
    sources = load_sources(ROOT / "data" / "sources.yaml", include_disabled=True)
    assert len(sources) >= 14
    assert {s.group for s in sources} == set(SourceGroup)


def test_every_source_has_a_built_adapter():
    from werkbot.adapters import REGISTRY

    sources = load_sources(ROOT / "data" / "sources.yaml", include_disabled=True)
    missing = {s.name: s.adapter for s in sources if s.adapter not in REGISTRY}
    assert missing == {}


def test_disabled_sources_are_skipped_by_default(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text(
        "- {name: active, group: A, adapter: x, url: 'https://a.example'}\n"
        "- {name: inactive, group: A, adapter: x, url: 'https://b.example', enabled: false}\n",
        encoding="utf-8",
    )
    assert [s.name for s in load_sources(config)] == ["active"]
    assert len(load_sources(config, include_disabled=True)) == 2


def test_init_db_creates_tables(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    db.init_db(path)  # running twice must be safe
    with db.connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "job_sources", "scores", "applications", "page_watch"} <= tables


def test_db_rejects_duplicate_fingerprint(tmp_path):
    import sqlite3

    path = tmp_path / "test.db"
    db.init_db(path)
    with db.connect(path) as conn:
        row = ("abc", "Working Student ML", "Acme", "https://example.com/1")
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, main_url) VALUES (?, ?, ?, ?)", row
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (fingerprint, title, company, main_url) VALUES (?, ?, ?, ?)", row
            )
        assert set(db.find_duplicates(conn)) == {"jobs without a source link"}
        conn.execute(
            "INSERT INTO job_sources (job_id, source, url, url_hash) VALUES (1, 'x', 'u', 'h')"
        )
        assert db.find_duplicates(conn) == {}


# The jobs and job_sources tables exactly as Phase 0 created them.
PHASE_0_TABLES = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, company TEXT NOT NULL, city TEXT,
    remote INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL DEFAULT '', language TEXT,
    date_posted TEXT, first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'reposted', 'expired', 'filtered_out', 'active')),
    main_url TEXT NOT NULL
);
CREATE TABLE job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL, source_job_id TEXT, url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE, seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_job_id)
);
"""


def test_init_db_adds_new_columns_to_an_old_database(tmp_path):
    """A database created in Phase 0 gets the Phase 2 columns without losing rows."""
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(PHASE_0_TABLES)
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, main_url) VALUES ('f', 't', 'c', 'u')"
        )
    db.init_db(path)
    with db.connect(path) as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        assert {"company_norm", "title_norm", "cities", "main_source", "reposted_at"} <= columns
        assert conn.execute("SELECT title, company_norm FROM jobs").fetchone()[:] == ("t", "")
        source_columns = {r["name"] for r in conn.execute("PRAGMA table_info(job_sources)")}
        assert "data" in source_columns


def test_cli_output_survives_windows_cp1252_pipe():
    """Regression: piping output on Windows (cp1252) crashed on '→' and non-Latin titles."""
    import os
    import subprocess
    import sys

    code = "import werkbot.cli as c; c.console.print('338 jobs → raw | AI Native 架构师 | Köln')"
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, env=env, timeout=60, check=False
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert "架构师" in result.stdout.decode("utf-8")
