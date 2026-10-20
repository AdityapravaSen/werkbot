"""Language filter: how much German does the job need?

The word "Deutsch" alone decides nothing. What matters is what the ad says ABOUT German:

    fluent    "fließend", "verhandlungssicher", "sehr gute", "C1", "native", "German-speaking"
    good      "gute", "solide", "B1/B2", "good"
    plus      "von Vorteil", "wünschenswert", "a plus", "basic", "nicht erforderlich"
    required  German is required, but no level is given ("Deutschkenntnisse erforderlich",
              or just "Deutsch- und Englischkenntnisse")

Each level word is matched to the language it is closest to, so in "fluent English,
German is a plus" the "fluent" belongs to English and German is a plus. Languages joined
by "und/and" share their level: "sehr gute Deutsch- und Englischkenntnisse" = fluent German.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from werkbot.dedup.normalize import fold
from werkbot.filters.config import Decision, LanguageRules

Level = Literal["none", "plus", "good", "required", "fluent"]
SEVERITY: dict[str, int] = {"none": 0, "plus": 1, "good": 2, "required": 3, "fluent": 4}

# Sentences and bullet points. A level only counts within one of these. Split on the
# original text (capital letters mark a new sentence), after hiding abbreviations.
_CLAUSES = re.compile(r"[\n\r;!?•·●▪■◦►✓✔➤]+|\.\s+(?=[A-ZÄÖÜ])|\.$|\s[–—]\s|\s-\s(?=[A-ZÄÖÜ])")
_ABBREVIATIONS = re.compile(
    r"\b(mind|ca|bzw|inkl|max|min|evtl|ggf|vgl|bspw|insb|sog|usw|etc|std|nr|z\.\s?b|d\.\s?h|u\.\s?a)\.",
    re.IGNORECASE,
)

_GERMAN = re.compile(
    r"\bdeutsch(?:kenntniss\w*|sprach\w*|niveau\w*)?(?![a-z])"
    r"|\bdeutsche[nr]? (?:(?:und|sowie|oder|bzw|&) \w+ )?sprach\w*"
    r"|\bgerman(?:[- ]?(?:speaking|speaker)s?)?\b"
)
_OTHER = re.compile(
    r"\b(?:englisch\w*|english|franzoesisch\w*|french|spanisch\w*|spanish|italienisch\w*"
    r"|italian|niederlaendisch\w*|dutch|polnisch\w*|polish|russisch\w*|russian|tuerkisch\w*"
    r"|turkish|chinesisch\w*|chinese|mandarin|japanisch\w*|japanese|arabisch\w*|arabic"
    r"|portugiesisch\w*|portuguese)\b"
)
# "German" is also an adjective ("German customers"). It only counts as the language
# when the clause talks about languages.
_LANGUAGE_CONTEXT = re.compile(
    r"englisch|english|sprach|language|skill|kenntnis|speak|fluen|native|proficien"
    r"|\b[abc][12]\b|written|spoken|wort und schrift|level|niveau"
)

_LEVELS = [
    ("range", [r"\bgut\w* bis sehr gut\w*", r"\bgood to (?:very good|excellent)\b"]),  # = good
    ("fluent", [
        r"fliessend\w*", r"verhandlungssicher\w*", r"muttersprach\w*", r"sehr gut\w*",
        r"exzellent\w*", r"hervorragend\w*", r"ausgezeichnet\w*", r"perfekt\w*",
        r"fehlerfrei\w*", r"einwandfrei\w*", r"stilsicher\w*", r"eloquent\w*",
        r"\bsicher\w*(?= (?:auf |in )?(?:deutsch|german|beherrsch|kenntnis|sprach|in wort))",
        r"\bbeherrsch\w*",
        r"\bc1\b", r"\bc2\b",
        r"\bfluent\w*", r"\bfluency\b", r"\bnative\w*", r"\bexcellent\b", r"\bvery good\b",
        r"\boutstanding\b", r"\bproficien\w*", r"\bmother tongue\b", r"\bfirst language\b",
        r"\bstrong\b", r"\badvanced\b", r"\bperfect\w*", r"\bbusiness[- ]level\b",
        r"\b(?:german|deutsch)[- ]?(?:speaking|speakers?|sprachig\w*)",
    ]),
    ("plus", [
        r"(?:nicht|not|keine?|no)\s+(?:\w+\s+){0,3}?(?:erforderlich|notwendig|noetig|required"
        r"|necessary|needed|mandatory|vorausgesetzt|zwingend|a must|must)",
        r"\bkeine (?:deutsch\w*|german)", r"\bno german\b", r"von vorteil", r"vorteilhaft",
        r"wuenschenswert", r"\bein plus\b", r"\bplus\b", r"nice[- ]to[- ]have", r"\badvantage\w*",
        r"\bbeneficial\b", r"\bbonus\b", r"\boptional\b", r"idealerweise", r"\bideally\b",
        r"\bpreferabl\w*", r"\bpreferred\b", r"grundkenntnis\w*", r"\bbasic\w*",
        r"erste kenntnis\w*", r"\ba1\b", r"\ba2\b", r"\bwilling\w* to learn",
        r"\bbereitschaft\w*,? (?:die )?(?:deutsch\w*|deutsche sprache) zu lernen",
        r"\bhelpful\b", r"\bhilfreich\b",
    ]),
    ("good", [
        r"\bgut\w*", r"\bgood\b", r"\bsolid\w*", r"\bfundiert\w*", r"\bb1\b", r"\bb2\b",
        r"\bconversational\b", r"\bworking knowledge\b", r"\bintermediate\b",
    ]),
    ("required", [
        r"erforderlich", r"\brequired\b", r"\bmust\b", r"\bmuss\b", r"\bmandatory\b",
        r"zwingend\w*", r"vorausgesetzt", r"voraussetzung\w*", r"\bessential\b",
        r"\bnecessary\b", r"notwendig\w*", r"unbedingt", r"\bneeded\b", r"benoetigt",
    ]),
]  # fmt: skip
_LEVEL_RE = re.compile("|".join(f"(?P<{name}>{'|'.join(patterns)})" for name, patterns in _LEVELS))
# A level word further away than this from every language is not about languages.
MAX_LEVEL_GAP = 30
# Text between two languages that joins them: "Deutsch- und Englisch", "English and German"
_JOIN = re.compile(r"^[\s\-/,]*(?:(?:und|and|&|sowie|or|oder|bzw\.?|as well as)[\s\-/,]*)?$")


@dataclass
class _Mention:
    start: int
    end: int
    german: bool
    group: int = 0


def german_level(text: str) -> tuple[Level, str]:
    """The strictest German level the text asks for, and the words that show it."""
    best: Level = "none"
    evidence = ""
    for clause in _CLAUSES.split(_ABBREVIATIONS.sub(r"\1", text or "")):
        clause = fold(clause)
        if "deutsch" not in clause and "german" not in clause:
            continue
        context = bool(_LANGUAGE_CONTEXT.search(clause))
        for segment in _segments(clause):
            level, at = _segment_level(segment, context)
            if SEVERITY[level] > SEVERITY[best]:
                best, evidence = level, _window(segment, at)
    return best, evidence


def _window(segment: str, at: int, chars: int = 70) -> str:
    """The words around position `at`, cut at word boundaries."""
    start, end = max(0, at - chars), min(len(segment), at + chars)
    text = segment[start:end]
    if start > 0:
        text = text.split(" ", 1)[-1]
    if end < len(segment):
        text = text.rsplit(" ", 1)[0]
    return " ".join(text.split())


def _segments(clause: str) -> list[str]:
    """Split a clause at commas, but keep a part without any level word together with
    its neighbour: "Deutsch, Englisch: fließend" stays one segment, while
    "English (fluent), German (good)" becomes two."""
    parts = clause.split(",")
    segments: list[str] = []
    pending = ""
    for part in parts:
        current = f"{pending},{part}" if pending else part
        if _LEVEL_RE.search(current):
            segments.append(current)
            pending = ""
        else:
            pending = current
    if pending:
        if segments:
            segments[-1] = f"{segments[-1]},{pending}"
        else:
            segments.append(pending)
    return segments


def _segment_level(segment: str, clause_has_context: bool) -> tuple[Level, int]:
    """German's level in one segment, and where German is mentioned."""
    mentions = [_Mention(m.start(), m.end(), True) for m in _GERMAN.finditer(segment)]
    if not mentions:
        return "none", 0
    if not (clause_has_context or re.search(r"\bdeutsch", segment) or _level_near(segment)):
        return "none", 0  # "German customers", "German market"
    mentions += [_Mention(m.start(), m.end(), False) for m in _OTHER.finditer(segment)]
    mentions.sort(key=lambda m: m.start)
    either: set[int] = set()  # groups joined by "oder/or": one of the languages is enough
    for prev, cur in zip(mentions, mentions[1:], strict=False):
        gap = segment[prev.end : cur.start]
        joined = _JOIN.match(gap)
        cur.group = prev.group if joined else prev.group + 1
        if joined and re.search(r"\b(?:oder|or|bzw)\b", gap):
            either.add(cur.group)

    levels: dict[int, set[str]] = {}
    for match in _LEVEL_RE.finditer(segment):
        target = _language_of(segment, match.start(), match.end(), mentions)
        if target is not None:
            levels.setdefault(target.group, set()).add(match.lastgroup)

    result: Level = "none"
    at = 0
    for mention in mentions:
        if not mention.german:
            continue
        level = _combine(levels.get(mention.group, set()))
        others = any(not m.german and m.group == mention.group for m in mentions)
        if mention.group in either and others:
            level = "plus"  # "sehr gute Deutsch- oder Englischkenntnisse"
        if SEVERITY[level] > SEVERITY[result]:
            result, at = level, mention.start
    return result, at


