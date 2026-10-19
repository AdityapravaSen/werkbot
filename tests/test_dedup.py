"""Phase 2: normalizers and the four duplicate checks, on real listings."""

import json
from pathlib import Path

import httpx
import pytest

from werkbot.adapters.fraunhofer import FraunhoferAdapter
from werkbot.adapters.workingstudentjobs import WorkingStudentJobsAdapter
from werkbot.config import Settings
from werkbot.dedup.match import Keys, fuzzy_reason, group_jobs
from werkbot.dedup.merge import merge
from werkbot.dedup.normalize import (
    load_aliases,
    normalize_city,
    normalize_company,
    normalize_title,
    normalize_url,
    parse_location,
)
from werkbot.models import Job, Source, SourceGroup

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
ALIASES = load_aliases(ROOT / "data" / "company_aliases.yaml")
KINDS = {"fraunhofer": "employer", "connecticum": "aggregator", "workingstudentjobs": "aggregator"}


def changed(job: Job, fields: dict) -> Job:
    """A validated copy of a job with some fields changed (model_copy skips validation)."""
    return Job.model_validate({**job.model_dump(), **fields})


def job(title, company="Acme GmbH", location="Kaiserslautern", source="board", **extra) -> Job:
    """A test job. Each call gets its own URL and ID unless they are given."""
    job.counter += 1
    job_id = extra.pop("source_job_id", str(job.counter))
    return Job(
        title=title,
        company=company,
        location=location,
        source=source,
        url=extra.pop("url", f"https://{source}.example/job/{job_id}"),
        source_job_id=job_id,
        **extra,
    )


job.counter = 0


def groups_of(jobs: list[Job]) -> list[list[int]]:
    keys = [Keys.of(j, ALIASES) for j in jobs]
    return [g.indexes for g in group_jobs(jobs, keys)]


# ---------- normalizers ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Werkstudent*in im Bereich emulator-basiertes Fuzzing", "student emulator basiertes fuzzing"),  # noqa: E501
        ("Werkstudent:in Datenanalyse und Digitalisierung", "student datenanalyse digitalisierung"),
        ("Werkstudent Audit Capture & AI Solutions (m/ w/ d)", "student audit capture ai solutions"),  # noqa: E501
        ("Studentische Hilfskraft (w/ m/ d) - miniSLR", "student minislr"),
        ("STUDENT ASSISTANT (all genders) - DATA SPACE", "student data space"),
        ("Student Assistant (m/f/o) for fact checking", "student fact checking"),
        ("Working Student - Acceptance Testing (f/m/d)", "student acceptance testing"),
        ("Stud. Hilfskraft (m/w/d) - Simulation", "student simulation"),
        ("Mitarbeiter/-in IT-Support (m/w/d)", "mitarbeiter it support"),
        ("Praktikum C# / C++ Entwicklung (m|w|d)", "intern c# c++ entwicklung"),
        ("Werkstudent / Working Student Data (gn*)", "student data"),
        ("Wissenschaftliche*r Mitarbeiter*in", "wissenschaftliche mitarbeiter"),
        # Seen in the real run (2026-10-19)
        ("Working Student in Software Development – Python (f/m/div.)", "student software development python"),  # noqa: E501
        ("Werkstudent im Bereich KI & Innovation [M /W /D]", "student ki innovation"),
        ("Working Student - Digital Solutions & AI iv)", "student digital solutions ai"),
        ("Auszubildende/r Elektroniker (m/w/d)", "auszubildende elektroniker"),
        ("Werkstudierende im Bereich Tools (m/w/d)", "student tools"),
        ("Working Student Data Science (x|f|m)", "student data science"),
        # Words that only look like gender endings stay
        ("Working Student: Infrastructure", "student infrastructure"),
        ("Werkstudent Business/Insights", "student business insights"),
    ],
)  # fmt: skip
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V.", "fraunhofer"),
        ("Fraunhofer IESE", "fraunhofer"),
        ("DFKI – Speech and Language Technology", "dfki"),
        ("Deutsches Forschungszentrum für Künstliche Intelligenz GmbH", "dfki"),
        ("Deutsches Zentrum für Luft- und Raumfahrt (DLR)", "dlr"),
        ("Deutsche Bahn AG", "deutsche bahn"),
        ("Deutsche Börse Group", "deutsche boerse"),
        ("Robert Bosch GmbH", "bosch"),
        ("Bosch Rexroth AG", "bosch rexroth"),  # a separate company
        ("SAP SE", "sap"),
        ("Müller & Co. KG", "mueller"),
        ("E.ON", "e on"),  # not mistaken for "e.V."
        ("Wandwerk e.K.", "wandwerk"),
        ("KPMG AG Wirtschaftsprüfungsgesellschaft", "kpmg"),
        ("KPMG Deutschland", "kpmg"),
        ("Infineon Technologies AG", "infineon"),
        ("Haufe-Lexware GmbH & Co.KG", "haufe"),
        ("ImmoScout24", "scout24"),
        ("Scout24 SE", "scout24"),
        ("FERCHAU GmbH Niederlassung Darmstadt", "ferchau"),
        ("Telekom Deutschland GmbH", "deutsche telekom"),
        ("Allgemeine Informationen", ""),  # a WeAreDevelopers heading, not a company
        ("Jobster", ""),  # posts other employers' ads on LinkedIn
        ("Unknown", ""),
        ("N/A", ""),
    ],
)
def test_normalize_company(raw, expected):
    assert normalize_company(raw, ALIASES) == expected


