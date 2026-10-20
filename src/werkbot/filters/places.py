"""Where is a place? Coordinates from data/places.tsv (GeoNames), fully offline."""

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from werkbot.dedup.normalize import _city_key, _words, fold, location_parts


@dataclass(frozen=True)
class Place:
    name: str
    country: str
    admin1: str  # German state code, see the header of data/places.tsv
    lat: float
    lon: float
    population: int


# State names and codes as boards write them -> GeoNames admin1 code. Used to pick the
# right one of several places with the same name ("Neustadt, RP").
_STATES = {
    "01": ["baden wurttemberg", "bw", "wurttemberg", "baden"],
    "02": ["bayern", "bavaria", "by"],
    "03": ["bremen", "hb"],
    "04": ["hamburg", "hh"],
    "05": ["hessen", "hesse", "he"],
    "06": ["niedersachsen", "lower saxony", "ni"],
    "07": ["nordrhein westfalen", "north rhine westphalia", "nrw", "nw", "rheinland"],
    "08": ["rheinland pfalz", "rhineland palatinate", "rlp", "rp", "pfalz"],
    "09": ["saarland", "sl", "saar"],
    "10": ["schleswig holstein", "sh"],
    "11": ["brandenburg", "bb"],
    "12": ["mecklenburg vorpommern", "mecklenburg western pomerania", "mv"],
    "13": ["sachsen", "saxony", "sn"],
    "14": ["sachsen anhalt", "saxony anhalt", "st"],
    "15": ["thuringen", "thuringia", "th"],
    "16": ["berlin", "be"],
}
_STATE_OF = {name: code for code, names in _STATES.items() for name in names}


def distance_km(a: Place, b: Place) -> float:
    """Straight-line (great-circle) distance."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class Gazetteer:
    def __init__(self, places: list[tuple[Place, list[str]]]):
        self._index: dict[str, list[Place]] = {}
        for place, keys in places:
            for key in keys:
                self._index.setdefault(key, []).append(place)

    @classmethod
    def load(cls, path: Path) -> "Gazetteer":
        return _load(path)

    def find(self, name: str, state_hints: frozenset[str] = frozenset()) -> Place | None:
        """Look a place up by name ('Kaiserslautern', 'Garching bei München')."""
        parts, _ = location_parts(name)
        if not parts:
            return None
        return self._pick(*parts[0], state_hints)

    def resolve(self, location: str | None) -> tuple[list[Place], list[str]]:
        """All places in a location string, and the names that could not be found."""
        found, unknown = self._resolve(location)
        if not found and location:
            # "Germany (Hamburg)", "Nürnberg Fürther Str. 111": look inside brackets and
            # drop street addresses, then try again
            cleaned = _STREET.sub(" ", re.sub(r"[()]", ",", location))
            if cleaned != location:
                again, missing = self._resolve(cleaned)
                if again:
                    return again, missing
        return found, unknown

    def _resolve(self, location: str | None) -> tuple[list[Place], list[str]]:
        hints = state_hints(location)
        found: list[Place] = []
        unknown: list[str] = []
        for full, short in location_parts(location)[0]:
            place = self._pick(full, short, hints)
            if place is None:
                unknown.append(full)
            elif place not in found:
                found.append(place)
        return found, unknown

    def _pick(self, full: str, short: str, hints: frozenset[str]) -> Place | None:
        # The exact name first ("neustadt an der waldnaab"), then the short one ("neustadt")
        candidates = self._candidates(full) or self._candidates(short)
        if not candidates and " " in short:
            # "Knick Berlin", "LUMAS Düsseldorf": a shop or team name around the city
            words = short.split()
            candidates = self._candidates(words[-1]) or self._candidates(words[0])
        if not candidates:
            return None
        german = [p for p in candidates if p.country == "DE"]
        candidates = german or candidates
        in_state = [p for p in candidates if p.admin1 in hints]
        return max(in_state or candidates, key=lambda p: p.population)

    def _candidates(self, key: str) -> list[Place]:
        found = self._index.get(key)
        if not found and key.startswith("st "):  # "St. Wendel" = "Sankt Wendel"
            found = self._index.get("sankt " + key[3:])
        return found or []


_STREET = re.compile(
    r"[\w.\-]*(?:str\.|straße|strasse|platz|weg|allee|gasse|damm|ufer)\b[\s\d\-/a-z]*|\b\d+\w*",
    re.IGNORECASE,
)


def state_hints(location: str | None) -> frozenset[str]:
    """German state codes mentioned in a location ('Karlsruhe, BW, DE' -> {'01'})."""
    text = _city_key(fold(location))
    codes = set()
    for part in re.split(r"[,;|/()]", text):
        code = _STATE_OF.get(_words(part))
        if code:
            codes.add(code)
    return frozenset(codes)


@lru_cache
def _load(path: Path) -> Gazetteer:
    rows: list[tuple[Place, list[str]]] = []
    with path.open(encoding="utf-8") as f:
        header = None
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if header is None:
                header = cols
                continue
            r = dict(zip(header, cols, strict=True))
            place = Place(
                name=r["name"],
                country=r["country"],
                admin1=r["admin1"],
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                population=int(r["population"] or 0),
            )
            rows.append((place, r["keys"].split("|")))
    return Gazetteer(rows)
