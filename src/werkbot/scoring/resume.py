"""The master resume (data/master_resume.yaml) as seen by the fit score."""

import re
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from werkbot.scoring.skills import Vocabulary, key

SPOKEN = {"spoken", "languages_spoken", "sprachen"}  # skills lists that are not tech skills


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class Bullet(_Loose):
    text: str
    tags: list[str] = Field(default_factory=list)


class _Dated(_Loose):
    start: str | None = None
    end: str | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _text(cls, value):
        return None if value is None else str(value)


class Experience(_Dated):
    company: str = ""
    role: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class Education(_Dated):
    school: str = ""
    degree: str = ""
    details: list[str] = Field(default_factory=list)


class Project(_Loose):
    name: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class Summary(_Loose):
    id: str = ""
    text: str = ""


class Resume(_Loose):
    summary_variants: list[Summary] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: dict[str, list[str]] = Field(default_factory=dict)

    # ---------- text ----------

    def items(self) -> list[str]:
        """One text per bullet, with its context ("ML Engineer at X: built ...")."""
        out = []
        for job in self.experience:
            out += [f"{job.role} at {job.company}: {b.text}" for b in job.bullets]
        for project in self.projects:
            out += [f"Project {project.name}: {b.text}" for b in project.bullets]
        for edu in self.education:
            out.append(" ".join([edu.degree, edu.school, *edu.details]).strip())
        out += [s.text for s in self.summary_variants if s.text]
        return [t for t in out if t]

    def tech_skills(self) -> list[str]:
        return [s for name, values in self.skills.items() if name.lower() not in SPOKEN
                for s in values or []]  # fmt: skip

    def text(self) -> str:
        """The whole resume as one text (for the semantic part)."""
        parts = [s.text for s in self.summary_variants[:1]]
        parts += [f"{j.role}: " + " ".join(b.text for b in j.bullets) for j in self.experience]
        parts += [f"{p.name}: " + " ".join(b.text for b in p.bullets) for p in self.projects]
        parts += [f"{e.degree} {e.school}" for e in self.education]
        parts.append("Skills: " + ", ".join(self.tech_skills()))
        return "\n".join(p for p in parts if p.strip())

    def ideal_ad(self, vocab: Vocabulary) -> str:
        """A made-up job ad that fits this resume perfectly (top of the semantic scale).
        Built from roles and skills only, written like an ad: copying the bullets would
        make it nearly identical to the resume and squeeze every real ad's score."""
        roles = ", ".join(dict.fromkeys(j.role for j in self.experience if j.role))
        skills = sorted(self.skill_names(vocab))
        shown = ", ".join(skills[:12])
        return (
            f"Working student {roles} (m/w/d). Your tasks: you work with {shown} in our team. "
            f"Your profile: enrolled student with first experience in {', '.join(skills[:25])}."
        )

    # ---------- skills ----------

    def skill_names(self, vocab: Vocabulary) -> set[str]:
        """Skill names the resume shows (from skills lists, tags, roles and bullets),
        without the ones they imply."""
        names: set[str] = set()
        terms = [*self.tech_skills()]
        texts = []
        for job in self.experience:
            texts.append(job.role)
            for b in job.bullets:
                terms += b.tags
                texts.append(b.text)
        for project in self.projects:
            for b in project.bullets:
                terms += b.tags
                texts.append(b.text)
        for edu in self.education:
            texts += [edu.degree, *edu.details]
        for term in terms:
            name = vocab.canonical(term)
            if name:
                names.add(name)
            else:
                names.update(vocab.find(term))
        for text in texts:
            names.update(vocab.find(text))
        return names

    def terms(self) -> set[str]:
        """Every skill and tag as written (compare form), for skills not in skills.yaml."""
        out = {key(t) for t in self.tech_skills()}
        for bullets in [*(j.bullets for j in self.experience), *(p.bullets for p in self.projects)]:
            for b in bullets:
                out.update(key(t) for t in b.tags)
        return {t for t in out if t}

    def full_text_key(self) -> str:
        return key("\n".join([*self.items(), *self.tech_skills()]))

    # ---------- dates ----------

    def years_of_experience(self, today: date | None = None) -> float:
        """Work experience in years, overlapping jobs counted once."""
        today = today or date.today()
        months: set[int] = set()
        for job in self.experience:
            start = _month(job.start, today)
            end = _month(job.end, today, is_end=True)
            if start is not None and end is not None:
                months.update(range(start, end + 1))
        return round(len(months) / 12, 1)

    def is_student(self, today: date | None = None) -> bool:
        """Enrolled now: a degree that ends 'present', has no end, or ends in the future."""
        today = today or date.today()
        now = today.year * 12 + today.month - 1
        for edu in self.education:
            end = _month(edu.end, today, is_end=True)
            start = _month(edu.start, today)
            if (end is None or end >= now) and (start is None or start <= now + 12):
                return True
        return False


_PRESENT = {"present", "now", "today", "heute", "current", "ongoing", "aktuell"}


def _month(value: str | None, today: date, is_end: bool = False) -> int | None:
    """'2022-07' -> month number; 'present' -> this month; '2022' -> Jan (Dec for an end)."""
    if value is None:
        return today.year * 12 + today.month - 1 if is_end else None
    text = str(value).strip().lower()
    if text in _PRESENT or not text:
        return today.year * 12 + today.month - 1
    m = re.match(r"(\d{4})(?:[-/.](\d{1,2}))?", text)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else (12 if is_end else 1)
    return year * 12 + month - 1


class ResumeMissing(FileNotFoundError):
    pass


def load_resume(path: Path) -> Resume:
    path = Path(path)
    if not path.exists():
        raise ResumeMissing(
            f"{path} not found. Copy data/master_resume.example.yaml to {path} "
            "and fill in your details (the file is git-ignored)."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Resume.model_validate(data)
