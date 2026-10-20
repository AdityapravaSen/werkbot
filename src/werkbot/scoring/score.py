"""The fit score: how well a job fits the resume, 0-100, with a one-line reason.

    semantic   how close the ad's text is to the resume (local embeddings)
    coverage   share of the ad's requirements the resume shows (must-haves count double)
    penalties  full points unless something is wrong: German, location, level

A part that can't be computed (no embedding model; an ad without requirements) gives its
weight to the others. Nothing here sends the resume anywhere.
"""

import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from werkbot.scoring.config import ScoringConfig
from werkbot.scoring.embed import Embedder, Semantic
from werkbot.scoring.requirements import (
    MIN_AD_CHARS,
    Extraction,
    Extractor,
    Requirement,
    extract_keywords,
)
from werkbot.scoring.resume import Resume
from werkbot.scoring.skills import Vocabulary, key

Status = Literal["met", "partial", "missing", "ignored"]
CREDIT_FULL = 1.0


@dataclass
class Profile:
    """What the resume shows, prepared once per run."""

    names: set[str]  # skill names incl. everything they imply
    shown: set[str]  # skill names the resume names itself (for reasons)
    terms: set[str]  # skills and tags as written, compare form
    text: str  # whole resume, compare form
    items: list[str]
    student: bool
    years: float

    @classmethod
    def of(cls, resume: Resume, vocab: Vocabulary, today: date | None = None) -> "Profile":
        shown = resume.skill_names(vocab)
        return cls(
            names=vocab.closure(shown),
            shown=shown,
            terms=resume.terms(),
            text=resume.full_text_key(),
            items=resume.items(),
            student=resume.is_student(today),
            years=resume.years_of_experience(today),
        )


@dataclass
class Match:
    requirement: Requirement
    status: Status
    by: str = ""  # the resume skill or bullet that meets it

    @property
    def name(self) -> str:
        """Short name for reasons: "AWS/Azure", or the start of the text."""
        if self.requirement.skills:
            return "/".join(self.requirement.skills[:2])
        text = self.requirement.text.strip()
        return text if len(text) <= 32 else text[:30].rstrip() + "…"


@dataclass
class FilterInfo:
    """The filter results the penalties need (from job_filters)."""

    german_level: str | None = None
    ad_language: str | None = None
    location_decision: str | None = None
    role_decision: str | None = None
    location_reason: str = ""
    has_text: bool = True  # False: the ad has no text yet (only the title)


@dataclass
class Score:
    fit: int
    reason: str
    semantic: float | None
    coverage: float | None
    penalty: float  # 0..1 lost of the penalty part
    method: str
    matches: list[Match] = field(default_factory=list)
    penalties: list[tuple[str, float]] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)

    def details(self) -> dict:
        return {
            "requirements": [
                {
                    "text": m.requirement.text,
                    "kind": m.requirement.kind,
                    "category": m.requirement.category,
                    "skills": m.requirement.skills,
                    "status": m.status,
                    "by": m.by,
                }
                for m in self.matches
            ],  # fmt: skip
            "penalties": [{"why": why, "amount": amount} for why, amount in self.penalties],
            "tasks": self.tasks,
        }


# ---------- coverage ----------


def match_requirement(req: Requirement, profile: Profile, vocab: Vocabulary,
                      ignore: list[str]) -> tuple[Status | None, str]:  # fmt: skip
    """'met' / 'missing' / 'ignored', or None if only text similarity can tell."""
    if req.category in ignore:
        return "ignored", ""
    if req.category == "education":
        return ("met", "enrolled student") if profile.student else ("missing", "")
    if not req.skills:
        return (None, "") if req.category in {"skill", "experience"} else ("ignored", "")
    all_known = True
    for term in req.skills:
        name = vocab.canonical(term)
        names = [name] if name else vocab.find(term)
        for n in names:
            if n in profile.names:
                return "met", n
        if not names:
            all_known = False
        k = key(term)
        if k in profile.terms or (
            len(k) > 2
            and re.search(
                rf"(?<![a-z0-9]){re.escape(k)}"
                rf"(?![a-z0-9+#])",
                profile.text,
            )
        ):
            return "met", term  # fmt: skip
    # Known skills that the resume doesn't show are missing; for unknown words a
    # similar resume bullet may still count.
    return ("missing", "") if all_known else (None, "")


