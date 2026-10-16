"""Shared data models. Every adapter returns `Job` objects."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class SourceGroup(StrEnum):
    API = "A"  # public API or feed
    HTML = "B"  # plain HTML with page numbers
    JS = "C"  # loaded by JavaScript (hidden endpoint or Playwright)
    WATCH = "D"  # page watcher, no listings
    LINKEDIN = "E"  # python-jobspy


class Source(BaseModel):
    """One entry in data/sources.yaml."""

    name: str
    group: SourceGroup
    adapter: str
    url: str
    enabled: bool = True
    params: dict = Field(default_factory=dict)
    notes: str = ""


class Job(BaseModel):
    """A single job posting, as returned by an adapter (before dedup)."""

    title: str
    company: str
    url: HttpUrl  # the job page on the source site
    source: str
    apply_url: HttpUrl | None = None  # where to apply, if the source gives a separate link
    source_job_id: str | None = None
    location: str | None = None
    remote: bool = False
    description: str = ""
    date_posted: datetime | None = None
    job_types: list[str] = Field(default_factory=list)
    language: str | None = None  # filled in by the language filter
    # Some sources state this directly (germantechjobs, workingstudentjobs).
    # None = unknown; the Phase 3 language filter then decides from the text.
    german_required: bool | None = None
