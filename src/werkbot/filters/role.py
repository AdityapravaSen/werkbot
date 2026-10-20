"""Role filter: the kind of job (working student, internship, part-time ...) and the field
(tech or not)."""

import re
from dataclasses import dataclass, field
from functools import lru_cache

from werkbot.dedup.normalize import _city_key, fold
from werkbot.filters.config import Decision, RoleRules

# Kinds of job, matched on folded text (lowercase, ü -> ue)
_KIND_PATTERNS: dict[str, str] = {
    "working_student": (
        r"werkstud\w*|working[ -]?students?|studentische[rn]? (?:hilfskr\w*|mitarbeiter\w*"
        r"|aushilfe\w*)|stud\.? ?hilfskr\w*|student(?:ische)? assistant\w*|\bhiwi\w*"
        r"|wissenschaftliche[rn]? hilfskr\w*|student(?:en)?job\w*|student worker|werkstudium"
        r"|studierende\w*|^\s*student(?:/?in)?\b|\bstudent(?:/?in)?\s*[(\-–:]"
    ),
    "internship": r"\bpraktik\w*|\bintern\b|\binterns\b|\binternship\w*|\bpraxissemester",
    "part_time": (
        r"\bteilzeit\w*|\bpart[- ]?time\b|\bminijob\w*|\bmini-job\w*|\bgeringfuegig\w*"
        r"|\b(?:8|10|12|15|16|18|20)\s*(?:std|stunden|h|hours|wochenstunden)\b"
    ),
    "thesis": r"abschlussarbeit\w*|masterarbeit\w*|bachelorarbeit\w*|\bthesis\b",
    "apprenticeship": r"\bausbildung\b|auszubildende\w*|\bazubi\w*|\bapprentice\w*",
    "dual_study": r"\bdual\w* stud\w*|\bdhbw\b",
    "phd": r"\bphd\b|\bdoktorand\w*|\bdoctoral\b|\bpromotion\b",
}
_KIND_RE = {kind: re.compile(p) for kind, p in _KIND_PATTERNS.items()}
_FULL_TIME = re.compile(r"\bvollzeit\b|\bfull[- _]?time\b")
# Only the title decides these (a description may say "abgeschlossene Ausbildung").
_TITLE_ONLY = {"apprenticeship", "dual_study", "phd"}


def job_kinds(title: str, job_types: list[str], description: str = "") -> set[str]:
    """Kinds found in the title and job types, else in the description."""
    head = fold(f"{title} | {' | '.join(job_types)}")
    kinds = {k for k, rx in _KIND_RE.items() if rx.search(head)}
    wanted = kinds - _TITLE_ONLY
    if not wanted and description:
        body = fold(description[:4000])
        kinds |= {k for k, rx in _KIND_RE.items() if k not in _TITLE_ONLY and rx.search(body)}
    if not (kinds - _TITLE_ONLY) and (
        _FULL_TIME.search(head) or _FULL_TIME.search(fold(description[:1500]))
    ):
        kinds.add("full_time_only")
    return kinds


class WordList:
    """Words from filters.yaml: whole words, `word*` (word start), `*word` (word end, for
    German compounds: "*informatik" = Wirtschaftsinformatik), CAPITALS = case-sensitive."""

    def __init__(self, words: list[str]):
        folded, exact = [], []
        for word in words:
            prefix, suffix = word.endswith("*"), word.startswith("*")
            core = word.strip("*")
            if core.isupper() and any(c.isalpha() for c in core):
                exact.append(_pattern(core, prefix, suffix, r"A-Za-z0-9"))
            else:
                folded.append(_pattern(_key(core), prefix, suffix, r"a-z0-9"))
        self._folded = re.compile("|".join(folded)) if folded else None
        self._exact = re.compile("|".join(exact)) if exact else None

    def find(self, text: str) -> list[tuple[int, int, str]]:
        """(start, end, word) of every hit. Positions refer to _key(text) for normal words
        and to text for CAPITALS words; both have the same length for these purposes."""
        hits: list[tuple[int, int, str]] = []
        if self._folded:
            hits += [(m.start(), m.end(), m.group(0)) for m in self._folded.finditer(_key(text))]
        if self._exact:
            hits += [(m.start(), m.end(), m.group(0)) for m in self._exact.finditer(text)]
        return hits