def coverage_of(matches: list[Match], config: ScoringConfig) -> float:
    """Weighted share of met requirements, pulled towards 0.5 by `prior_weight` (so an ad
    that names one thing isn't 0 or 100, and an ad without requirements is 0.5)."""
    rules = config.coverage
    credit = {"met": CREDIT_FULL, "partial": rules.partial, "missing": 0.0}
    total = rules.prior_weight
    got = rules.prior_weight * 0.5
    for m in matches:
        if m.status == "ignored":
            continue
        weight = rules.must_weight if m.requirement.kind == "must" else rules.nice_weight
        total += weight
        got += weight * credit[m.status]
    return got / total if total else 0.5


# ---------- penalties ----------


def penalties_of(info: FilterInfo, extraction: Extraction, profile: Profile,
                 config: ScoringConfig) -> list[tuple[str, float]]:  # fmt: skip
    rules = config.penalties
    found: list[tuple[str, float]] = []
    level = info.german_level or "none"
    if not info.has_text:  # nothing to check the language (or anything else) against
        found.append(("no ad text yet", rules.no_ad_text))
    elif level in rules.german and rules.german[level] > 0:
        found.append((_GERMAN_LABEL.get(level, f"German {level}"), rules.german[level]))
    elif level == "none" and info.ad_language == "de" and rules.german_ad_no_mention > 0:
        found.append(("German ad", rules.german_ad_no_mention))
    if info.location_decision == "review" and rules.location_review > 0:
        found.append((info.location_reason or "location unclear", rules.location_review))
    if info.role_decision == "review" and rules.role_review > 0:
        found.append(("role unclear", rules.role_review))
    if extraction.seniority == "senior" and rules.senior > 0:
        found.append(("senior position", rules.senior))
    if extraction.min_years and extraction.min_years > profile.years and rules.more_years > 0:
        found.append((f"asks {extraction.min_years:g}+ years", rules.more_years))
    return found


_GERMAN_LABEL = {
    "fluent": "fluent German",
    "required": "German required",
    "good": "good German",
    "plus": "German a plus",
}


# ---------- one job ----------


def combine(semantic: float | None, coverage: float | None, penalty: float,
            config: ScoringConfig) -> int:  # fmt: skip
    w = config.weights
    parts = [(w.semantic, semantic), (w.coverage, coverage), (w.penalties, 1 - penalty)]
    total = sum(weight for weight, value in parts if value is not None)
    if not total:
        return 0
    value = sum(weight * v for weight, v in parts if v is not None) / total
    return round(100 * value)


def reason_of(matches: list[Match], penalties: list[tuple[str, float]], limit: int) -> str:
    """'missing: Kubernetes, AWS, (Docker) · German required'. Nice-to-haves are in
    brackets. With nothing missing, what matched."""
    missing = [m.name for m in matches if m.status == "missing" and m.requirement.kind == "must"]
    missing += [f"({m.name})" for m in matches
                if m.status == "missing" and m.requirement.kind == "nice"
                and m.name not in missing]  # fmt: skip
    missing = list(dict.fromkeys(missing))  # "Compliance" asked for twice
    parts = []
    if missing:
        more = len(missing) - limit
        parts.append("missing: " + ", ".join(missing[:limit]) + (f" +{more}" if more > 0 else ""))
    else:
        met = [
            m.by if m.status == "met" and m.by else m.name
            for m in matches
            if m.status in {"met", "partial"} and m.requirement.category != "education"
        ]
        if met:
            parts.append("has: " + ", ".join(list(dict.fromkeys(met))[:limit]))
    parts += [why for why, _ in penalties]
    return " · ".join(parts) or "no requirements found"


