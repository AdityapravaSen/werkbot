"""A small wrapper around LiteLLM: one JSON answer per request, checked with pydantic.

Any LiteLLM model works ("gemini/...", "groq/...", "ollama/..."); the API key comes from
.env (GEMINI_API_KEY, GROQ_API_KEY, ...). Free tiers allow only a few requests per minute
and per day, so requests are spaced out and a rate limit stops the LLM for the run.
"""

import json
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ValidationError

PROMPTS = Path(__file__).parent / "prompts"


class LLMError(Exception):
    """The request failed or the answer was unusable."""


class BadAnswer(LLMError):
    """The model answered, but not in the asked format (twice)."""


class RateLimited(LLMError):
    """The provider said 'too many requests' (often the daily free quota)."""


class LLMUnavailable(LLMError):
    """No API key for the configured model, or LiteLLM is not installed."""


def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def load_env(path: Path = Path(".env")) -> None:
    """Put .env into the environment so LiteLLM finds the API keys (keeps existing ones)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv comes with pydantic-settings
        return
    load_dotenv(path, override=False)


class LLM:
    def __init__(
        self,
        model: str,
        requests_per_minute: float = 8,
        timeout_s: float = 60,
        completion: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        retry_waits: tuple[float, ...] = (10, 30),
    ):
        self.model = model
        self.retry_waits = retry_waits  # pauses before retrying a busy / timed-out request
        self.min_gap = 60 / requests_per_minute if requests_per_minute > 0 else 0
        self.timeout_s = timeout_s
        self._completion = completion
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.requests = 0

    @classmethod
    def from_settings(cls, model: str, requests_per_minute: float, timeout_s: float) -> "LLM":
        """The real client. Raises LLMUnavailable if the model's API key is missing."""
        load_env()
        os.environ.setdefault("LITELLM_MODE", "PRODUCTION")  # don't let LiteLLM read .env too
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - litellm is a dependency
            raise LLMUnavailable("litellm is not installed") from exc
        litellm.suppress_debug_info = True
        logging.getLogger("LiteLLM").setLevel(logging.ERROR)  # no per-request warnings
        _drop_empty_keys()
        check = litellm.validate_environment(model=model)
        if not check.get("keys_in_environment", False):
            missing = " or ".join(check.get("missing_keys") or []) or "an API key"
            raise LLMUnavailable(f"{model} needs {missing} in .env")
        return cls(model, requests_per_minute, timeout_s, completion=litellm.completion)

    def ask(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        """Send one request and return the answer as `schema`. If the answer isn't valid,
        ask once more with the error (models sometimes drop a field)."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in (1, 2):
            text = self._send(messages)
            try:
                return schema.model_validate(parse_json(text))
            except (ValueError, ValidationError) as exc:
                logger.debug("LLM answer not valid (attempt {}): {}", attempt, exc)
                if attempt == 2:
                    raise BadAnswer(f"answer did not match the format: {exc}") from exc
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": f"That was not valid: {exc}. "
                                                "Answer again with only the JSON object."},
                ]  # fmt: skip
        raise AssertionError("unreachable")

    def _send(self, messages: list[dict]) -> str:
        """One answer. A busy server (503 "high demand"), a timeout or a dropped connection
        is retried after a pause; a rate limit is not (the quota won't come back soon)."""
        completion = self._completion
        if completion is None:
            raise LLMUnavailable("no LLM client")
        waits = list(self.retry_waits)
        while True:
            self._wait()
            try:
                response = completion(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout=self.timeout_s,
                    drop_params=True,  # providers that don't know an option just ignore it
                    **self._sampling(),
                )
            except Exception as exc:
                if _is_rate_limit(exc):
                    raise RateLimited(str(exc)[:300]) from exc
                if waits and _is_temporary(exc):
                    pause = waits.pop(0)
                    logger.debug("LLM busy ({}), retrying in {:g} s", type(exc).__name__, pause)
                    self._sleep(pause)
                    continue
                raise LLMError(f"{type(exc).__name__}: {_short(exc)}") from exc
            finally:
                self._last = self._clock()
                self.requests += 1
            break
        try:
            return response.choices[0].message.content or ""
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise LLMError("empty answer") from exc

    def _sampling(self) -> dict:
        """Temperature 0 for steady answers; Gemini 3+ prefers its default (LiteLLM warns)."""
        return {} if self.model.startswith("gemini/") else {"temperature": 0}

    def _wait(self) -> None:
        if self._last is None or not self.min_gap:
            return
        left = self.min_gap - (self._clock() - self._last)
        if left > 0:
            self._sleep(left)


def _drop_empty_keys() -> None:
    """`GROQ_API_KEY=` (empty, as in .env.example) means no key; LiteLLM would count it."""
    for name in [n for n, v in os.environ.items() if n.endswith("_API_KEY") and not v.strip()]:
        del os.environ[name]


def _is_temporary(exc: Exception) -> bool:
    """Server busy / down for a moment, timeouts, dropped connections."""
    if getattr(exc, "status_code", None) in {500, 502, 503, 504, 529}:
        return True
    names = {"ServiceUnavailableError", "InternalServerError", "Timeout", "TimeoutError",
             "APIConnectionError", "ConnectionError", "BadGatewayError"}  # fmt: skip
    if type(exc).__name__ in names:
        return True
    text = str(exc).lower()
    return any(w in text for w in ("503", "unavailable", "overloaded", "high demand", "timed out"))


def _short(exc: Exception) -> str:
    """The provider's message without the JSON around it."""
    text = str(exc)
    m = re.search(r'"message":\s*"([^"]+)"', text)
    return (m.group(1) if m else " ".join(text.split()))[:200]


def _is_rate_limit(exc: Exception) -> bool:
    if type(exc).__name__ == "RateLimitError" or getattr(exc, "status_code", None) == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "rate limit" in text


def parse_json(text: str) -> dict:
    """The JSON object in an answer, also if the model wrapped it in ```json fences."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in the answer")
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    return data