@pytest.mark.parametrize(
    ("raw", "city", "cities", "remote"),
    [
        ("70565 Stuttgart, Deutschland", "stuttgart", ["stuttgart"], False),
        ("Frankfurt am Main", "frankfurt", ["frankfurt"], False),
        ("Frankfurt a.M.", "frankfurt", ["frankfurt"], False),
        ("Munich, Bavaria, Germany", "munchen", ["munchen"], False),
        ("München", "munchen", ["munchen"], False),
        ("Boblingen, Germany", "boblingen", ["boblingen"], False),  # umlaut dropped
        ("Böblingen", "boblingen", ["boblingen"], False),
        ("Coblenz, Rhineland-Palatinate, Germany", "koblenz", ["koblenz"], False),  # LinkedIn
        ("Karlsruhe, BW, DE", "karlsruhe", ["karlsruhe"], False),  # state code
        ("Leonberg, Württemberg", "leonberg", ["leonberg"], False),  # region
        ("Otto-Ostrowski-Straße 7, 10249 Berlin", "berlin", ["berlin"], False),  # street
        ("Köln, Picassoplatz 1, Germany", "koln", ["koln"], False),
        ("Munich HQ", "munchen", ["munchen"], False),
        ("Wangen, Kreis Göppingen", "wangen", ["wangen"], False),
        ("Frankfurt am Main, Germany (Remote available)", "frankfurt", ["frankfurt"], True),
        ("Kaiserslautern, Rhineland-Palatinate, Germany", "kaiserslautern", ["kaiserslautern"], False),  # noqa: E501
        ("Saarbrücken; Kaiserslautern", "saarbrucken", ["saarbrucken", "kaiserslautern"], False),
        ("Garching bei München", "garching", ["garching"], False),
        ("Kaiserslautern (Remote möglich)", "kaiserslautern", ["kaiserslautern"], True),
        ("Remote", "remote", [], True),
        ("Homeoffice", "remote", [], True),
        ("Germany", "", [], False),
        (None, "", [], False),
    ],
)  # fmt: skip
def test_normalize_city(raw, city, cities, remote):
    assert normalize_city(raw) == city
    assert parse_location(raw) == (cities, remote)


def test_normalize_url():
    assert (
        normalize_url("http://www.Connecticum.de/jobs/x-1/?utm_source=a&b=2&a=1#top")
        == "https://connecticum.de/jobs/x-1?a=1&b=2"
    )
    # LinkedIn adds tracking parameters to the same job link
    assert normalize_url(
        "https://de.linkedin.com/jobs/view/4012?refId=abc&trackingId=x&position=1&pageNum=0"
    ) == normalize_url("https://de.linkedin.com/jobs/view/4012")
    # Encoded and plain characters are the same page; the path keeps its case
    assert normalize_url("https://jobs.fraunhofer.de/job/CC%2B%2B-1/") == normalize_url(
        "https://jobs.fraunhofer.de/job/CC++-1"
    )
    assert normalize_url("https://a.example/Job/1") != normalize_url("https://a.example/job/1")


# ---------- real duplicates ----------


def _fixture_jobs(adapter_cls, name, adapter, fixture) -> list[Job]:
    body = (FIXTURES / fixture).read_text(encoding="utf-8")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=body if len(calls) == 1 else "")

    source = Source(
        name=name,
        group=SourceGroup.HTML,
        adapter=adapter,
        url=f"https://{name}.example/",
        params={"delay_s": 0, "max_pages": 1},
    )
    return adapter_cls(source, Settings(), transport=httpx.MockTransport(handler)).fetch()


