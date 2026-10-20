"""Skill words (data/skills.yaml): find skills in a text and match spellings."""

import re
from functools import lru_cache
from pathlib import Path

import yaml

from werkbot.dedup.normalize import _city_key, fold

SHORT = 2  # spellings this short only match in a text as written ("R", "Go", "ML")


def key(text: str) -> str:
    """Compare form of a spelling: lowercase, no umlauts in any spelling, '-' and '_' as
    spaces ("Scikit-Learn" = "scikit learn", "Datenbanken" = "datenbanken")."""
    text = _city_key(fold(text))
    return " ".join(re.sub(r"[-_]+", " ", text).split())


class Vocabulary:
    def __init__(self, skills: dict[str, list[str]], implies: dict[str, list[str]] | None = None):
        self.names = list(skills)
        self._by_key: dict[str, str] = {}
        self._exact: dict[str, str] = {}
        for name, aliases in skills.items():
            for spelling in [name, *(aliases or [])]:
                spelling = str(spelling)
                self._by_key.setdefault(key(spelling), name)
                if len(spelling) <= SHORT:
                    self._exact.setdefault(spelling, name)
        long_keys = sorted((k for k in self._by_key if len(k) > SHORT), key=len, reverse=True)
        self._folded = _alternation(long_keys, r"a-z0-9", r"a-z0-9+#")
        self._short = _alternation(
            sorted(self._exact, key=len, reverse=True), r"A-Za-z0-9&", r"A-Za-z0-9+#&"
        )
        mentioned = {n for name, targets in (implies or {}).items() for n in [name, *targets]}
        unknown = mentioned - set(skills)
        if unknown:
            raise ValueError(f"skills.yaml implies unknown skills: {', '.join(sorted(unknown))}")
        self._implies = {name: list(targets or []) for name, targets in (implies or {}).items()}

    @classmethod
    def load(cls, path: Path) -> "Vocabulary":
        return _load(Path(path))

    def canonical(self, term: str) -> str | None:
        """The skill name for one spelling ("k8s" -> "Kubernetes"), or None."""
        return self._by_key.get(key(term))

    def find(self, text: str) -> list[str]:
        """Skill names in a text, in order of first appearance. Longer spellings win
        ("neural networks" is Deep Learning, not Networking)."""
        hits: list[tuple[int, str]] = []
        if self._folded is not None:
            folded = key(text)
            hits += [(m.start(), self._by_key[m.group(0)]) for m in self._folded.finditer(folded)]
        if self._short is not None:
            # positions in the folded text and the original differ a little; order only
            hits += [(m.start(), self._exact[m.group(0)]) for m in self._short.finditer(text)]
        return list(dict.fromkeys(name for _, name in sorted(hits)))

    def closure(self, names) -> set[str]:
        """The names plus every skill they imply, following chains."""
        seen: set[str] = set()
        todo = list(names)
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            todo += self._implies.get(name, [])
        return seen


def _alternation(words: list[str], before: str, after: str) -> re.Pattern | None:
    if not words:
        return None
    body = "|".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![{before}])(?:{body})(?![{after}])")


@lru_cache(maxsize=4)
def _load(path: Path) -> Vocabulary:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Vocabulary(data.get("skills") or {}, data.get("implies") or {})
