"""Phase 3: places, distances, train times and work mode."""

from pathlib import Path

import pytest

from werkbot.filters.config import LocationRules, load_filter_config
from werkbot.filters.location import LocationFilter, work_mode
from werkbot.filters.places import Gazetteer, distance_km

ROOT = Path(__file__).resolve().parents[1]
GAZ = Gazetteer.load(ROOT / "data" / "places.tsv")
RULES = load_filter_config(ROOT / "data" / "filters.yaml").location
HOME = GAZ.find("Kaiserslautern")


@pytest.mark.parametrize(
    ("location", "place", "km"),
    [
        ("Mannheim", "Mannheim", 51),
        ("Cologne, North Rhine-Westphalia, Germany", "Köln", 176),  # LinkedIn writes English
        ("Coblenz, Rhineland-Palatinate, Germany", "Koblenz", 102),
        ("Boblingen, Germany", "Böblingen", 124),  # umlaut dropped
        ("Garching bei München, BY, Germany", "Garching", 313),
        ("Neustadt, RP", "Neustadt an der Weinstraße", 29),  # state hint
        ("Neustadt an der Waldnaab", "Neustadt an der Waldnaab", 319),  # full name first
        ("St. Wendel, Saar", "Sankt Wendel", 44),
        ("Ramstein-Miesenbach", "Ramstein-Miesenbach", 16),
        ("Stuttgart-Feuerbach, BW, Germany", "Stuttgart Feuerbach", 123),
        ("Germany (Hamburg)", "Hamburg", 482),  # the city is in brackets
        ("Nürnberg Fürther Str. 111, Germany", "Nuremberg", 239),  # street address
        ("Knick Berlin", "Berlin", 522),  # a shop name around the city
        ("Wien, Österreich", "Vienna", 644),
    ],
)
def test_places_are_found(location, place, km):
    found, _ = GAZ.resolve(location)
    assert found[0].name == place
    assert round(distance_km(HOME, found[0])) == km


def judge(location, title="Werkstudent Data", description="", remote=False, means="remote"):
    mode, evidence = work_mode(title, location, description, remote, means)
    return LocationFilter(RULES, GAZ).judge([location], mode, evidence)


@pytest.mark.parametrize(
    ("location", "decision", "reason_start"),
    [
        ("Mannheim", "keep", "Mannheim, 51 km"),
        ("Ulm, Donau", "keep", "Ulm"),  # 199 km
        ("Düsseldorf", "keep", "Düsseldorf, 2:55 h by train"),  # 210 km, but a fast train
        ("Nürnberg", "drop", "too far"),  # 239 km and 3:56 h by train
        ("Berlin", "drop", "too far"),
        ("Amsterdam", "drop", "outside DE"),
        ("Luxembourg", "drop", "outside DE"),  # close, but another country
        ("Germany", "review", "no city given"),
        (None, "review", "no city given"),
        ("Parsdorf, Bavaria", "review", "unknown place"),
    ],
)
def test_location_decisions(location, decision, reason_start):
    result = judge(location)
    assert (result.decision, result.reason[: len(reason_start)]) == (decision, reason_start)


@pytest.mark.parametrize(
    ("title", "location", "description", "remote", "means", "mode"),
    [
        ("Werkstudent", "Remote", "", False, "remote", "remote"),
        ("Werkstudent", "Germany (remote)", "", False, "remote", "remote"),
        ("Werkstudent", "Remote / Düsseldorf", "", False, "remote", "remote"),
        ("Werkstudent", "Germany (Remote available)", "", False, "remote", "remote"),
        ("Werkstudent (Remote)", "Germany", "", False, "remote", "remote"),
        ("Werkstudent", "Hamburg", "Bei uns arbeitest du 100% remote.", False, "remote", "remote"),
        ("Werkstudent", "Berlin", "Homeoffice möglich", False, "remote", "hybrid"),
        ("Werkstudent", "Berlin", "teilweise mobiles Arbeiten", False, "remote", "hybrid"),
        ("Working Student", "München, Germany (Remote available)", "", True, "hybrid", "hybrid"),
        ("Werkstudent", "Berlin", "", True, "hybrid", "hybrid"),  # Arbeitsagentur's flag
        ("Werkstudent", "Berlin", "", True, "remote", "remote"),  # LinkedIn's flag
        ("Werkstudent - kein Home Office", "Lauffen", "", False, "remote", "onsite"),
        ("Werkstudent", "Berlin", "Homeoffice ist leider nicht möglich.", True, "remote", "onsite"),
        ("Werkstudent Remote Sensing", "Bremen", "Remote sensing data", False, "remote", "onsite"),
        ("Werkstudent", "Bonn", "", False, "remote", "onsite"),
        # "bundesweit" = offices in several cities, not working from home
        ("Werkstudent Field Assessment - bundesweit", "Köln", "", False, "remote", "onsite"),
    ],
)
def test_work_mode(title, location, description, remote, means, mode):
    assert work_mode(title, location, description, remote, means)[0] == mode


def test_remote_and_hybrid_outside_the_area():
    assert judge("Remote").decision == "keep"
    hybrid = judge("Berlin", description="Homeoffice möglich")
    assert (hybrid.decision, hybrid.mode) == ("review", "hybrid")  # hybrid_outside_area
    # Inside the area the work mode doesn't matter
    assert judge("Mannheim", description="Homeoffice möglich").decision == "keep"


def test_rules_come_from_the_config():
    strict = RULES.model_copy(update={"hybrid_outside_area": "drop", "max_distance_km": 50})
    lf = LocationFilter(strict, GAZ)
    assert lf.judge(["Berlin"], "hybrid", "homeoffice").decision == "drop"
    assert lf.judge(["Mannheim"], "onsite", "").decision == "drop"  # 51 km > 50
    assert lf.judge(["Köln"], "onsite", "").decision == "keep"  # still 2:27 h by train


def test_home_and_train_places_must_exist():
    with pytest.raises(ValueError, match="Atlantis"):
        LocationFilter(LocationRules(home="Atlantis"), GAZ)
    with pytest.raises(ValueError, match="Nowhere"):
        LocationFilter(LocationRules(train_times={"Nowhere": "1:00"}), GAZ)
