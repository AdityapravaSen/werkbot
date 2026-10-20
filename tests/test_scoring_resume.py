"""Phase 4: skill words and the resume."""

from datetime import date
from pathlib import Path

import pytest

from werkbot.scoring.resume import ResumeMissing, load_resume
from werkbot.scoring.skills import Vocabulary

ROOT = Path(__file__).resolve().parents[1]
VOCAB = Vocabulary.load(ROOT / "data" / "skills.yaml")
RESUME = load_resume(ROOT / "tests" / "fixtures" / "resume.yaml")
TODAY = date(2026, 10, 20)


# ---------- skill words ----------


def test_spellings_map_to_one_name():
    assert VOCAB.canonical("k8s") == "Kubernetes"
    assert VOCAB.canonical("Amazon Web Services") == "AWS"
    assert VOCAB.canonical("Scikit-Learn") == "scikit-learn"
    assert VOCAB.canonical("Künstliche Intelligenz") == "Artificial Intelligence"
    assert VOCAB.canonical("ml") == "Machine Learning"  # as a term, case doesn't matter
    assert VOCAB.canonical("Basket weaving") is None


def test_finding_skills_in_a_text():
    text = "Erfahrung mit Python und K8s, Kenntnisse in C/C++ sowie neural networks."
    assert VOCAB.find(text) == ["Python", "Kubernetes", "C", "C++", "Deep Learning"]


def test_short_spellings_only_match_as_written():
    """'R', 'Go', 'ML' in a text only count in that spelling: not 'r' or 'go'."""
    assert VOCAB.find("Our R&D team, go for it") == []
    assert VOCAB.find("Statistics with R and Go") == ["Statistics", "R", "Go"]
    assert VOCAB.find("ML/AI") == ["Machine Learning", "Artificial Intelligence"]
    assert VOCAB.find("500 ml Wasser") == []


def test_implied_skills_follow_chains_one_way():
    names = VOCAB.closure(["PySpark"])
    assert {"Spark", "Python", "Big Data", "Data Engineering"} <= names
    assert "PySpark" not in VOCAB.closure(["Spark"])


def test_unknown_names_in_implies_are_an_error():
    with pytest.raises(ValueError, match="Hadop"):
        Vocabulary({"Spark": []}, {"Spark": ["Hadop"]})


# ---------- resume ----------


def test_skills_from_lists_tags_and_bullets():
    names = RESUME.skill_names(VOCAB)
    assert {"Python", "PySpark", "Snowflake", "Hive", "Databricks", "Airflow"} <= names
    assert {"Model Validation", "Data Engineering", "LLMs", "Scraping"} <= names  # tags
    assert "Machine Learning" in names  # bullet text
    assert not {"German", "English"} & names  # spoken languages are not tech skills


def test_unknown_skills_are_kept_as_written():
    assert "weights & biases" in RESUME.terms()


def test_years_count_overlapping_jobs_once():
    # 2021-07 .. 2024-06 and 2024-01 .. 2024-12 -> 2021-07 .. 2024-12 = 42 months
    assert RESUME.years_of_experience(TODAY) == 3.5


def test_student_while_a_degree_is_ongoing():
    assert RESUME.is_student(TODAY)
    finished = RESUME.model_copy(update={"education": RESUME.education[1:]})
    assert not finished.is_student(TODAY)


def test_example_resume_loads():
    example = load_resume(ROOT / "data" / "master_resume.example.yaml")
    assert example.items() and example.text()


def test_missing_resume_says_what_to_do(tmp_path):
    with pytest.raises(ResumeMissing, match="master_resume.example.yaml"):
        load_resume(tmp_path / "master_resume.yaml")
