"""Read data/filters.yaml into typed settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Decision = Literal["keep", "review", "drop"]
Kind = Literal[
    "working_student", "internship", "part_time", "thesis",
    "full_time_only", "apprenticeship", "dual_study", "phd",
]  # fmt: skip


class LocationRules(BaseModel):
    home: str = "Kaiserslautern"
    max_distance_km: float = 200
    max_train_hours: float = 3.0
    train_times: dict[str, str] = Field(default_factory=dict)  # place -> "H:MM"
    countries: list[str] = Field(default_factory=lambda: ["DE"])
    remote_outside_area: Decision = "keep"
    hybrid_outside_area: Decision = "review"
    unknown_place: Decision = "review"

    def train_hours(self) -> dict[str, float]:
        hours = {}
        for place, text in self.train_times.items():
            h, _, m = str(text).partition(":")
            hours[place] = int(h) + int(m or 0) / 60
        return hours


class LanguageRules(BaseModel):
    fluent: Decision = "drop"
    required: Decision = "review"
    good: Decision = "keep"
    plus: Decision = "keep"
    german_ad_no_mention: Decision = "review"


class RoleRules(BaseModel):
    kinds: list[Kind] = Field(
        default_factory=lambda: ["working_student", "internship", "part_time"]
    )
    not_wanted: list[Kind] = Field(
        default_factory=lambda: ["full_time_only", "apprenticeship", "dual_study", "phd"]
    )
    tech_words: list[str] = Field(default_factory=list)
    non_tech_words: list[str] = Field(default_factory=list)
    tech_sources: list[str] = Field(default_factory=list)


class SiteLimit(BaseModel):
    delay_s: float | None = None
    max_per_run: int | None = None
    skip: bool = False  # the job pages have no description (fetching would waste requests)


class DetailRules(BaseModel):
    min_description_chars: int = 300
    delay_s: float = 2.0
    max_per_run: int = 150
    retry_failed_after_days: int = 3
    per_site: dict[str, SiteLimit] = Field(default_factory=dict)


class FilterConfig(BaseModel):
    location: LocationRules = Field(default_factory=LocationRules)
    language: LanguageRules = Field(default_factory=LanguageRules)
    role: RoleRules = Field(default_factory=RoleRules)
    details: DetailRules = Field(default_factory=DetailRules)


@lru_cache
def load_filter_config(path: Path) -> FilterConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return FilterConfig.model_validate(data or {})