def job_text(title: str, description: str, extraction: Extraction) -> str:
    """What the semantic part compares: the LLM's English summary of tasks and
    requirements if there is one (focused, and the same language as most resumes),
    otherwise the start of the ad."""
    if extraction.method == "llm" and (extraction.tasks or extraction.requirements):
        lines = [title, *extraction.tasks, *(r.text for r in extraction.requirements)]
        return "\n".join(lines)
    return f"{title}\n{description[:3000]}"


# ---------- all jobs ----------


def build_semantic(embedder: Embedder, resume: Resume, vocab: Vocabulary,
                   config: ScoringConfig) -> Semantic:  # fmt: skip
    skills = sorted(resume.skill_names(vocab))
    return Semantic(
        embedder,
        resume.text(),
        resume.ideal_ad(vocab),
        config.semantic.unrelated_ads,
        resume.items(),
        [f"Experience with {s}" for s in skills],
        config.semantic.unrelated_requirements,
    )


@dataclass
class ScoreStats:
    scored: int = 0
    methods: Counter = field(default_factory=Counter)
    removed: int = 0  # old scores of jobs that no longer pass the filters


def jobs_to_score(conn) -> list:
    return conn.execute(
        """SELECT j.id, j.title, j.description, f.decision, f.reasons, f.german_level,
                  f.ad_language, f.location_decision, f.role_decision
           FROM jobs j JOIN job_filters f ON f.job_id = j.id
           WHERE f.decision IN ('keep', 'review') AND j.status != 'expired'
           ORDER BY j.id"""
    ).fetchall()


def score_jobs(
    conn,
    resume: Resume,
    vocab: Vocabulary,
    config: ScoringConfig,
    extractor: Extractor,
    semantic: Semantic | None = None,
    today: date | None = None,
) -> ScoreStats:
    """Score every job that passed the filters and save it in the scores table."""
    profile = Profile.of(resume, vocab, today)
    ignore = config.coverage.ignore_categories
    rows = jobs_to_score(conn)
    stats = ScoreStats()

    # The LLM's budget per run is small: best candidates first, so they get the LLM's
    # reading this run and the rest (often unrelated jobs in "review") later.
    rows = sorted(rows, key=_priority_key(rows, profile, vocab, config, semantic))

    prepared = []  # (row, extraction, matches, texts needing similarity)
    for row in rows:
        extraction = extractor.extract(row["title"], row["description"] or "")
        matches, unsure = [], []
        for req in extraction.requirements:
            status, by = match_requirement(req, profile, vocab, ignore)
            match = Match(req, status or "missing", by)
            matches.append(match)
            if status is None:
                unsure.append(match)
        prepared.append((row, extraction, matches, unsure))

    if semantic is not None:
        texts = [job_text(r["title"], r["description"] or "", e) for r, e, _, _ in prepared]
        job_scores: list[float | None] = list(semantic.job_scores(texts))
        # A title alone is never as close as a whole ad (median 0 in the first real run),
        # and leaving the part out made title-only jobs top the list (their title's skills
        # match). So they get a middling value from scoring.yaml until their text arrives.
        for i, (row, *_) in enumerate(prepared):
            if len(row["description"] or "") < MIN_AD_CHARS:
                job_scores[i] = config.semantic.no_ad_text
        unsure_all = [m for _, _, _, unsure in prepared for m in unsure]
        closeness = semantic.requirement_scores([m.requirement.text for m in unsure_all])
        for m, (value, index) in zip(unsure_all, closeness, strict=True):
            if value >= config.semantic.partial_match_min:
                m.status, m.by = "partial", f"similar: {profile.items[index][:60]}"
    else:
        job_scores = [None] * len(prepared)
        for _, _, _, unsure in prepared:  # can't be checked without embeddings: not counted
            for m in unsure:
                m.status, m.by = "ignored", "needs embeddings to check"

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    for (row, extraction, matches, _), sem in zip(prepared, job_scores, strict=True):
        info = FilterInfo(
            german_level=row["german_level"],
            ad_language=row["ad_language"],
            location_decision=row["location_decision"],
            role_decision=row["role_decision"],
            location_reason=_location_reason(row["reasons"]),
            has_text=len(row["description"] or "") >= MIN_AD_CHARS,
        )
        penalties = penalties_of(info, extraction, profile, config)
        penalty = min(1.0, sum(amount for _, amount in penalties))
        cov = coverage_of(matches, config)
        result = Score(
            fit=combine(sem, cov, penalty, config),
            reason=reason_of(matches, penalties, config.show.reason_items),
            semantic=sem,
            coverage=cov,
            penalty=penalty,
            method=extraction.method,
            matches=matches,
            penalties=penalties,
            tasks=extraction.tasks,
        )
        _save(conn, row["id"], result, now)
        stats.scored += 1
        stats.methods[result.method] += 1

    stats.removed = conn.execute(
        """DELETE FROM scores WHERE job_id NOT IN (
               SELECT f.job_id FROM job_filters f JOIN jobs j ON j.id = f.job_id
               WHERE f.decision IN ('keep', 'review') AND j.status != 'expired')"""
    ).rowcount
    conn.commit()
    return stats


