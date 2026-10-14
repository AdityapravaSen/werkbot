"""Settings loaded from environment variables and the .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WERKBOT_", extra="ignore")

    llm_model: str = "gemini/gemini-2.5-flash"

    home_city: str = "Kaiserslautern"
    max_distance_km: int = 100
    max_age_days: int = 7

    db_path: Path = Path("data/werkbot.db")
    sources_path: Path = Path("data/sources.yaml")
    resume_path: Path = Path("data/master_resume.yaml")
    output_dir: Path = Path("output")

    # Polite scraping defaults
    request_timeout_s: float = 30.0
    max_parallel_requests: int = 5
    user_agent: str = "werkbot/0.1 (personal job search tool)"


@lru_cache
def get_settings() -> Settings:
    return Settings()
