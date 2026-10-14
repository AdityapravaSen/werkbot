"""Smoke tests for the project skeleton."""

from pathlib import Path

import pytest

from werkbot import db
from werkbot.models import SourceGroup
from werkbot.sources import load_sources

ROOT = Path(__file__).resolve().parents[1]


def test_sources_yaml_is_valid():
    sources = load_sources(ROOT / "data" / "sources.yaml", include_disabled=True)
    assert len(sources) == 15  # 14 sites; LinkedIn has two searches
    groups = {s.group for s in sources}
    assert groups == set(SourceGroup)


def test_disabled_sources_are_skipped_by_default():
    enabled = load_sources(ROOT / "data" / "sources.yaml")
    assert all(s.enabled for s in enabled)
    assert "foorilla" not in {s.name for s in enabled}


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
        assert db.find_duplicates(conn) == {}
