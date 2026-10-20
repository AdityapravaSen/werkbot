"""Settings loaded from environment variables and the .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WERKBOT_", extra="ignore")

    # Any LiteLLM model. "-latest" follows Google's newest Flash-Lite (largest free quota).
    llm_model: str = "gemini/gemini-flash-lite-latest"

    home_city: str = "Kaiserslautern"
    max_distance_km: int = 160
    max_age_days: int = 7
    expire_after_days: int = 14  # a job not seen for this long is marked expired

    db_path: Path = Path("data/werkbot.db")
    sources_path: Path = Path("data/sources.yaml")
    resume_path: Path = Path("data/master_resume.yaml")
    company_aliases_path: Path = Path("data/company_aliases.yaml")
    filters_path: Path = Path("data/filters.yaml")
    places_path: Path = Path("data/places.tsv")
    scoring_path: Path = Path("data/scoring.yaml")
    skills_path: Path = Path("data/skills.yaml")
    output_dir: Path = Path("output")

    # Polite scraping defaults
    request_timeout_s: float = 30.0
    request_delay_s: float = 1.0  # pause between pages of the same site
    max_retries: int = 5
    max_parallel_requests: int = 5
    user_agent: str = "werkbot/0.1 (personal job search tool)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
