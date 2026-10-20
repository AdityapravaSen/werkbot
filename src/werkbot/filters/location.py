"""Location filter: is the job in the area (distance or train time), or remote?"""

import re
from dataclasses import dataclass, field
from typing import Literal

from werkbot.dedup.normalize import fold
from werkbot.filters.config import Decision, LocationRules
from werkbot.filters.places import Gazetteer, Place, distance_km

WorkMode = Literal["onsite", "hybrid", "remote"]

_HOME_OFFICE = r"(?:home[ -]?office|remote|mobile[sn]? arbeiten|mobile work\w*)"
# "kein Home Office", "no remote", "Homeoffice ist leider nicht möglich"
_NO_REMOTE = re.compile(
    rf"\b(?:kein\w*|no|not|nicht|ohne)\s+(?:\w+\s+){{0,2}}{_HOME_OFFICE}"
    rf"|{_HOME_OFFICE}\s+(?:\w+\s+){{0,2}}(?:nicht|not)\s+(?:moeglich|possible|available)"
    r"|\b(?:100\s?%|ausschliesslich|nur|only)\s+(?:vor ort|on[- ]?site|im buero|in the office)"
)
# Fully remote. ("deutschlandweit" / "bundesweit" are NOT here: they usually mean
# offices in several cities, not working from home.)
_FULL_REMOTE = re.compile(
    r"100\s?%\s*(?:remote|home ?office|mobil\w*)|\bfull(?:y)?[- ]remote|\bkomplett remote"
    r"|\bvollstaendig (?:remote|im home ?office|mobil)|\brein(?:es)? remote"
    r"|\bausschliesslich remote|\bremote[- ]first\b|\bremote[- ]only\b|\bnur remote\b"
    r"|\bwork from anywhere|\bortsunabhaengig"
    r"|\bremote (?:within|from|in|aus|innerhalb) (?:ganz )?(?:germany|deutschland)"
    r"|\b(?:remote|home ?office) (?:job|position|stelle|role)\b"
)
# Partly remote
_HYBRID = re.compile(
    r"\bhybrid\w*|\bhome[ -]?office|\bmobile[sn]? arbeiten|\bmobile work\w*"
    r"|\bremote\b(?![ -](?:sensing|control|access|server|desktop|maintenance|monitoring))"
    r"|\bwork from home\b|\bwfh\b|\bflexible[rns]? arbeitsort\w*|\bteilweise (?:remote|mobil)"
)
_REMOTE_ALTERNATIVE = re.compile(r"(?:^|[/,;|]|\boder\b|\bor\b)\s*remote\s*(?:$|[/,;|])")


def work_mode(
    title: str,
    location: str | None,
    description: str,
    remote_flag: bool = False,
    remote_means: str = "remote",
) -> tuple[WorkMode, str]:
    """How the job is done, with the words that show it.

    Checked in this order: an explicit "no home office" > fully remote > partly remote
    > what the board's remote flag means for that board > on site.
    """
    loc = fold(location)
    head = f"{fold(title)} | {loc}"
    text = f"{head} | {fold(description)}"
    if m := _NO_REMOTE.search(text):
        return "onsite", m.group(0)
    if m := _FULL_REMOTE.search(text):
        return "remote", m.group(0)
    # The location itself says remote: "Remote", "Germany (remote)", "Remote / Düsseldorf".
    # "Frankfurt (Remote available)" is only partly remote (found below).
    loc_plain = re.sub(r"\([^)]*\)", " ", loc).strip()
    loc_words = re.sub(r"[()]", " ", loc)
    says_remote = re.search(r"\bremote\b|\bhome ?office\b", loc_words)
    if says_remote and (
        _REMOTE_ALTERNATIVE.search(loc_plain) or _no_city_besides_remote(loc_words)
    ):
        return "remote", location or "remote"
    if re.search(r"\(\s*remote\s*\)", fold(title)):
        return "remote", "(remote) in the title"
    if m := _HYBRID.search(text):
        return "hybrid", m.group(0)
    if remote_flag:
        mode: WorkMode = "hybrid" if remote_means == "hybrid" else "remote"
        return mode, "the board marks it as remote"
    return "onsite", ""


def _no_city_besides_remote(loc: str) -> bool:
    rest = re.sub(
        r"\b(?:remote|home ?office|germany|deutschland|de|eu|europe|available|possible"
        r"|moeglich|option\w*|only|first|friendly|work|job)\b",
        " ",
        loc,
    )
    return not re.search(r"[a-z]{3,}", rest)


@dataclass
class LocationResult:
    decision: Decision
    reason: str
    mode: WorkMode
    places: list[str] = field(default_factory=list)
    distance_km: float | None = None
    in_area: bool = False


class LocationFilter:
    def __init__(self, rules: LocationRules, gazetteer: Gazetteer):
        self.rules = rules
        self.gazetteer = gazetteer
        home = gazetteer.find(rules.home)
        if home is None:
            raise ValueError(f"Home '{rules.home}' not found in the places table")
        self.home = home
        self.train: dict[Place, float] = {}
        for name, hours in rules.train_hours().items():
            place = gazetteer.find(name)
            if place is None:
                raise ValueError(f"train_times: place '{name}' not found in the places table")
            self.train[place] = hours

    def judge(self, locations: list[str | None], mode: WorkMode, evidence: str) -> LocationResult:
        """`locations`: every location string the boards gave for this job."""
        places: list[Place] = []
        unknown: list[str] = []
        for location in locations:
            found, missing = self.gazetteer.resolve(location)
            places += [p for p in found if p not in places]
            unknown += [u for u in missing if u not in unknown]

        names = [p.name for p in places]
        nearest = min((distance_km(self.home, p) for p in places), default=None)
        result = LocationResult("keep", "", mode, names, nearest)
        how = f" ({mode}: '{evidence}')" if mode != "onsite" and evidence else ""

        foreign = [p for p in places if p.country not in self.rules.countries]
        if places and len(foreign) == len(places):
            return self._set(result, "drop", f"outside {'/'.join(self.rules.countries)}: "
                             f"{', '.join(names)}")  # fmt: skip

        local = [p for p in places if p not in foreign]
        for place in sorted(local, key=lambda p: distance_km(self.home, p)):
            km = distance_km(self.home, place)
            hours = self.train.get(place)
            if km <= self.rules.max_distance_km:
                result.in_area = True
                return self._set(result, "keep", f"{place.name}, {km:.0f} km{how}")
            if hours is not None and hours <= self.rules.max_train_hours:
                result.in_area = True
                return self._set(
                    result, "keep", f"{place.name}, {_hm(hours)} by train ({km:.0f} km){how}"
                )

        where = ", ".join(f"{p.name} {distance_km(self.home, p):.0f} km" for p in local)
        if mode == "remote":
            return self._set(result, self.rules.remote_outside_area, f"remote{how}"
                             + (f", office: {where}" if where else ""))  # fmt: skip
        if unknown and not places:
            return self._set(
                result, self.rules.unknown_place, f"unknown place: {', '.join(unknown)}"
            )
        if mode == "hybrid" and local:
            return self._set(result, self.rules.hybrid_outside_area, f"hybrid{how}, {where}")
        if local:
            return self._set(result, "drop", f"too far: {where}")
        return self._set(result, self.rules.unknown_place, "no city given")

    @staticmethod
    def _set(result: LocationResult, decision: Decision, reason: str) -> LocationResult:
        result.decision, result.reason = decision, reason
        return result


def _hm(hours: float) -> str:
    return f"{int(hours)}:{round((hours % 1) * 60):02d} h"