def test_fraunhofer_job_on_workingstudentjobs_is_merged():
    """The same Fraunhofer IESE job, read by our adapters from both real pages."""
    fraunhofer = _fixture_jobs(
        FraunhoferAdapter, "fraunhofer", "fraunhofer", "fraunhofer_list.html"
    )
    wsj = _fixture_jobs(
        WorkingStudentJobsAdapter,
        "workingstudentjobs",
        "workingstudentjobs",
        "workingstudentjobs_list.html",
    )
    jobs = fraunhofer + wsj
    merged = [g for g in groups_of(jobs) if len(g) > 1]

    assert len(merged) == 1
    pair = [jobs[i] for i in merged[0]]
    assert {j.source for j in pair} == {"fraunhofer", "workingstudentjobs"}
    assert all("DATA SPACE" in j.title for j in pair)
    # The link shown is the employer's own page, not the aggregator copy
    assert merge(pair, KINDS).main.source == "fraunhofer"


@pytest.fixture
def fraunhofer_vs_connecticum() -> dict[str, Job]:
    data = json.loads((FIXTURES / "fraunhofer_vs_connecticum.json").read_text(encoding="utf-8"))
    return {row.pop("tag"): Job.model_validate(row) for row in data["jobs"]}


def test_fraunhofer_site_vs_connecticum(fraunhofer_vs_connecticum):
    tagged = fraunhofer_vs_connecticum
    tags = list(tagged)
    groups = [{tags[i] for i in g} for g in groups_of(list(tagged.values()))]

    def group_of(tag):
        return next(g for g in groups if tag in g)

    # Same title on both sites (level 3, after "Fraunhofer SIT" = "Fraunhofer-Gesellschaft ...")
    assert {"F3", "C4"} <= group_of("F3")  # Werkstudent*in ... emulator-basiertes Fuzzing
    assert {"F4", "C7"} <= group_of("F4")  # Studentische Hilfskraft ... Windows Binary Fuzzing
    assert {"F5", "C1"} <= group_of("F5")  # Student Assistant Windows Binary Fuzzing
    assert {"F1", "C6"} <= group_of("F1")  # ... Backend-Entwicklung ... - C/C++
    # connecticum dropped " - C/C++" from the title (level 4, fuzzy)
    assert "C2" in group_of("F2")
    # connecticum lists the German one twice (C3 without " - C/C++", C6 with it). One board
    # with two IDs and different titles counts as two jobs, so C3 stays on its own.
    assert group_of("C3") == {"C3"}
    # Not duplicates
    for tag in ("F6", "F7", "C8", "C9", "C18", "C19"):
        assert group_of(tag) == {tag}, tag
    assert group_of("F3").isdisjoint(group_of("F4"))  # other city, other job


def test_merge_prefers_employer_link_longest_description_earliest_date(fraunhofer_vs_connecticum):
    from datetime import UTC, datetime

    fraunhofer = changed(
        fraunhofer_vs_connecticum["F3"],
        {"description": "short", "date_posted": datetime(2026, 10, 10, tzinfo=UTC)},
    )
    connecticum = changed(
        fraunhofer_vs_connecticum["C4"],
        {"description": "a much longer text", "date_posted": datetime(2026, 10, 1)},
    )
    merged = merge([connecticum, fraunhofer], KINDS)

    assert merged.main is fraunhofer
    assert merged.description == "a much longer text"
    assert merged.date_posted == datetime(2026, 10, 1, tzinfo=UTC)


# ---------- things that must NOT be merged ----------


def test_short_title_is_not_a_duplicate_of_a_longer_one():
    """token_set_ratio alone says 100 here, because one title's words are a subset."""
    assert groups_of([job("Werkstudent (m/w/d)"), job("Werkstudent Data Science (m/w/d)")]) == [
        [0],
        [1],
    ]


def test_working_student_and_werkstudent_are_the_same_title():
    jobs = [job("Working Student Data Science (m/w/d)"), job("Werkstudent Data Science")]
    assert groups_of(jobs) == [[0, 1]]  # level 3: they normalize to the same title


def test_one_board_with_two_ids_and_different_titles_is_two_jobs():
    """Fuzzy matching is only for copies on another board."""
    short, longer = "Werkstudent Software Development", "Werkstudent Software Development C++"
    same_board = [job(short), job(longer)]
    two_boards = [job(short), job(longer, source="b")]
    assert groups_of(same_board) == [[0], [1]]
    assert groups_of(two_boards) == [[0, 1]]


def test_same_title_in_different_cities_is_kept_apart():
    jobs = [job("Werkstudent Data Science", location="Berlin"), job("Werkstudent Data Science")]
    assert groups_of(jobs) == [[0], [1]]


