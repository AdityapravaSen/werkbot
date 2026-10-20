"""Phase 4: the LLM wrapper and reading requirements from ads (no network: fake LLMs)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from werkbot import db
from werkbot.llm import client
from werkbot.llm.client import LLM, BadAnswer, LLMError, LLMUnavailable, RateLimited, parse_json
from werkbot.scoring.config import LLMRules
from werkbot.scoring.requirements import Extraction, Extractor, Requirement, extract_keywords
from werkbot.scoring.skills import Vocabulary

ROOT = Path(__file__).resolve().parents[1]
VOCAB = Vocabulary.load(ROOT / "data" / "skills.yaml")


def answer(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeCompletion:
    """Stands in for litellm.completion: returns the given answers in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return answer(item if isinstance(item, str) else json.dumps(item))


GOOD = {
    "tasks": ["Build data pipelines"],
    "requirements": [
        {"text": "Python", "kind": "must", "category": "skill", "skills": ["Python"]},
        {"text": "Kubernetes", "kind": "nice-to-have", "category": "skill", "skills": "K8s"},
    ],
    "seniority": "student",
    "min_years": None,
}


# ---------- client ----------


def test_json_is_found_also_inside_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": 1} hope it helps') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("no json here")


def test_answer_is_checked_and_asked_again_once():
    fake = FakeCompletion("not json", GOOD)
    llm = LLM("test/model", requests_per_minute=0, completion=fake)
    result = llm.ask("system", "user", Extraction)
    assert [r.skills for r in result.requirements] == [["Python"], ["K8s"]]
    assert result.requirements[1].kind == "nice"
    assert len(fake.calls) == 2 and "not valid" in fake.calls[1]["messages"][-1]["content"]
    assert fake.calls[0]["temperature"] == 0


def test_gemini_keeps_its_default_temperature():
    fake = FakeCompletion(GOOD)
    LLM("gemini/gemini-flash-lite-latest", 0, completion=fake).ask("s", "u", Extraction)
    assert "temperature" not in fake.calls[0]
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


def test_two_bad_answers_are_an_error():
    llm = LLM("test/model", 0, completion=FakeCompletion("nope", "still nope"))
    with pytest.raises(BadAnswer):
        llm.ask("s", "u", Extraction)


class ServiceUnavailableError(Exception):  # the name LiteLLM uses for a 503
    pass


BUSY = ServiceUnavailableError('{"error": {"code": 503, "message": "This model is currently '
                               'experiencing high demand."}}')  # fmt: skip


def test_a_busy_server_is_retried_after_a_pause():
    waits = []
    fake = FakeCompletion(BUSY, GOOD)
    llm = LLM("test/model", 0, completion=fake, sleep=waits.append)
    assert llm.ask("s", "u", Extraction).requirements
    assert waits == [10] and len(fake.calls) == 2


def test_giving_up_after_the_retries():
    waits = []
    fake = FakeCompletion(TimeoutError("read timed out"), BUSY, BUSY)
    llm = LLM("test/model", 0, completion=fake, sleep=waits.append)
    with pytest.raises(LLMError, match="ServiceUnavailableError: This model is currently"):
        llm.ask("s", "u", Extraction)
    assert waits == [10, 30] and len(fake.calls) == 3


def test_rate_limits_are_recognised():
    class RateLimitError(Exception):
        pass

    llm = LLM("test/model", 0, completion=FakeCompletion(RateLimitError("quota")))
    with pytest.raises(RateLimited):
        llm.ask("s", "u", Extraction)
    llm = LLM("test/model", 0, completion=FakeCompletion(Exception("429 RESOURCE_EXHAUSTED")))
    with pytest.raises(RateLimited):
        llm.ask("s", "u", Extraction)


def test_requests_are_spaced_out():
    now = [0.0]
    waits = []

    def sleep(s):
        waits.append(s)
        now[0] += s

    llm = LLM("test/model", requests_per_minute=6, completion=FakeCompletion(GOOD, GOOD),
              sleep=sleep, clock=lambda: now[0])  # fmt: skip
    llm.ask("s", "u", Extraction)
    now[0] += 4
    llm.ask("s", "u", Extraction)
    assert waits == [6.0]  # 10 s between requests, 4 s had passed


@pytest.mark.real_llm_client
@pytest.mark.parametrize("value", [None, "", "  "])
def test_missing_or_empty_api_key(monkeypatch, value):
    """An empty `GROQ_API_KEY=` (as in .env.example) is no key either."""
    import litellm  # noqa: F401  (imported first: older LiteLLM reads .env on import)

    monkeypatch.setattr(client, "load_env", lambda path=None: None)  # ignore a real .env
    if value is None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GROQ_API_KEY", value)
    with pytest.raises(LLMUnavailable, match="GROQ_API_KEY"):
        LLM.from_settings("groq/openai/gpt-oss-120b", 8, 60)


def test_odd_values_from_models_are_cleaned_up():
    e = Extraction.model_validate({"requirements": [
        {"text": "x", "kind": "Must", "category": "Tooling", "skills": "AWS or Azure"}],
        "seniority": "Junior", "min_years": "2+ years"})  # fmt: skip
    req = e.requirements[0]
    assert (req.kind, req.category, req.skills) == ("must", "other", ["AWS", "Azure"])
    assert (e.seniority, e.min_years) == ("unknown", 2.0)


# ---------- requirements without an LLM ----------

