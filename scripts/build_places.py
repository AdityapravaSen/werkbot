"""Build data/places.tsv: German places (and big cities nearby) with coordinates.

The location filter needs to know where "Kirchheimbolanden" or "Unterföhring" is, offline
and without an API. This script turns GeoNames data into a small table once; the
table is committed, so werkbot itself needs no extra package.

    uv run --with geonamescache==3.0.2 python scripts/build_places.py

Data: GeoNames (https://www.geonames.org), licensed CC BY 4.0, via geonamescache.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import geonamescache  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from werkbot.dedup.normalize import location_parts  # noqa: E402

OUT = ROOT / "data" / "places.tsv"
GERMANY_MIN_POPULATION = 500
# Neighbours: only bigger cities, so "Amsterdam" or "Strasbourg" are recognised (and
# then filtered by country or distance) instead of being an unknown place.
NEIGHBOURS = {"LU", "FR", "NL", "BE", "CH", "AT", "DK", "PL", "CZ"}
NEIGHBOUR_MIN_POPULATION = 15000
LATIN = re.compile(r"^[A-Za-zÀ-ÿ' .()\-/]+$")
SIMILAR_MIN = 80


def keys_for(name: str, alternatives: list[str], all_names: bool = False) -> list[str]:
    """Lookup keys: the place's own name (full and short form), plus alternative names
    that are spelling variants of it ("Garching bei Muenchen"). For German places,
    translations and codes ("Monaco di Baviera", "MUC") are left out: the English names
    boards really use (Munich, Cologne ...) are city aliases in werkbot.dedup.normalize.
    For the few foreign cities all names are kept, so "Wien" finds Vienna."""
    own = _parts(name)
    if not own:
        return []
    keys = list(dict.fromkeys(own))
    for alt in alternatives:
        for key in _parts(alt):
            similar = all_names or max(fuzz.ratio(key, k) for k in own) >= SIMILAR_MIN
            if len(key) >= 4 and similar and key not in keys:
                keys.append(key)
    return keys


def _parts(name: str) -> list[str]:
    if not LATIN.match(name):
        return []
    parts, _ = location_parts(name)
    return list(parts[0]) if len(parts) == 1 else []


def main() -> None:
    cache = geonamescache.GeonamesCache(min_city_population=GERMANY_MIN_POPULATION)
    rows = []
    for city in cache.get_cities().values():
        country, population = city["countrycode"], city["population"] or 0
        if country == "DE" or (country in NEIGHBOURS and population >= NEIGHBOUR_MIN_POPULATION):
            keys = keys_for(city["name"], city.get("alternatenames", []), country != "DE")
            if keys:
                rows.append(
                    (
                        city["name"],
                        country,
                        city.get("admin1code") or "",
                        f"{city['latitude']:.5f}",
                        f"{city['longitude']:.5f}",
                        str(population),
                        "|".join(keys),
                    )
                )
    rows.sort(key=lambda r: (r[1] != "DE", -int(r[5]), r[0]))
    header = (
        "# Places for the location filter. Built by scripts/build_places.py.\n"
        "# Data: GeoNames (https://www.geonames.org), CC BY 4.0, via geonamescache 3.0.2.\n"
        "# admin1 for Germany: 01 BW, 02 BY, 03 HB, 04 HH, 05 HE, 06 NI, 07 NW, 08 RP,\n"
        "# 09 SL, 10 SH, 11 BB, 12 MV, 13 SN, 14 ST, 15 TH, 16 BE\n"
        "name\tcountry\tadmin1\tlat\tlon\tpopulation\tkeys\n"
    )
    OUT.write_text(header + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} places -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