def test_two_searches_on_one_site_count_as_one_board():
    """linkedin_local and linkedin_remote are both LinkedIn: no fuzzy match between them."""
    jobs = [
        job("Werkstudent Software Development", source="linkedin_local"),
        job("Werkstudent Software Development C++", source="linkedin_remote"),
    ]
    boards = {"linkedin_local": "linkedin", "linkedin_remote": "linkedin"}
    keys = [Keys.of(j, ALIASES, boards) for j in jobs]
    assert [g.indexes for g in group_jobs(jobs, keys)] == [[0], [1]]
    assert groups_of(jobs) == [[0, 1]]  # without the mapping they'd count as two boards


def test_a_shared_region_word_is_not_the_same_place():
    """Arbeitsagentur writes "Leonberg, Württemberg" and "Fellbach, Württemberg"."""
    jobs = [
        job("Werkstudent Vertrieb Innendienst", location="Leonberg, Württemberg"),
        job(
            "Werkstudent Vertrieb Innendienst Export", location="Fellbach, Württemberg", source="b"
        ),
    ]
    assert groups_of(jobs) == [[0], [1]]


def test_one_city_in_a_list_of_cities_is_the_same_place():
    jobs = [
        job(
            "Praktikant / Werkstudent Cyber Security Strategy", location="Berlin, Hamburg, München"
        ),
        job("Werkstudent Cyber Security Strategy", location="Hamburg", source="b"),
    ]
    assert groups_of(jobs) == [[0, 1]]


def test_remote_matches_any_city():
    jobs = [
        job("Werkstudent Data Science Analytics", location="Remote"),
        job("Werkstudent Data Science", source="b"),
    ]
    assert groups_of(jobs) == [[0, 1]]


def test_unknown_company_is_never_matched_by_title():
    """foorilla has no company names: two 'Unknown' jobs are not the same job."""
    jobs = [
        job("Working Student Marketing", company="Unknown", source_job_id="1"),
        job("Working Student Marketing", company="Unknown", source_job_id="2"),
    ]
    assert groups_of(jobs) == [[0], [1]]


def test_same_url_is_merged_even_without_company():
    url = "https://www.linkedin.com/jobs/view/42"
    jobs = [
        job("A", company="Unknown", source="linkedin_local", url=url),
        job("A", company="Unknown", source="linkedin_remote", url=url + "?trk=x"),
    ]
    assert groups_of(jobs) == [[0, 1]]


def test_one_short_title_does_not_chain_two_different_jobs():
    """'... Software' is similar to both, but Frontend and Backend are different jobs."""
    jobs = [
        job("Werkstudent Software Entwicklung Frontend", source="a"),
        job("Werkstudent Software Entwicklung", source="b"),
        job("Werkstudent Software Entwicklung Backend", source="c"),
    ]
    groups = groups_of(jobs)
    assert [0, 2] not in groups and [0, 1, 2] not in groups


@pytest.mark.parametrize(
    ("title_a", "title_b"),
    [
        ("Werkstudent HR Backend Sales", "Werkstudent Design Backend Sales"),
        ("Working Student AI Product Management", "Working Student HR Product Management"),
        ("Werkstudent IT Security", "Werkstudent OT Security"),
        ("Werkstudent Data Engineering", "Werkstudent Data Science"),
        ("Werkstudent Frontend Development", "Werkstudent Backend Development"),
    ],
)
def test_titles_that_differ_in_one_real_word_are_different_jobs(title_a, title_b):
    """token_set_ratio scores most of these >= 90, because the differing word is short."""
    assert groups_of([job(title_a), job(title_b, source="b")]) == [[0], [1]]


@pytest.mark.parametrize(
    ("title_a", "title_b"),
    [
        ("Werkstudent Software Engineer (m/w/d)", "Working Student Software Engineering"),
        ("Werkstudent Daten-Analyse", "Werkstudent:in Datenanalyse"),
        ("Werkstudent Data Science Analytics", "Data Science Werkstudent"),
        ("Working Student: Agentic AI Developer (2027)", "Working Student Agentic AI Developer"),
    ],
)  # fmt: skip
def test_spelling_variants_and_extra_words_are_the_same_job(title_a, title_b):
    assert groups_of([job(title_a), job(title_b, source="b")]) == [[0, 1]]


def test_borderline_titles_need_the_same_description():
    a = Keys.of(job("Werkstudent Backend Entwicklung Security Tools C++"), ALIASES)
    b = Keys.of(job("Werkstudent Backend Development Security Tools C++", source="b"), ALIASES)
    text = "Wir suchen Verstärkung für unser Team. " * 20

    assert fuzzy_reason(a, b) is None  # score 85-89, no descriptions
    assert fuzzy_reason(a, b, text, text) is not None
    assert fuzzy_reason(a, b, text, "Something else entirely. " * 20) is None