def _level_near(segment: str, chars: int = 25) -> bool:
    """A level word right next to "German": "German is a plus", "basic German"."""
    for m in re.finditer(r"\bgerman\b", segment):
        window = segment[max(0, m.start() - chars) : m.end() + chars]
        if _LEVEL_RE.search(window):
            return True
    return False


# Only punctuation or a CEFR level between an adjective and its language:
# "fließende (C1) Englischkenntnisse", "sehr gute Deutsch-"
_ADJECTIVE_GAP = re.compile(r"[\s()\[\],:/\-]*(?:[abc][12]\+?[\s()\[\],:/\-]*)?")
# "... (weitere Sprachen sind von Vorteil)": about other languages
_OTHER_LANGUAGES = re.compile(r"\b(?:weitere|andere|zusaetzliche|other|additional|further)\b")


def _language_of(segment: str, start: int, end: int, mentions: list[_Mention]):
    """Which language a level word describes.

    1. right before a language, like an adjective: "fluent English", "gute Deutschkenntnisse"
    2. else the language just before it: "German at B1", "Deutsch ist von Vorteil"
    3. else the language just after it: "excellent skills in German"
    Nothing if every language is far away or the words talk about "other languages".
    """
    after = [m for m in mentions if m.start >= end]
    before = [m for m in mentions if m.end <= start]
    nxt = after[0] if after else None
    prev = before[-1] if before else None
    if (
        nxt
        and len(segment[end : nxt.start]) <= 15
        and _ADJECTIVE_GAP.fullmatch(segment[end : nxt.start])
    ):
        return nxt
    for candidate, gap in ((prev, segment[prev.end : start] if prev else ""),
                           (nxt, segment[end : nxt.start] if nxt else "")):  # fmt: skip
        if candidate and len(gap) <= MAX_LEVEL_GAP and not _OTHER_LANGUAGES.search(gap):
            return candidate
    inside = [m for m in mentions if m.start <= start and end <= m.end]
    return inside[0] if inside else None  # "German-speaking"


