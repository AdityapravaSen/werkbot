"""Phase 4: the fit score (fake LLM and fake embeddings, no network)."""

import csv
import hashlib
import json
import math
import re
from datetime import date
from pathlib import Path

import pytest

from werkbot import db
from werkbot.dedup.normalize import load_aliases
from werkbot.dedup.store import save_jobs
from werkbot.filters.config import load_filter_config
from werkbot.filters.places import Gazetteer
from werkbot.filters.run import Filters, run_filters
from werkbot.llm.client import LLM
from werkbot.models import Job
from werkbot.scoring.config import ScoringConfig, load_scoring_config
from werkbot.scoring.embed import Scale
from werkbot.scoring.requirements import Extraction, Extractor, Requirement
from werkbot.scoring.resume import load_resume
from werkbot.scoring.score import (
    FilterInfo,
    Match,
    Profile,
    build_semantic,
    combine,
    coverage_of,
    export_scores_csv,
    match_requirement,
    penalties_of,
    reason_of,
    score_jobs,
)
from werkbot.scoring.skills import Vocabulary, key
from werkbot.sources import load_sources

ROOT = Path(__file__).resolve().parents[1]
VOCAB = Vocabulary.load(ROOT / "data" / "skills.yaml")
RESUME = load_resume(ROOT / "tests" / "fixtures" / "resume.yaml")
CONFIG = load_scoring_config(ROOT / "data" / "scoring.yaml")
TODAY = date(2026, 10, 20)
PROFILE = Profile.of(RESUME, VOCAB, TODAY)
IGNORE = CONFIG.coverage.ignore_categories


def req(text, kind="must", category="skill", skills=None) -> Requirement:
    return Requirement(text=text, kind=kind, category=category,
                       skills=[text] if skills is None else skills)  # fmt: skip


def status(r: Requirement):
    return match_requirement(r, PROFILE, VOCAB, IGNORE)


# ---------- requirement by requirement ----------


def test_skills_on_the_resume_are_met():
    assert status(req("Python")) == ("met", "Python")
    assert status(req("Apache Spark")) == ("met", "Spark")  # implied by PySpark
    assert status(req("Big Data")) == ("met", "Big Data")  # PySpark -> Spark -> Big Data
    assert status(req("cloud", skills=["AWS", "Snowflake"])) == ("met", "Snowflake")  # any one


def test_known_skills_not_on_the_resume_are_missing():
    assert status(req("Kubernetes")) == ("missing", "")
    assert status(req("React", skills=["React.js"])) == ("missing", "")


def test_unknown_words_are_looked_up_in_the_resume_text():
    assert status(req("Weights & Biases"))[0] == "met"  # in the skills list as written
    assert status(req("credit risk"))[0] == "met"  # in a bullet
    assert status(req("Foobar framework")) == (None, "")  # only similarity can tell


def test_categories():
    assert status(req("team player", category="soft", skills=[]))[0] == "ignored"
    assert status(req("fluent German", category="language", skills=[]))[0] == "ignored"
    assert status(req("enrolled student", category="education", skills=[]))[0] == "met"
    assert status(req("driving licence", category="other", skills=[]))[0] == "ignored"
    assert status(req("experience in a startup", category="experience", skills=[]))[0] is None


def test_coverage_counts_must_haves_double():
    matches = [Match(req("Python"), "met"), Match(req("Kubernetes"), "missing"),
               Match(req("AWS", kind="nice"), "missing"),
               Match(req("team", category="soft"), "ignored")]  # fmt: skip
    # met 2 of 5, plus the prior (2, half met): (2 + 1) / (5 + 2)
    assert coverage_of(matches, CONFIG) == pytest.approx(3 / 7)
    assert coverage_of([Match(req("x"), "partial")], CONFIG) == 0.5
    assert coverage_of([], CONFIG) == 0.5  # nothing known: neutral
    one = [Match(req("Python"), "met")]
    assert coverage_of(one, CONFIG) == pytest.approx(3 / 4)  # one hit is not 100%


# ---------- penalties ----------


def extraction(**kw) -> Extraction:
    return Extraction(**kw)


