"""Adapter base class and registry."""

import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from loguru import logger
from tenacity import RetryCallState, retry_if_exception, stop_after_attempt
from tenacity import retry as tenacity_retry

from werkbot.config import Settings
from werkbot.models import Job, Source

REGISTRY: dict[str, type["Adapter"]] = {}


def register(cls: type["Adapter"]) -> type["Adapter"]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set a `name`")
    REGISTRY[cls.name] = cls
    return cls


def _is_retryable(exc: BaseException) -> bool:
    """Retry on network errors, rate limits (429) and server errors (5xx)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


MAX_WAIT_S = 60.0


def _wait_time(state: RetryCallState) -> float:
    """How long to wait before the next try.

    On 429 the site often says how long to wait (Retry-After header), so we obey it.
    Otherwise back off exponentially: 1s, 2s, 4s, 8s ... capped at 20s.
    """
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after.strip().isdigit():
            return min(float(retry_after), MAX_WAIT_S)
        return min(15.0 * state.attempt_number, MAX_WAIT_S)
    return min(2.0 ** (state.attempt_number - 1), 20.0)


class Adapter(ABC):
    """Base class for all source adapters.

    `transport` lets tests swap in a fake HTTP layer (httpx.MockTransport),
    so tests never touch the real website.
    """

    name: str = ""

    def __init__(
        self,
        source: Source,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.source = source
        self.settings = settings
        self._transport = transport
        # Set when a source stopped early (e.g. rate-limited); the CLI shows these.
        self.warnings: list[str] = []

    @property
    def delay_s(self) -> float:
        """Pause between requests to the same site. A source can override it in sources.yaml."""
        return float(self.source.params.get("delay_s", self.settings.request_delay_s))

    def pause(self) -> None:
        time.sleep(self.delay_s)

    def warn(self, message: str) -> None:
        """Record a problem (shown as `partial` in the CLI) without stopping the scrape."""
        logger.warning("{}: {}", self.source.name, message)
        self.warnings.append(message)

    def client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.settings.request_timeout_s,
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
            transport=self._transport,
        )

    def get(self, client: httpx.Client, url: str, **params: Any) -> httpx.Response:
        """GET with automatic retries and exponential backoff."""

        @tenacity_retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.settings.max_retries),
            wait=_wait_time,
            reraise=True,
        )
        def _get() -> httpx.Response:
            logger.debug("GET {} {}", url, params or "")
            response = client.get(url, params=params or None)
            response.raise_for_status()
            return response

        return _get()

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Return every matching job currently listed by this source."""