def _without_overlaps(a: list[tuple[int, int, str]], b: list[tuple[int, int, str]]):
    """Drop hits that lie inside a longer hit of the other list: in "Business
    Development", the non-tech phrase wins over the tech word "develop*"."""

    def inside(hit, others):
        return any(o[0] <= hit[0] and hit[1] <= o[1] and (o[1] - o[0]) > (hit[1] - hit[0])
                   for o in others)  # fmt: skip

    return [h for h in a if not inside(h, b)], [h for h in b if not inside(h, a)]


def _key(text: str) -> str:
    """Lowercase, no umlauts in any spelling ("Büro" = "buero" = "Buro")."""
    return _city_key(fold(text))


def _pattern(word: str, prefix: bool, suffix: bool, chars: str) -> str:
    start = "" if suffix else rf"(?<![{chars}])"
    end = "" if prefix else rf"(?![{chars}+#])"
    return rf"{start}{re.escape(word)}{end}"


@lru_cache(maxsize=8)
def _lists(tech: tuple[str, ...], non_tech: tuple[str, ...]) -> tuple[WordList, WordList]:
    return WordList(list(tech)), WordList(list(non_tech))


@dataclass
class RoleResult:
    decision: Decision
    reason: str
    kinds: set[str] = field(default_factory=set)
    tech: bool | None = None  # None = unclear


def judge_role(
    title: str,
    job_types: list[str],
    description: str,
    source: str,
    rules: RoleRules,
) -> RoleResult:
    kinds = job_kinds(title, job_types, description)
    wanted = kinds & set(rules.kinds)
    unwanted = kinds & set(rules.not_wanted)
    kind_text = ", ".join(sorted(kinds)) or "unknown kind"

    if not wanted:
        if kinds:
            return RoleResult("drop", f"not wanted: {kind_text}", kinds)
        if description:
            return RoleResult("drop", "not a student / intern / part-time job", kinds)
        return RoleResult("review", "kind of job unknown (no description yet)", kinds)
    if unwanted & _TITLE_ONLY:
        return RoleResult("drop", f"not wanted: {kind_text}", kinds)

    tech, non_tech = _lists(tuple(rules.tech_words), tuple(rules.non_tech_words))
    head = f"{title} | {' | '.join(job_types)}"
    t_hits, n_hits = _without_overlaps(tech.find(head), non_tech.find(head))
    if t_hits and not n_hits:
        return RoleResult("keep", f"{kind_text}; tech: {_show(t_hits)}", kinds, True)
    if n_hits and not t_hits:
        return RoleResult("drop", f"not tech: {_show(n_hits)}", kinds, False)
    if t_hits and n_hits:
        return RoleResult(
            "review", f"tech ({_show(t_hits)}) and non-tech ({_show(n_hits)})", kinds, None
        )

    # The title says nothing about the field. A tech-only board settles it; otherwise the
    # description can show a clearly non-tech job. Tech words in a description are not
    # enough to keep a job (every ad mentions "MS Office", "Daten", "Digitalisierung").
    if source in rules.tech_sources:
        return RoleResult("keep", f"{kind_text}; tech job board", kinds, True)
    if description:
        t_found, n_found = _without_overlaps(tech.find(description), non_tech.find(description))
        t_desc, n_desc = len(t_found), len(n_found)
        if n_desc >= 3 and n_desc >= 2 * t_desc:
            return RoleResult("drop", "not tech (description)", kinds, False)
        return RoleResult(
            "review", f"field unclear (description: {t_desc} tech, {n_desc} non-tech words)",
            kinds, None,
        )  # fmt: skip
    return RoleResult("review", "field unclear", kinds, None)


def _show(hits: list[tuple[int, int, str]]) -> str:
    return ", ".join(dict.fromkeys(h[2].strip() for h in hits))