def test_penalties_from_the_filters_and_the_ad():
    found = penalties_of(FilterInfo(german_level="required"), extraction(), PROFILE, CONFIG)
    assert found == [("German required", 0.6)]
    found = penalties_of(FilterInfo(german_level="none", ad_language="de"), extraction(),
                         PROFILE, CONFIG)  # fmt: skip
    assert found == [("German ad", 0.4)]
    found = penalties_of(FilterInfo(has_text=False), extraction(), PROFILE, CONFIG)
    assert found == [("no ad text yet", 0.4)]
    info = FilterInfo(location_decision="review", location_reason="hybrid outside the area",
                      role_decision="review")  # fmt: skip
    found = penalties_of(info, extraction(seniority="senior", min_years=5), PROFILE, CONFIG)
    assert [why for why, _ in found] == ["hybrid outside the area", "role unclear",
                                         "senior position", "asks 5+ years"]  # fmt: skip
    assert (
        penalties_of(FilterInfo(german_level="plus"), extraction(min_years=3), PROFILE, CONFIG)
        == []
    )  # fmt: skip  (3 years <= 3.5 on the resume)


# ---------- combining ----------


def test_parts_that_are_missing_give_their_weight_to_the_others():
    assert combine(1.0, 1.0, 0.0, CONFIG) == 100
    assert combine(0.5, 0.6, 0.0, CONFIG) == 20 + 27 + 15
    # no embeddings: (45 * 0.8 + 15 * 1) / 60
    assert combine(None, 0.8, 0.0, CONFIG) == 85
    assert combine(None, None, 1.0, CONFIG) == 0


def test_reason_names_missing_must_haves_first():
    matches = [Match(req("AWS", kind="nice"), "missing"), Match(req("Kubernetes"), "missing"),
               Match(req("Python"), "met", "Python")]  # fmt: skip
    assert reason_of(matches, [("German required", 0.6)], 4) == (
        "missing: Kubernetes, (AWS) · German required"
    )
    assert reason_of([Match(req("Python"), "met", "Python")], [], 4) == "has: Python"
    twice = [Match(req("Compliance"), "missing"), Match(req("Compliance"), "missing")]
    assert reason_of(twice, [], 4) == "missing: Compliance"
    many = [Match(req(f"Skill{i}"), "missing") for i in range(6)]
    assert reason_of(many, [], 4).endswith("Skill3 +2")
    assert reason_of([], [], 4) == "no requirements found"


def test_scale_between_anchors():
    s = Scale(0.8, 0.9)
    assert (s(0.7), s(0.85), s(0.95)) == (0.0, pytest.approx(0.5), 1.0)
    assert Scale(0.8, 0.8).high == pytest.approx(0.82)  # too close: widened, not divided by 0


def test_config_file_matches_the_model():
    assert load_scoring_config(ROOT / "data" / "scoring.yaml").weights.coverage == 45
    assert ScoringConfig().weights.semantic == 40


# ---------- all jobs, end to end ----------


class WordEmbedder:
    """Fake embeddings: hashed word counts. Texts sharing words are close."""

    DIM = 512

    def encode(self, texts):
        out = []
        for text in texts:
            v = [0.0] * self.DIM
            for word in re.findall(r"[a-z0-9+#]+", key(text)):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIM] += 1
            norm = math.sqrt(sum(x * x for x in v)) or 1
            out.append([x / norm for x in v])
        return out


ENGLISH = (
    "We are looking for a working student to join our data platform team in Mannheim. "
    "You will build data pipelines and help our machine learning engineers. "
)

LLM_ANSWERS = {
    "Werkstudent Data Engineering (m/w/d)": {
        "tasks": ["Build data pipelines in PySpark", "Support machine learning engineers"],
        "requirements": [
            {"text": "Python", "kind": "must", "category": "skill", "skills": ["Python"]},
            {"text": "Spark", "kind": "must", "category": "skill", "skills": ["Spark"]},
            {"text": "Kubernetes", "kind": "nice", "category": "skill", "skills": ["Kubernetes"]},
            {"text": "Enrolled in computer science", "kind": "must", "category": "education",
             "skills": []},
            {"text": "Good English", "kind": "must", "category": "language", "skills": []},
        ],
        "seniority": "student", "min_years": None,
    },
    "Working Student Frontend": {
        "tasks": ["Build web pages"],
        "requirements": [
            {"text": "React", "kind": "must", "category": "skill", "skills": ["React"]},
            {"text": "TypeScript", "kind": "must", "category": "skill", "skills": ["TypeScript"]},
        ],
        "seniority": "student", "min_years": None,
    },
}  # fmt: skip