def _priority_key(rows, profile: Profile, vocab: Vocabulary, config: ScoringConfig,
                  semantic: Semantic | None):  # fmt: skip
    """Order for reading ads: kept jobs, then jobs whose role is clearly right, then by a
    quick estimate (skill words, plus the semantic part if available)."""
    ignore = config.coverage.ignore_categories
    estimate: dict[int, float] = {}
    for row in rows:
        quick = extract_keywords(row["title"], row["description"] or "", vocab)
        matches = []
        for req in quick.requirements:
            status, _ = match_requirement(req, profile, vocab, ignore)
            matches.append(Match(req, status or "ignored"))
        estimate[row["id"]] = coverage_of(matches, config)
    if semantic is not None and rows:
        texts = [f"{r['title']}\n{(r['description'] or '')[:3000]}" for r in rows]
        for row, value in zip(rows, semantic.job_scores(texts), strict=True):
            estimate[row["id"]] += value

    def key(row) -> tuple:
        return (row["decision"] != "keep", row["role_decision"] != "keep",
                -estimate[row["id"]], row["id"])  # fmt: skip

    return key


def _location_reason(reasons_json: str | None) -> str:
    """ "location: hybrid (hybrid: 'remote'), Berlin 522 km" -> 'hybrid, Berlin 522 km'"""
    for reason in json.loads(reasons_json or "[]"):
        name, _, rest = reason.partition(": ")
        if name == "location":  # without the evidence in brackets
            return re.sub(r"\s*\([^)]*\)", "", rest).strip()
    return ""


def _save(conn, job_id: int, s: Score, now: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO scores
               (job_id, fit_score, reason, scored_at, semantic, coverage, penalty, method, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, s.fit, s.reason, now, _round(s.semantic), _round(s.coverage), round(s.penalty, 3),
         s.method, json.dumps(s.details(), ensure_ascii=False)),
    )  # fmt: skip


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def export_scores_csv(conn, path: Path) -> int:
    """Scored jobs, best first (opens in Excel with umlauts intact)."""
    rows = conn.execute(
        """SELECT s.fit_score, s.reason, j.title, j.company, f.places, f.distance_km,
                  f.work_mode, f.german_level, f.decision, s.semantic, s.coverage, s.penalty,
                  s.method, j.main_url, j.first_seen_at, j.id
           FROM scores s JOIN jobs j ON j.id = s.job_id JOIN job_filters f ON f.job_id = j.id
           ORDER BY s.fit_score DESC, j.first_seen_at DESC"""
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["score", "reason", "title", "company", "place", "km", "work mode", "German",
             "filter", "semantic", "coverage", "penalty", "requirements from", "link",
             "first seen", "id"]
        )  # fmt: skip
        for r in rows:
            writer.writerow(list(r))
    return len(rows)
