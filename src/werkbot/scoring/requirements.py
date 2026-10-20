"""What a job ad asks for: from the LLM (saved per ad, so each ad costs one request ever),
or from the skill word list when no LLM is available."""

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from werkbot.dedup.normalize import fold, sha256
from werkbot.llm.client import LLM, BadAnswer, LLMError, RateLimited, load_prompt
from werkbot.scoring.config import LLMRules
from werkbot.scoring.skills import Vocabulary

PROMPT_VERSION = 1  # raise when prompts/requirements.md changes, so ads are read again
RETRY_FAILED_AFTER = timedelta(days=7)
MAX_ERRORS = 3  # connection / server errors in a row before the LLM is stopped for the run
MIN_AD_CHARS = 200  # shorter "ads" (title only) aren't worth a request

Category = Literal["skill", "experience", "education", "language", "soft", "other"]
Method = Literal["llm", "keywords"]


class Requirement(BaseModel):
    text: str
    kind: Literal["must", "nice"] = "must"
    category: Category = "other"
    skills: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value):
        return "nice" if str(value or "").strip().lower() in {"nice", "nice-to-have",
                                                               "nice to have", "optional",
                                                               "plus"} else "must"  # fmt: skip

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value):
        value = str(value or "").strip().lower()
        return value if value in Category.__args__ else "other"

    @field_validator("skills", mode="before")
    @classmethod
    def _skills(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = re.split(r"\s*(?:,|/|\bor\b|\boder\b)\s*", value)
        return [str(v).strip() for v in value if str(v).strip()][:6]


class Extraction(BaseModel):
    tasks: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    seniority: Literal["student", "entry", "mid", "senior", "unknown"] = "unknown"
    min_years: float | None = None
    method: Method = "llm"

    @field_validator("seniority", mode="before")
    @classmethod
    def _seniority(cls, value):
        value = str(value or "").strip().lower()
        return value if value in {"student", "entry", "mid", "senior"} else "unknown"

    @field_validator("min_years", mode="before")
    @classmethod
    def _years(cls, value):
        if value in (None, "", "null"):
            return None
        m = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(m.group(0)) if m else None

    @field_validator("tasks", mode="before")
    @classmethod
    def _tasks(cls, value):
        return [str(v) for v in (value or []) if str(v).strip()][:8]


def ad_hash(title: str, description: str) -> str:
    return sha256(f"{title}\n{description}")


# ---------- without an LLM: skill words by section ----------

_MUST_HEAD = re.compile(
    r"profil|profile|requirements|qualifications?|qualifikation|what you bring|you bring|"
    r"you have|your skills|skills|mitbringst|bringst du mit|anforderungen|voraussetzungen|"
    r"must have|das zeichnet dich aus|what we expect|wir erwarten|kenntnisse"
)
_NICE_HEAD = re.compile(
    r"nice to have|nice-to-have|bonus|von vorteil|wunschenswert|wuenschenswert|optional|"
    r"pluspunkte|extra points|good to have"
)
_TASK_HEAD = re.compile(
    r"aufgaben|tasks|responsibilities|what you will do|what you'll do|your role|deine rolle|"
    r"das erwartet dich|your mission|job description|stellenbeschreibung"
)
_SKIP_HEAD = re.compile(
    r"wir bieten|we offer|benefits|what we offer|uber uns|ueber uns|about us|who we are|"
    r"das bieten wir|unser angebot|perks|kontakt|contact|bewerbung|how to apply"
)
_NICE_INLINE = re.compile(
    r"nice to have|a plus|von vorteil|wunschenswert|wuenschenswert|idealerweise|ideally|"
    r"preferably|optional|bonus|gerne auch|is an advantage|ist ein plus|pluspunkt"
)
_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|jahre)", re.IGNORECASE)
_SENIOR = re.compile(r"\b(senior|lead|principal|head of|teamleit\w*|leiter\w*)\b")
_STUDENT = re.compile(r"werkstud|working student|praktik|intern|hiwi|hilfskraft|thesis|"
                      r"abschlussarbeit|studentische|student assistant")  # fmt: skip


def _lines(description: str) -> list[str]:
    parts = re.split(r"\n+|\s[•·▪●]\s|\s-\s(?=[A-ZÄÖÜ])", description)
    return [p.strip() for p in parts if p.strip()]


def _is_heading(line: str) -> bool:
    """'Dein Profil', 'What you bring:'; not a bullet like 'Docker ist von Vorteil'."""
    words = len(line.split())
    return len(line) <= 60 and (words <= 5 or (line.rstrip().endswith(":") and words <= 10))


def extract_keywords(title: str, description: str, vocab: Vocabulary) -> Extraction:
    """Requirements from skill words: in the profile part of the ad they are must-haves,
    in a 'nice to have' part or in the tasks they are nice-to-haves; words in the
    'we offer' or 'about us' part are ignored. The title's skills are must-haves."""
    found: dict[str, str] = {name: "must" for name in vocab.find(title)}
    section = "unknown"
    years: float | None = None
    for line in _lines(description):
        head = fold(line)
        if _is_heading(line):
            for pattern, name in ((_SKIP_HEAD, "skip"), (_NICE_HEAD, "nice"),
                                  (_TASK_HEAD, "tasks"), (_MUST_HEAD, "must")):  # fmt: skip
                if pattern.search(head):
                    section = name
                    break
        if section == "skip":
            continue
        kind = "must" if section == "must" else "nice"
        if _NICE_INLINE.search(head):
            kind = "nice"
        for name in vocab.find(line):
            if found.get(name) != "must":
                found[name] = kind
        if section == "must" and years is None:
            m = _YEARS.search(line)
            years = float(m.group(1)) if m else None
    folded_title = fold(title)
    seniority = (
        "senior" if _SENIOR.search(folded_title)
        else "student" if _STUDENT.search(folded_title) else "unknown"
    )  # fmt: skip
    requirements = [
        Requirement(text=name, kind=kind, category="skill", skills=[name])
        for name, kind in found.items()
    ]
    return Extraction(requirements=requirements, seniority=seniority, min_years=years,
                      method="keywords")  # fmt: skip