class AnswerByTitle:
    def __init__(self):
        self.calls = 0

    def __call__(self, messages, **kwargs):
        from types import SimpleNamespace

        self.calls += 1
        title = re.search(r"Title: (.*)", messages[1]["content"]).group(1)
        content = json.dumps(LLM_ANSWERS[title])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def job(n, title, description=ENGLISH * 3, location="Mannheim") -> Job:
    return Job(title=title, company=f"Company {n}", url=f"https://arbeitnow.example/{n}",
               source="arbeitnow", source_job_id=str(n), location=location,
               description=description)  # fmt: skip


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "werkbot.db"
    db.init_db(path)
    connection = db.connect(path)
    save_jobs(
        connection,
        [
            job(1, "Werkstudent Data Engineering (m/w/d)"),
            job(2, "Working Student Frontend", "We build web pages with React. " * 10),
            job(3, "Werkstudent Marketing (m/w/d)"),  # dropped by the filters: not scored
            job(4, "Werkstudent Data Engineering", "", location="Kaiserslautern"),  # no text
        ],
        {},
        load_aliases(ROOT / "data" / "company_aliases.yaml"),
    )
    sources = {s.name: s for s in load_sources(ROOT / "data" / "sources.yaml",
                                               include_disabled=True)}  # fmt: skip
    filters = Filters(load_filter_config(ROOT / "data" / "filters.yaml"),
                      Gazetteer.load(ROOT / "data" / "places.tsv"), sources)  # fmt: skip
    run_filters(connection, filters)
    yield connection
    connection.close()


def run(conn, semantic=True, max_per_run=100):
    fake = AnswerByTitle()
    rules = CONFIG.llm.model_copy(update={"max_per_run": max_per_run})
    extractor = Extractor(conn, VOCAB, rules, LLM("test/model", 0, completion=fake))
    sem = build_semantic(WordEmbedder(), RESUME, VOCAB, CONFIG) if semantic else None
    stats = score_jobs(conn, RESUME, VOCAB, CONFIG, extractor, sem, today=TODAY)
    return stats, fake


def scores(conn) -> dict[str, dict]:
    return {
        r["title"]: dict(r)
        for r in conn.execute("SELECT j.title, s.* FROM scores s JOIN jobs j ON j.id = s.job_id")
    }


def test_jobs_that_passed_the_filters_get_a_score_and_a_reason(conn):
    stats, fake = run(conn)
    s = scores(conn)
    assert set(s) == {"Werkstudent Data Engineering (m/w/d)", "Working Student Frontend",
                      "Werkstudent Data Engineering"}  # fmt: skip
    data = s["Werkstudent Data Engineering (m/w/d)"]
    front = s["Working Student Frontend"]
    assert data["fit_score"] > front["fit_score"]
    assert data["reason"] == "missing: (Kubernetes)"
    # Python, Spark, studying (2 each) met, Kubernetes (1) not; prior 2: (6 + 1) / (7 + 2)
    assert data["coverage"] == pytest.approx(7 / 9, abs=1e-3)
    assert front["reason"] == "missing: React, TypeScript"
    assert front["coverage"] == pytest.approx(1 / 6, abs=1e-3)
    assert data["method"] == "llm" and fake.calls == 2  # the job without a text isn't sent
    assert 0 <= front["semantic"] < data["semantic"] <= 1
    assert s["Werkstudent Data Engineering"]["reason"] == "has: Data Engineering · no ad text yet"
    assert s["Werkstudent Data Engineering"]["semantic"] == CONFIG.semantic.no_ad_text

    details = json.loads(data["details"])
    assert [r["status"] for r in details["requirements"]] == [
        "met", "met", "missing", "met", "ignored"]  # fmt: skip
    assert details["tasks"][0] == "Build data pipelines in PySpark"
    assert stats.scored == 3 and stats.methods == {"llm": 2, "keywords": 1}


