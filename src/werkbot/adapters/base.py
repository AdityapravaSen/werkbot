"""Adapter base class and registry."""

from abc import ABC, abstractmethod

import httpx

from werkbot.config import Settings
from werkbot.models import Job, Source

REGISTRY: dict[str, type["Adapter"]] = {}


def register(cls: type["Adapter"]) -> type["Adapter"]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set a `name`")
    REGISTRY[cls.name] = cls
    return cls


class Adapter(ABC):
    """Base class for all source adapters."""

    name: str = ""

    def __init__(self, source: Source, settings: Settings) -> None:
        self.source = source
        self.settings = settings

    def client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.settings.request_timeout_s,
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
        )

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Return every job currently listed by this source."""