# ---------- with the LLM, saved per ad ----------


class Extractor:
    """Gets the requirements of each ad: saved LLM answer > new LLM request (within the
    run's budget) > skill words. With several models (WERKBOT_LLM_MODEL=a,b), a rate limit
    or repeated errors move on to the next one; after the last, skill words for the rest."""

    def __init__(self, conn, vocab: Vocabulary, rules: LLMRules,
                 llm: LLM | list[LLM] | None = None, now: datetime | None = None):  # fmt: skip
        self.conn = conn
        self.vocab = vocab
        self.rules = rules
        llms = [] if llm is None else [llm] if isinstance(llm, LLM) else list(llm)
        self.llms = llms if rules.enabled else []
        self.now = now or datetime.now(UTC)
        self.stats: Counter = Counter()
        self.stopped: str | None = None  # why LLM requests stopped in this run
        self.switched: list[str] = []  # "gemini/... -> groq/...: rate limit"
        self._current = 0
        self._errors = 0
        self._system = load_prompt("requirements") if self.llms else ""

    @property
    def llm(self) -> LLM | None:
        return self.llms[self._current] if self._current < len(self.llms) else None

    def extract(self, title: str, description: str) -> Extraction:
        h = ad_hash(title, description)
        saved, failed_recently = self._saved(h)
        if saved is not None:
            self.stats["saved"] += 1
            return saved
        if failed_recently:
            self.stats["failed_before"] += 1
        while self._can_ask(description) and not failed_recently:
            try:
                result = self._ask(title, description)
            except RateLimited as exc:
                self._next_model(f"rate limit: {exc}")
                continue  # the same ad with the next model, if there is one
            except BadAnswer as exc:  # this ad confuses the model: don't retry for a while
                self.stats["failed"] += 1
                self._errors = 0
                logger.warning("LLM failed for '{}': {}", title, exc)
                self._save(h, "failed", {"error": str(exc)[:500]})
                break
            except LLMError as exc:  # server busy, timeout, connection: not the ad's fault
                self.stats["errors"] += 1
                self._errors += 1
                logger.warning("LLM error for '{}': {}", title[:70], exc)
                if self._errors >= MAX_ERRORS:
                    self._next_model(f"{MAX_ERRORS} errors in a row, last: {exc}")
                break  # this ad: skill words now, the LLM next run
            self._errors = 0
            self.stats["asked"] += 1
            self._save(h, "llm", result.model_dump(exclude={"method"}))
            logger.info("LLM read ad {} of at most {}: {}", self.stats["asked"],
                        self.rules.max_per_run, title[:70])  # fmt: skip
            return result
        self.stats["keywords"] += 1
        return extract_keywords(title, description, self.vocab)

    def _next_model(self, why: str) -> None:
        old = self.llm
        self._current += 1
        self._errors = 0
        new = self.llm
        if new is None:
            self.stopped = why
            logger.warning("LLM stopped ({}); skill words for the rest of the run", why)
        else:
            assert old is not None
            self.switched.append(f"{old.model} -> {new.model}: {why[:80]}")
            logger.warning("{} stopped ({}); continuing with {}", old.model, why[:120], new.model)

    def _can_ask(self, description: str) -> bool:
        if self.llm is None or len(description) < MIN_AD_CHARS:
            return False
        # Ads read (or given up on) count; busy-server errors don't
        if self.stats["asked"] + self.stats["failed"] >= self.rules.max_per_run:
            if not self.stopped:
                self.stopped = f"max_per_run ({self.rules.max_per_run}) reached"
            return False
        return True

    def _ask(self, title: str, description: str) -> Extraction:
        assert self.llm is not None
        ad = description[: self.rules.max_ad_chars]
        user = f"Title: {title}\n\nAd:\n{ad}"
        result = self.llm.ask(self._system, user, Extraction)
        assert isinstance(result, Extraction)
        result.method = "llm"
        return result

    def _saved(self, h: str) -> tuple[Extraction | None, bool]:
        """(saved LLM answer, whether the LLM failed on this ad in the last days)"""
        row = self.conn.execute(
            "SELECT method, prompt_version, data, created_at FROM job_requirements"
            " WHERE ad_hash = ?",
            (h,),
        ).fetchone()
        if row is None or row["prompt_version"] != PROMPT_VERSION:
            return None, False
        if row["method"] == "failed":
            created = datetime.fromisoformat(row["created_at"]).replace(tzinfo=UTC)
            return None, self.now - created < RETRY_FAILED_AFTER
        try:
            return Extraction.model_validate({**json.loads(row["data"]), "method": "llm"}), False
        except ValueError:
            return None, False

    def _save(self, h: str, method: str, data: dict) -> None:
        model = self.llm.model if self.llm else ""  # the model that answered
        self.conn.execute(
            "INSERT OR REPLACE INTO job_requirements"
            " (ad_hash, method, model, prompt_version, data, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, method, model, PROMPT_VERSION, json.dumps(data, ensure_ascii=False),
             self.now.strftime("%Y-%m-%d %H:%M:%S")),
        )  # fmt: skip
        self.conn.commit()