def test_a_second_run_uses_the_saved_requirements(conn):
    run(conn)
    _, fake = run(conn)
    assert fake.calls == 0
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 3


def test_the_best_candidates_are_read_by_the_llm_first(conn):
    """With room for one ad, the data job (more skill words in common) gets the LLM."""
    from werkbot.scoring.score import _priority_key, jobs_to_score

    rows = list(reversed(jobs_to_score(conn)))  # not just database order
    key = _priority_key(rows, PROFILE, VOCAB, CONFIG, None)
    assert sorted(rows, key=key)[0]["title"] == "Werkstudent Data Engineering (m/w/d)"
    run(conn, max_per_run=1)
    s = scores(conn)
    assert s["Werkstudent Data Engineering (m/w/d)"]["method"] == "llm"
    assert s["Working Student Frontend"]["method"] == "keywords"


def test_without_embeddings_the_other_parts_decide(conn):
    run(conn, semantic=False)
    data = scores(conn)["Werkstudent Data Engineering (m/w/d)"]
    assert data["semantic"] is None
    assert data["fit_score"] == round(100 * (45 * 7 / 9 + 15 * 1) / 60)


def test_scores_of_jobs_that_no_longer_pass_are_removed(conn):
    run(conn)
    conn.execute("UPDATE job_filters SET decision = 'drop' WHERE job_id = "
                 "(SELECT id FROM jobs WHERE title = 'Working Student Frontend')")  # fmt: skip
    stats, _ = run(conn)
    assert stats.removed == 1 and "Working Student Frontend" not in scores(conn)


def test_scores_csv_is_sorted_best_first(conn, tmp_path):
    run(conn)
    path = tmp_path / "scores.csv"
    assert export_scores_csv(conn, path) == 3
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    values = [float(r["score"]) for r in rows]
    assert values == sorted(values, reverse=True)
    assert rows[0]["link"].startswith("https://")


def test_similar_resume_bullets_half_meet_unknown_requirements():
    sem = build_semantic(WordEmbedder(), RESUME, VOCAB, CONFIG)
    close = "Validated machine learning models for model risk management"
    (value, index), (far, _) = sem.requirement_scores([close, "Kochen und Backen"])
    assert value >= CONFIG.semantic.partial_match_min > far
    assert "Validated machine learning models" in PROFILE.items[index]


# ---------- CLI ----------


def test_cli_score(conn, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from werkbot.cli import app
    from werkbot.config import get_settings

    db_file = conn.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.setenv("WERKBOT_DB_PATH", db_file)
    get_settings.cache_clear()
    missing = CliRunner().invoke(app, ["score"])  # the test setup has no resume
    assert missing.exit_code == 0 and "Scoring skipped" in missing.output

    monkeypatch.setenv("WERKBOT_RESUME_PATH", str(ROOT / "tests" / "fixtures" / "resume.yaml"))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["score", "--show", "5"])
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())  # the console wraps long lines
    assert "Scores: 3 jobs" in out and "3 from skill words" in out
    assert "No LLM" in out and "No semantic part" in out
    get_settings.cache_clear()


def test_cli_warns_about_filter_results_from_before_phase_4(conn, monkeypatch):
    from typer.testing import CliRunner

    from werkbot.cli import app
    from werkbot.config import get_settings

    conn.execute("UPDATE job_filters SET location_decision = NULL")
    conn.commit()
    monkeypatch.setenv("WERKBOT_DB_PATH", conn.execute("PRAGMA database_list").fetchone()["file"])
    monkeypatch.setenv("WERKBOT_RESUME_PATH", str(ROOT / "tests" / "fixtures" / "resume.yaml"))
    get_settings.cache_clear()
    out = " ".join(CliRunner().invoke(app, ["score", "--show", "0"]).output.split())
    assert "filter results are from before Phase 4" in out
    get_settings.cache_clear()
