"""Phase 3: kind of job and tech field. Titles are real ones from the first full run."""

from pathlib import Path

import pytest

from werkbot.filters.config import load_filter_config
from werkbot.filters.role import WordList, judge_role

RULES = load_filter_config(Path(__file__).resolve().parents[1] / "data" / "filters.yaml").role


@pytest.mark.parametrize(
    ("title", "job_types", "description", "source", "decision"),
    [
        ("Werkstudent Software Development (m/w/d)", [], "", "arbeitnow", "keep"),
        ("Werkstudent IT-Support (m/w/d)", [], "", "arbeitnow", "keep"),
        ("Werkstudent Softwareentwicklung", [], "", "arbeitnow", "keep"),  # German compound
        ("Werkstudent Wirtschaftsinformatik", [], "", "arbeitnow", "keep"),
        ("Werkstudent - IT Projektmanagement ERP Software (m/w/d)", [], "", "arbeitnow", "keep"),
        ("STUDENT ASSISTANT (all genders) - DATA SPACE INFRASTRUCTURE", [], "", "fraunhofer",
         "keep"),
        ("Working Student - Insights & Analytics", ["Working Student"], "", "workingstudentjobs",
         "keep"),
        ("Student (f/ m/ x) - Battery Aging Modelling", ["Werkstudentenjob"], "", "connecticum",
         "keep"),  # tech-only board
        ("Data Engineer (m/w/d)", [], "Wir suchen eine:n Werkstudent:in für Python.", "arbeitnow",
         "keep"),  # the kind is in the description
        ("Techniker (m/w/d) (Elektro/Bau/Versorgung)", ["TEMPORARY"],
         "Teilzeit (24 Std.) / Ramstein", "ramstein_softgarden", "review"),
        # not tech
        ("Werkstudent Marketing (m/w/d)", [], "", "arbeitnow", "drop"),
        ("Werkstudent Buchhaltung / Accounting", [], "", "arbeitsagentur", "drop"),
        ("Grafikdesigner:in für E-Mail & Ads (Werkstudent/Freelance)", [], "", "arbeitnow",
         "drop"),
        ("Working Student - Talent Acquisition", [], "", "arbeitnow", "drop"),
        # "Business/Sales Development" is not software development
        ("Werkstudent Customer Success & Business Development (d/w/m)", [], "", "arbeitnow",
         "drop"),
        ("Sales Development Representative (Werkstudent:in)", [], "", "linkedin_remote", "drop"),
        # tech and non-tech words: review
        ("Werkstudent Sales Support für SAP Sovereign Cloud", [], "", "linkedin_remote", "review"),
        # not the kind of job wanted
        ("Duales Studium Informatik/IT Automotive (m/w/d)", ["Internship"], "", "germantechjobs",
         "drop"),
        ("Auszubildende/r Elektroniker (m/w/d)", ["FULL_TIME"], "", "ramstein_softgarden", "drop"),
        ("Systemtechniker - Infrastruktur & Hardware (m/w/d)", ["FULL_TIME"],
         "Vollzeit (38.5 Std.) / Ramstein", "ramstein_softgarden", "drop"),
        ("SPECULATIVE APPLICATION: PHD STUDENT", [], "", "fraunhofer", "drop"),
        ("Master's Thesis »Valuation methods for pension products«", [], "", "fraunhofer", "drop"),
        # unclear without a description
        ("INITIATIVE APPLICATION: CAREER STARTER", [], "", "fraunhofer", "review"),
        ("Werkstudent (m/w/d)", [], "", "arbeitnow", "review"),
    ],
)  # fmt: skip
def test_judge_role(title, job_types, description, source, decision):
    assert judge_role(title, job_types, description, source, RULES).decision == decision


def test_tech_words_in_a_description_alone_do_not_keep_a_job():
    """Every ad mentions "MS Office", "Daten", "Digitalisierung"."""
    description = "Du arbeitest mit Daten, MS Office und unterstützt die Digitalisierung."
    result = judge_role("Werkstudent (d/w/m) | Politik und Gremien", [], description,
                        "arbeitnow", RULES)  # fmt: skip
    assert result.decision == "review"


def test_word_list_syntax():
    words = WordList(["entwickl*", "IT", "c++", "büro*", "*informatik"])
    assert [w for *_, w in words.find("Wirtschaftsinformatik")] == ["informatik"]
    found = [w for _, _, w in words.find("Softwareentwicklung, Entwickler, IT-Support, C++")]
    assert found == ["entwickl", "c++", "IT"]  # "entwickl*" must start a word
    assert words.find("it is fine") == []  # CAPITALS only match in capitals
    assert [w for *_, w in words.find("Buro und Büroarbeit")] == ["buro", "buro"]