def _gap(start: int, end: int, mention: _Mention) -> int:
    if end <= mention.start:
        return mention.start - end
    if start >= mention.end:
        return start - mention.end
    return 0


def _combine(found: set[str]) -> Level:
    if "plus" in found:
        return "plus"  # "fließendes Deutsch ist von Vorteil" is still only a plus
    if "range" in found:
        return "good"  # "gute bis sehr gute Deutschkenntnisse"
    if "fluent" in found:
        return "fluent"
    if "good" in found:
        return "good"
    return "required"  # German is mentioned (or required) without a level


@lru_cache
def _detector():
    from lingua import Language, LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.GERMAN).build()


def ad_language(text: str, min_chars: int = 200) -> str | None:
    """'en' or 'de' for a description; None when it is too short or mixed."""
    if not text or len(text) < min_chars:
        return None
    values = _detector().compute_language_confidence_values(text[:3000])
    top = values[0]
    if top.value < 0.75:
        return None
    return "en" if top.language.name == "ENGLISH" else "de"


@dataclass
class LanguageResult:
    decision: Decision
    reason: str
    level: Level
    ad_language: str | None


def judge_language(
    versions: list[tuple[str, str]],
    rules: LanguageRules,
    german_required_flag: bool | None = None,
) -> LanguageResult:
    """`versions`: (title, description) from every board that lists the job.

    If one board has the ad in English, that version decides: the German version of the
    same job often asks for German only because it is written in German.
    """
    versions = list(dict.fromkeys((t or "", d or "") for t, d in versions))  # same text once
    langs = [ad_language(description) for _, description in versions]
    english = [v for v, lang in zip(versions, langs, strict=True) if lang == "en"]
    chosen = english or versions
    ad_lang = "en" if english else ("de" if "de" in langs else None)

    level: Level = "none"
    evidence = ""
    for title, description in chosen:
        for text in (title, description):
            found, words = german_level(text or "")
            if SEVERITY[found] > SEVERITY[level]:
                level, evidence = found, words
    if german_required_flag and SEVERITY[level] < SEVERITY["required"]:
        level, evidence = "required", "the board marks German as required"

    if level == "none":
        if ad_lang == "de":
            return LanguageResult(
                rules.german_ad_no_mention, "ad in German, no language requirement", level, ad_lang
            )
        return LanguageResult("keep", f"ad in {_name(ad_lang)}", level, ad_lang)
    decision: Decision = getattr(rules, level)
    return LanguageResult(decision, f"German {level}: '{evidence}'", level, ad_lang)


def _name(lang: str | None) -> str:
    return {"en": "English", "de": "German"}.get(lang or "", "unknown language")