GERMAN_AD = """Über uns
Wir sind ein Softwarehaus mit Kunden in ganz Europa und nutzen Kubernetes in der Cloud.
Deine Aufgaben
Du entwickelst Datenpipelines mit Airflow.
Dein Profil
Sehr gute Kenntnisse in Python und SQL
Erste Erfahrung mit Docker ist von Vorteil
Mindestens 2 Jahre Berufserfahrung
Nice to have
Erfahrung mit AWS
Wir bieten
Moderne Hardware, Java-Kurse und Obst
"""


def test_skill_words_by_section():
    e = extract_keywords("Werkstudent Data Engineering (m/w/d)", GERMAN_AD, VOCAB)
    kinds = {r.skills[0]: r.kind for r in e.requirements}
    assert kinds == {
        "Data Engineering": "must",  # from the title
        "Airflow": "nice",  # tasks
        "Python": "must",
        "SQL": "must",
        "Docker": "nice",  # "von Vorteil" on the line
        "AWS": "nice",  # nice-to-have section
    }  # not Kubernetes (about us) or Java (we offer)
    assert (e.seniority, e.min_years, e.method) == ("student", 2.0, "keywords")


def test_senior_titles():
    assert extract_keywords("Senior Data Engineer", "", VOCAB).seniority == "senior"


# ---------- requirements with the LLM, saved per ad ----------


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "werkbot.db"
    db.init_db(path)
    connection = db.connect(path)
    yield connection
    connection.close()


AD = "We are looking for a working student. " * 10  # long enough to send


def extractor(conn, *answers, now=None, **rules):
    fake = FakeCompletion(*answers)
    llm = LLM("test/model", 0, completion=fake, retry_waits=())
    return Extractor(conn, VOCAB, LLMRules(**rules), llm, now=now), fake


def test_each_ad_is_sent_once(conn):
    ex, fake = extractor(conn, GOOD)
    first = ex.extract("Werkstudent Python", AD)
    assert first.method == "llm" and len(fake.calls) == 1
    assert "Title: Werkstudent Python" in fake.calls[0]["messages"][1]["content"]

    again, _ = extractor(conn)  # a later run: no answers needed
    second = again.extract("Werkstudent Python", AD)
    assert second == first and again.stats["saved"] == 1


def test_short_ads_are_not_sent(conn):
    ex, fake = extractor(conn)
    result = ex.extract("Werkstudent Python", "Python")
    assert result.method == "keywords" and fake.calls == []


def test_a_rate_limit_stops_the_llm_for_the_run(conn):
    ex, fake = extractor(conn, Exception("429 Too Many Requests"))
    assert ex.extract("A", AD).method == "keywords"
    assert ex.extract("B", AD).method == "keywords"
    assert "rate limit" in ex.stopped and len(fake.calls) == 1


def test_the_next_model_takes_over_after_a_rate_limit(conn):
    first = FakeCompletion(Exception("429 RESOURCE_EXHAUSTED"))
    second = FakeCompletion(GOOD, GOOD)
    llms = [LLM("gemini/x", 0, completion=first, retry_waits=()),
            LLM("groq/y", 0, completion=second, retry_waits=())]  # fmt: skip
    ex = Extractor(conn, VOCAB, LLMRules(), llms)
    assert ex.extract("A", AD).method == "llm"  # the same ad, answered by groq
    assert ex.extract("B", AD).method == "llm"
    assert len(first.calls) == 1 and len(second.calls) == 2
    assert ex.switched[0].startswith("gemini/x -> groq/y: rate limit") and not ex.stopped
    row = conn.execute("SELECT model FROM job_requirements LIMIT 1").fetchone()
    assert row["model"] == "groq/y"


def test_server_errors_dont_use_up_the_runs_budget(conn):
    ex, fake = extractor(conn, ConnectionError("down"), GOOD, GOOD, max_per_run=2)
    assert [ex.extract(t, AD).method for t in "ABCD"] == ["keywords", "llm", "llm", "keywords"]
    assert "max_per_run" in ex.stopped and ex.stats["errors"] == 1


def test_max_requests_per_run(conn):
    ex, fake = extractor(conn, GOOD, GOOD, max_per_run=1)
    assert ex.extract("A", AD).method == "llm"
    assert ex.extract("B", AD).method == "keywords"
    assert "max_per_run" in ex.stopped and len(fake.calls) == 1


def test_failed_ads_are_retried_after_a_week(conn):
    now = datetime(2026, 10, 20, tzinfo=UTC)
    ex, _ = extractor(conn, "bad", "bad", now=now)
    assert ex.extract("A", AD).method == "keywords" and ex.stats["failed"] == 1

    soon, fake = extractor(conn, now=now + timedelta(days=2))
    assert soon.extract("A", AD).method == "keywords" and fake.calls == []

    later, fake = extractor(conn, GOOD, now=now + timedelta(days=8))
    assert later.extract("A", AD).method == "llm" and len(fake.calls) == 1


def test_connection_errors_are_not_held_against_the_ad(conn):
    ex, _ = extractor(conn, ConnectionError("down"), GOOD)
    assert ex.extract("A", AD).method == "keywords" and ex.stats["errors"] == 1
    assert ex.extract("A", AD).method == "llm"  # asked again in the same run: nothing saved


def test_repeated_errors_stop_the_llm_for_the_run(conn):
    ex, fake = extractor(conn, *[ConnectionError("down")] * 3)
    for title in "ABCD":
        ex.extract(title, AD)
    assert len(fake.calls) == 3 and "3 errors in a row" in ex.stopped


def test_llm_disabled_in_config(conn):
    ex, fake = extractor(conn, enabled=False)
    assert ex.extract("Werkstudent Python", AD).method == "keywords" and fake.calls == []


def test_requirement_skills_are_limited():
    assert len(Requirement(text="x", skills=[str(i) for i in range(10)]).skills) == 6
