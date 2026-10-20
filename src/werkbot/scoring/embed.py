"""The semantic part of the fit score: how close an ad's text is to the resume, with a
local sentence-transformers model (optional extra: uv sync --extra ml).

Raw similarities of embedding models sit in a narrow band (e5: roughly 0.75-0.9 for any
two job-like texts), so they are rescaled against two anchors computed per run:
0 = as close as some clearly unrelated ads, 1 = as close as an ideal ad built from the
resume. That keeps the scale meaningful whichever model is configured.
"""

import math
from collections.abc import Sequence
from typing import Protocol

from loguru import logger


class EmbeddingsUnavailable(RuntimeError):
    pass


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceEmbedder:
    def __init__(self, model: str, prefix: str = ""):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingsUnavailable(
                "sentence-transformers is not installed (uv sync --extra ml)"
            ) from exc
        logger.info("Loading embedding model {} (downloads once) ...", model)
        try:
            self._model = SentenceTransformer(model)
        except Exception as exc:  # no network on the first run, unknown model name, ...
            raise EmbeddingsUnavailable(f"could not load {model}: {exc}") from exc
        self.prefix = prefix

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            [self.prefix + t for t in texts],
            batch_size=16,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, v)) for v in vectors]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class Scale:
    """Maps a raw similarity to 0..1 between a low and a high anchor."""

    MIN_SPREAD = 0.02

    def __init__(self, low: float, high: float):
        if high - low < self.MIN_SPREAD:
            logger.warning(
                "Embedding anchors too close ({:.3f} vs {:.3f}); semantic part is unreliable",
                low,
                high,
            )
            high = low + self.MIN_SPREAD
        self.low, self.high = low, high

    def __call__(self, raw: float) -> float:
        return min(1.0, max(0.0, (raw - self.low) / (self.high - self.low)))


class Semantic:
    """Similarity of job texts to the resume, and of requirement texts to its bullets."""

    def __init__(
        self,
        embedder: Embedder,
        resume_text: str,
        ideal_ad: str,
        unrelated_ads: list[str],
        items: list[str],
        skill_phrases: list[str],
        unrelated_requirements: list[str] | None = None,
    ):
        if not unrelated_ads:
            raise ValueError("scoring.yaml: semantic.unrelated_ads must not be empty")
        self.embedder = embedder
        vectors = embedder.encode([resume_text, ideal_ad, *unrelated_ads])
        self._resume, ideal, unrelated = vectors[0], vectors[1], vectors[2:]
        self.scale = Scale(
            low=sum(cosine(self._resume, u) for u in unrelated) / len(unrelated),
            high=cosine(self._resume, ideal),
        )
        self._items = embedder.encode(items) if items else []
        if self._items:
            # Requirements are short phrases, so their scale needs short anchors too (whole
            # ads as the low anchor sat almost as close as the high one: 0.830 vs 0.839)
            short = embedder.encode(unrelated_requirements) if unrelated_requirements else []
            lows = short or unrelated
            low = sum(self._best_item(u) for u in lows) / len(lows)
            phrases = embedder.encode(skill_phrases[:10]) if skill_phrases else []
            high = (
                sum(self._best_item(p) for p in phrases) / len(phrases)
                if phrases else max(cosine(self._items[0], i) for i in self._items)
            )  # fmt: skip
            self.item_scale = Scale(low, high)

    def job_scores(self, texts: list[str]) -> list[float]:
        return [self.scale(cosine(self._resume, v)) for v in self.embedder.encode(texts)]

    def requirement_scores(self, texts: list[str]) -> list[tuple[float, int]]:
        """(0..1 closeness, index of the closest resume item) per requirement text."""
        if not self._items or not texts:
            return [(0.0, -1) for _ in texts]
        out = []
        for v in self.embedder.encode(texts):
            sims = [cosine(v, item) for item in self._items]
            best = max(range(len(sims)), key=sims.__getitem__)
            out.append((self.item_scale(sims[best]), best))
        return out

    def _best_item(self, vector: Sequence[float]) -> float:
        return max(cosine(vector, item) for item in self._items)
