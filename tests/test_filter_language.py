"""Phase 3: how much German a job needs."""

import pytest

from werkbot.filters.config import LanguageRules
from werkbot.filters.language import ad_language, german_level, judge_language


@pytest.mark.parametrize(
    ("text", "level"),
    [
        # fluent: dropped by default
        ("Sehr gute Deutsch- und Englischkenntnisse in Wort und Schrift", "fluent"),
        ("Fluent English and German", "fluent"),
        ("Deutsch auf C1-Niveau", "fluent"),
        ("German (C1) and English (B2)", "fluent"),
        ("Du sprichst verhandlungssicher Deutsch", "fluent"),
        ("Deutsch auf Muttersprachniveau", "fluent"),
        ("Excellent communication skills in German and English", "fluent"),
        ("Werkstudent:in/Working Student People Experience Team (German speaking)", "fluent"),
        ("Sehr gute Deutschkenntnisse, Englischkenntnisse von Vorteil", "fluent"),
        ("Sprachen:\n- Deutsch: fließend\n- Englisch: gut", "fluent"),
        ("Deutsch und Englisch fließend in Wort und Schrift", "fluent"),
        ("Native-level German and fluent English", "fluent"),
        ("Business fluent German", "fluent"),
        ("Du beherrschst die deutsche Sprache sicher in Wort und Schrift", "fluent"),
        ("Sehr gute Kenntnisse der deutschen und englischen Sprache in Wort und Schrift", "fluent"),
        # Seen in real ads (2026-10-19)
        (
            "Du kannst dich auf Deutsch sehr gut ausdrücken und verfügst idealerweise über "
            "weitere Sprachkenntnisse",
            "fluent",
        ),
        (
            "Sehr gute Deutsch- sowie Englischkenntnisse (weitere Sprachen sind von Vorteil)",
            "fluent",
        ),
        (
            "Du verfügst über sehr gute Deutsch- und Englischkenntnisse "
            "sowie gute MS-Office-Kenntnisse",
            "fluent",
        ),
        ("Sauberes, fehlerfreies Deutsch in Wort und Schrift", "fluent"),
        (
            "Du bringst Motivation und Einsatzbereitschaft mit und verfügst über sehr gute "
            "Deutschkenntnisse",
            "fluent",
        ),
        ("Sehr gute Deutschkenntnisse und ein gutes Gespür für digitale Kommunikation", "fluent"),
        # good: kept
        ("Sichere (B2) Deutschkenntnisse und fließende (C1) Englischkenntnisse", "good"),
        ("You speak German at least at B1 level and English at C1 level", "good"),
        ("Fluent English (company language) and German at B1 level or higher", "good"),
        ("Gute Deutsch- und Englischkenntnisse", "good"),
        ("English (fluent), German (good)", "good"),
        ("Fließende Englisch-, gute Deutschkenntnisse", "good"),
        ("Deutschkenntnisse (mind. B2)", "good"),
        ("Gute Englisch-, Deutsch- und MS-Office-Kenntnisse", "good"),
        ("Sehr gute Englischkenntnisse und gute Deutschkenntnisse", "good"),
        ("Sicherer Umgang mit MS Office sowie gute Deutschkenntnisse", "good"),
        ("Deutschkenntnisse auf B1-Niveau", "good"),
        ("Gute bis sehr gute Deutschkenntnisse", "good"),
        # plus: kept
        ("Fluent English, German is a plus", "plus"),
        ("Very good English skills; German is a plus", "plus"),
        ("Fluent English, basic German", "plus"),
        ("Deutschkenntnisse sind nicht zwingend erforderlich", "plus"),
        ("German language skills are a plus but not required", "plus"),
        ("fließende Deutschkenntnisse sind von Vorteil", "plus"),
        ("Gute Englischkenntnisse und idealerweise Deutschkenntnisse", "plus"),
        ("Sehr gute Englischkenntnisse, Deutschkenntnisse von Vorteil", "plus"),
        ("Good command of English; German is not required", "plus"),
        ("Strong English skills (German is a bonus)", "plus"),
        ("Sehr gute Deutsch- oder Englischkenntnisse", "plus"),  # either language is enough
        # required without a level: review
        ("Deutschkenntnisse erforderlich", "required"),
        ("Kenntnisse in Deutsch und Englisch", "required"),
        # not about the language
        ("You will work with German customers", "none"),
        ("Wir bieten einen Arbeitsplatz in Deutschland", "none"),
        ("Praktikum bei der Deutschen Bahn", "none"),
    ],
)
def test_german_level(text, level):
    assert german_level(text)[0] == level


GERMAN_AD = (
    "Wir suchen ab sofort eine engagierte Werkstudentin oder einen engagierten Werkstudenten "
    "zur Unterstützung unseres Teams im Bereich Softwareentwicklung. Du arbeitest an "
    "spannenden Projekten mit und lernst moderne Technologien kennen. Wir bieten flexible "
    "Arbeitszeiten und ein tolles Team."
)
ENGLISH_AD = (
    "We are looking for a working student to support our data team. You will build data "
    "pipelines in Python, work with our machine learning engineers and help us ship "
    "features. We offer flexible hours, a modern office and a friendly team."
)


def test_ad_language():
    assert ad_language(GERMAN_AD) == "de"
    assert ad_language(ENGLISH_AD) == "en"
    assert ad_language("Werkstudent Data Science") is None  # too short to tell


def test_judge_language_uses_the_rules():
    rules = LanguageRules()
    fluent = ENGLISH_AD + " Requirements: fluent German and English."
    plus = ENGLISH_AD + " German is a plus."
    assert judge_language([("Working Student", fluent)], rules).decision == "drop"
    assert judge_language([("Working Student", plus)], rules).decision == "keep"
    assert judge_language([("Working Student", ENGLISH_AD)], rules).decision == "keep"
    # A German ad that says nothing about language: probably German, so review
    german = judge_language([("Werkstudent", GERMAN_AD)], rules)
    assert (german.decision, german.ad_language) == ("review", "de")


def test_english_version_of_the_same_job_decides():
    """The German copy asks for German because it is written in German."""
    german_copy = GERMAN_AD + " Sehr gute Deutschkenntnisse."
    english_copy = ENGLISH_AD + " German is a plus."
    result = judge_language([("Werkstudent", german_copy), ("Working Student", english_copy)],
                            LanguageRules())  # fmt: skip
    assert (result.decision, result.level, result.ad_language) == ("keep", "plus", "en")


def test_board_flag_german_required():
    result = judge_language([("Working Student", ENGLISH_AD)], LanguageRules(), True)
    assert (result.decision, result.level) == ("review", "required")
