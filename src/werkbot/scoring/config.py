"""Read data/scoring.yaml into typed settings."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Weights(BaseModel):
    semantic: float = 40
    coverage: float = 45
    penalties: float = 15


class SemanticRules(BaseModel):
    model: str = "intfloat/multilingual-e5-small"
    prefix: str = "query: "
    unrelated_ads: list[str] = Field(default_factory=list)
    unrelated_requirements: list[str] = Field(default_factory=list)
    partial_match_min: float = 0.75
    no_ad_text: float = 0.4


class CoverageRules(BaseModel):
    must_weight: float = 2
    nice_weight: float = 1
    partial: float = 0.5
    prior_weight: float = 2
    ignore_categories: list[str] = Field(default_factory=lambda: ["language", "soft"])


class LLMRules(BaseModel):
    enabled: bool = True
    requests_per_minute: float = 8
    max_per_run: int = 100
    max_ad_chars: int = 6000
    timeout_s: float = 60


class PenaltyRules(BaseModel):
    german: dict[str, float] = Field(
        default_factory=lambda: {"fluent": 1.0, "required": 0.6, "good": 0.3, "plus": 0.0}
    )
    german_ad_no_mention: float = 0.4
    no_ad_text: float = 0.4
    location_review: float = 0.5
    role_review: float = 0.3
    senior: float = 1.0
    more_years: float = 0.5


class ShowRules(BaseModel):
    min_score: float = 0
    reason_items: int = 4


class ScoringConfig(BaseModel):
    weights: Weights = Field(default_factory=Weights)
    semantic: SemanticRules = Field(default_factory=SemanticRules)
    coverage: CoverageRules = Field(default_factory=CoverageRules)
    llm: LLMRules = Field(default_factory=LLMRules)
    penalties: PenaltyRules = Field(default_factory=PenaltyRules)
    show: ShowRules = Field(default_factory=ShowRules)


@lru_cache(maxsize=4)
def load_scoring_config(path: Path) -> ScoringConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return ScoringConfig.model_validate(data)
