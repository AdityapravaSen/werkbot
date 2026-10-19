"""Normalizers: turn the many ways boards write the same thing into one form.

They are only used to compare jobs. What the user sees stays exactly as the
source wrote it.
"""

import hashlib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import yaml

# ---------- shared ----------

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def fold(text: str | None) -> str:
    """Lowercase, write umlauts out (ü -> ue), drop other accents (é -> e)."""
    if not text:
        return ""
    text = text.lower().translate(_UMLAUTS)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _words(text: str, keep: str = "") -> str:
    """Replace everything except letters, digits and `keep` with single spaces."""
    return " ".join(re.sub(rf"[^a-z0-9{re.escape(keep)}]+", " ", text).split())


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- URL ----------

# Query parameters that only track where a click came from. Compared lowercase.
TRACKING_PARAMS = {
    "ref", "refid", "trk", "trkinfo", "trackingid", "gclid", "fbclid", "msclkid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "igshid", "position", "pagenum",
}  # fmt: skip
_PATH_SAFE = "/:@!$&'()*+,;=-._~"


def normalize_url(url: str) -> str:
    """https, lowercase host without www, no tracking parameters, no trailing slash.

    The path keeps its case: some sites treat /Job/ and /job/ as different pages.
    """
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = quote(unquote(parts.path), safe=_PATH_SAFE).rstrip("/")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    )
    return urlunsplit(("https", host, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return sha256(normalize_url(url))


# ---------- title ----------

_GENDER_TAGS = [
    # (m/w/d) (w/ m/ d) (m/f/o) (m|w|d) (m,w,d) (m/w/div) (f/m/div.) [M /W /D] (d/f/m) (x|f|m)
    r"[(\[]\s*(?:[mwfdxoi]|div|divers)\.?\s*(?:[/|,]\s*(?:[mwfdxoi]|div|divers)\.?\s*)+[)\]]",
    # WeAreDevelopers cuts "(f/m/div)" to "iv)" in some titles
    r"(?<![a-z(])d?iv\.?\)",
    # m/w/d without brackets
    r"(?<![a-z])(?:[mwfdx]\s*/\s*){2}[mwfdx](?![a-z])",
    r"\(\s*(?:all genders?|alle geschlechter|any gender|genderneutral|gn\*?)\s*\)",
    r"\ball genders?\b",
    # Werkstudent:in, Werkstudent*in, Werkstudent_in, Mitarbeiter/-in, Mitarbeiter/in, Student(in)
    r"(?<=[a-z])(?:[:*_]|/-?|\()in(?:nen)?\)?(?![a-z])",
    # Wissenschaftliche*r, Wissenschaftliche:r, Auszubildende/r
    r"(?<=[a-z])[:*_/]r(?![a-z])",
]
_GENDER_RE = re.compile("|".join(f"(?:{p})" for p in _GENDER_TAGS))

# Different words for the same kind of job. Matched as whole words, longest first.
_ROLE_SYNONYMS = {
    "student": [
        "working students", "working student", "werkstudentinnen", "werkstudentin",
        "werkstudenten", "werkstudent", "studentische hilfskraefte",
        "studentische hilfskraft", "studentischer mitarbeiter", "studentische mitarbeiterin",
        "studentische mitarbeiter", "stud hilfskraft", "wissenschaftliche hilfskraefte",
        "wissenschaftliche hilfskraft", "wiss hilfskraft", "student assistants",
        "student assistant", "student worker", "student job", "studentenjob", "hiwi",
        "werkstudierende", "werkstudium", "werkstudententaetigkeit", "werksstudent",
    ],
    "intern": [
        "pflichtpraktikum", "praktikantin", "praktikant", "praktikum", "internship", "intern",
    ],
    "thesis": ["abschlussarbeit", "masterarbeit", "bachelorarbeit", "thesis"],
}  # fmt: skip
_SYNONYM_RE = [
    (re.compile(rf"\b{re.escape(phrase)}\b"), canonical)
    for canonical, phrases in _ROLE_SYNONYMS.items()
    for phrase in sorted(phrases, key=len, reverse=True)
]

# Filler words that boards add or drop ("im Bereich", "for", "ab sofort", "(remote)").
_TITLE_STOPWORDS = {
    "a", "an", "the", "and", "und", "of", "for", "fuer", "to", "at", "as", "als", "on",
    "in", "im", "with", "mit", "bei", "von", "vom", "zur", "zum",
    "der", "die", "das", "des", "den", "dem", "bereich", "area", "field",
    "ab", "sofort", "immediately", "remote", "hybrid", "homeoffice",
}  # fmt: skip


def normalize_title(title: str | None) -> str:
    """'Werkstudent*in im Bereich Data Science (m/w/d)' -> 'student data science'."""
    text = _GENDER_RE.sub(" ", fold(title))
    text = _words(text, keep="+#")  # keep C++ and C#
    for pattern, canonical in _SYNONYM_RE:
        text = pattern.sub(canonical, text)
    seen: set[str] = set()
    tokens = []
    for token in text.split():
        if token in _TITLE_STOPWORDS or token in seen or not token.strip("+#"):
            continue
        seen.add(token)
        tokens.append(token)
    return " ".join(tokens)


# ---------- company ----------

# Placeholders adapters use when a board shows no company, plus job-ad resellers that
# post other employers' ads on LinkedIn under their own name.
UNKNOWN_COMPANIES = {
    "", "unknown", "n a", "na", "none", "anonym", "anonymous", "confidential",
    "allgemeine informationen", "general information",  # a WeAreDevelopers heading
    "jobster", "heyjobs", "instaffo",
}  # fmt: skip

# Legal forms and other words that differ between boards for the same employer
# ("KPMG AG Wirtschaftsprüfungsgesellschaft" = "KPMG Deutschland", "Infineon Technologies").
_LEGAL_WORDS = {
    "gmbh", "ggmbh", "mbh", "ag", "se", "kg", "kgaa", "ohg", "gbr", "ug", "eg",
    "haftungsbeschraenkt", "partg", "mbb", "ev", "ek", "inc", "ltd", "llc", "plc", "corp",
    "corporation", "limited", "co", "sa", "bv", "nv", "sarl", "spa", "group", "gruppe",
    "holding", "deutschland", "germany", "technologies", "technology",
    "wirtschaftspruefungsgesellschaft", "steuerberatungsgesellschaft",
}  # fmt: skip


@lru_cache
def load_aliases(path: Path) -> tuple[tuple[re.Pattern, str], ...]:
    """Read data/company_aliases.yaml: {canonical name: [regex, ...]}."""
    if not path.exists():
        return ()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tuple(
        (re.compile(pattern), str(canonical))
        for canonical, patterns in data.items()
        for pattern in patterns
    )


def normalize_company(name: str | None, aliases=()) -> str:
    """'Deutsche Bahn AG' -> 'deutsche bahn'. Returns '' when the company is unknown."""
    text = re.sub(r"\be\.\s?[vk]\b\.?", " ", fold(name))  # e.V., e.K. (not E.ON)
    text = re.sub(
        r"\b(?:zweig)?niederlassung\b.*$", " ", text
    )  # "FERCHAU ... Niederlassung Darmstadt"
    plain = _words(text)
    if plain in UNKNOWN_COMPANIES:
        return ""
    base = _drop_legal(plain)
    for pattern, canonical in aliases:
        if pattern.search(base):
            return canonical
    # No alias: also drop bracketed parts, e.g. "Deutsches Zentrum ... (DLR)"
    return _drop_legal(_words(re.sub(r"\([^)]*\)", " ", text))) or base


def _drop_legal(text: str) -> str:
    tokens = [t for t in text.split() if t not in _LEGAL_WORDS]
    return " ".join(tokens) or text


# ---------- city ----------

_REMOTE_WORDS = re.compile(
    r"\b(remote|home ?office|mobile? (?:arbeiten|office)|deutschlandweit|bundesweit"
    r"|anywhere|work from home|wfh)\b"
)


def _city_key(text: str) -> str:
    """Boards write umlauts in city names three ways (Böblingen, Boeblingen, Boblingen).
    After fold() they are 'oe' or 'o'; this makes all of them 'o'."""
    return text.replace("ae", "a").replace("oe", "o").replace("ue", "u")


# States, countries, regions and state codes: never a city ("Karlsruhe, BW, DE").
_REGIONS = {
    _city_key(r)
    for r in {
        "germany", "deutschland", "de", "deu", "ger", "eu", "europe", "emea", "dach",
        "rheinland pfalz", "rhineland palatinate", "rlp", "saarland", "hessen", "hesse",
        "baden wuerttemberg", "bayern", "bavaria", "nordrhein westfalen",
        "north rhine westphalia", "nrw", "niedersachsen", "lower saxony", "sachsen",
        "saxony", "sachsen anhalt", "saxony anhalt", "thueringen", "thuringia",
        "brandenburg", "mecklenburg vorpommern", "mecklenburg western pomerania",
        "schleswig holstein", "bw", "by", "be", "bb", "hb", "hh", "he", "mv", "ni", "nw",
        "rp", "sl", "sn", "st", "sh", "th",
        "austria", "oesterreich", "switzerland", "schweiz", "netherlands", "nederland",
        "belgium", "france", "poland", "spain", "italy", "uk", "united kingdom", "usa",
        "noord holland", "zuid holland", "noord brabant", "overijssel", "gelderland",
        # regions written after a town name: "Karlsruhe, Baden", "Ulm, Donau"
        "baden", "wuerttemberg", "wuertt", "rheinland", "pfalz", "rhein", "neckar", "donau",
        "main", "taunus", "eifel", "westerwald", "wetterau", "saar", "bergstrasse",
        "schwarzwald", "odenwald", "allgaeu", "sauerland", "ruhr",
    }
}  # fmt: skip
# Words around a city name: "Munich HQ", "Köln Zentrale", "Location Munich"
_CITY_NOISE = {
    "hq", "headquarter", "headquarters", "zentrale", "location", "standort", "store",
    "campus", "office", "buro", "germany", "deutschland",
}  # fmt: skip
# English or older names -> one name (keys and values after _city_key).
_CITY_ALIASES = {
    "munich": "munchen", "cologne": "koln", "nuremberg": "nurnberg",
    "hanover": "hannover", "brunswick": "braunschweig", "coblenz": "koblenz",
    "treves": "trier", "frankfurt main": "frankfurt", "ludwigshafen rhein": "ludwigshafen",
}  # fmt: skip
_SPLIT = re.compile(r"[,;|/]|\s+-\s+|\s+(?:oder|or|und|and|&)\s+")
# "Frankfurt am Main" -> "frankfurt", "Garching bei München" -> "garching"
_QUALIFIER = re.compile(r"\s+(?:am|an der|a|bei|in der|im|vor der|ob der)\s+.*$")


def parse_location(location: str | None) -> tuple[list[str], bool]:
    """'70565 Stuttgart, Deutschland' -> (['stuttgart'], False). Cities in order.

    City names are a comparison key, not for display: umlauts become plain vowels
    ('München' -> 'munchen'), so every spelling a board uses gives the same key.
    """
    text = fold(location)
    remote = bool(_REMOTE_WORDS.search(text))
    cities: list[str] = []
    for part in _SPLIT.split(re.sub(r"\([^)]*\)", " ", text)):
        part = _words(re.sub(r"\b\d{4,5}\b", " ", part))  # postal codes
        if re.search(r"\d", part) or part.startswith(("kreis ", "landkreis ")):
            continue  # a street address ("Picassoplatz 1") or a district
        part = _city_key(" ".join(w for w in part.split() if w not in _CITY_NOISE))
        part = _CITY_ALIASES.get(part, part)
        part = _QUALIFIER.sub("", part)
        part = _CITY_ALIASES.get(part, part)
        is_city = part and part not in _REGIONS and not _REMOTE_WORDS.search(part)
        if is_city and part not in cities:
            cities.append(part)
    return cities, remote


def normalize_city(location: str | None, remote: bool = False) -> str:
    """The first city, 'remote' when there is no city but it's remote, else ''."""
    cities, says_remote = parse_location(location)
    if cities:
        return cities[0]
    return "remote" if (remote or says_remote) else ""


# ---------- fingerprint ----------


def fingerprint(company: str, title: str, city: str) -> str | None:
    """sha256 of the normalized company + title + city. None if the company is unknown."""
    if not company or not title:
        return None
    return sha256(f"{company}|{title}|{city}")
