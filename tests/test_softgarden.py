"""Tests for the softgarden feed adapter. Fake feed copies the real format (2026-10-16)."""

import httpx

from werkbot.adapters.softgarden import SoftgardenFeedAdapter
from werkbot.config import Settings
from werkbot.models import Source, SourceGroup
from werkbot.text import matches_keyword

FEED_URL = "https://ramsteinaf.career.softgarden.de/jobs.feed.json"


def _element(job_id, title, city, employment="FULL_TIME"):
    return {
        "@type": "DataFeedItem",
        "dateModified": "2026-10-16T05:01:55.000Z",
        "item": {
            "@type": "JobPosting",
            "title": title,
            "url": f"https://ramsteinaf.career.softgarden.de/jobs/{job_id}/x/",
            "datePosted": "2026-10-08T15:32:10.028+02:00",
            "identifier": {"name": "U.S. Air Force", "@type": "PropertyValue", "value": job_id},
            "description": "<p>Vollzeit</p><p>Aufgaben: <b>Netzwerke</b></p>",
            "employmentType": employment,
            "hiringOrganization": {"name": "U.S. Air Force – Personalbüro Ramstein Air Base"},
            "jobLocation": {
                "@type": "Place",
                "address": {"postalCode": "67655", "addressLocality": city},
            },
        },
    }


FEED = {
    "@type": "DataFeed",
    "numberOfItems": 5,
    "dataFeedElement": [
        _element(1, "IT-Systemadministrator (m/w/d)", "Kaiserslautern"),
        _element(2, "Ingenieur (Elektroingenieurwesen) (m/w/d)", "Ramstein-Miesenbach"),
        _element(3, "Sachbearbeiter Mitarbeiterverwaltung (m/w/d)", "Kaiserslautern"),
        _element(4, "Netzwerktechniker (m/w/d)", "Germersheim"),  # other location
        _element(1, "IT-Systemadministrator (m/w/d)", "Kaiserslautern"),  # duplicate
    ],
}


def _adapter(feed, **params) -> SoftgardenFeedAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=feed)

    source = Source(
        name="ramstein_softgarden",
        group=SourceGroup.API,
        adapter="softgarden_feed",
        url=FEED_URL,
        params=params,
    )
    settings = Settings(request_delay_s=0, max_retries=2, _env_file=None)
    return SoftgardenFeedAdapter(source, settings, transport=httpx.MockTransport(handler))


def test_filters_by_location_and_title_keywords():
    jobs = _adapter(
        FEED,
        locations=["Kaiserslautern", "Ramstein-Miesenbach"],
        title_keywords=["IT", "Ingenieur"],
    ).fetch()

    assert [j.source_job_id for j in jobs] == ["1", "2"]  # 3: no keyword, 4: wrong city


def test_no_filters_keeps_all_unique_jobs():
    assert len(_adapter(FEED).fetch()) == 4  # duplicate id 1 removed


def test_job_fields():
    job = _adapter(FEED).fetch()[0]

    assert job.title == "IT-Systemadministrator (m/w/d)"
    assert job.company == "U.S. Air Force – Personalbüro Ramstein Air Base"
    assert job.location == "Kaiserslautern"
    assert job.date_posted is not None and job.date_posted.utcoffset() is not None
    assert job.description == "Vollzeit\nAufgaben: Netzwerke"
    assert job.job_types == ["FULL_TIME"]


def test_warns_when_format_changes():
    adapter = _adapter({"numberOfItems": 76, "somethingNew": []})

    assert adapter.fetch() == []
    assert adapter.warnings and "76 jobs but none were readable" in adapter.warnings[0]


def test_short_keywords_match_whole_words_only():
    assert matches_keyword("IT-Support (m/w/d)", ["IT"])
    assert not matches_keyword("Mitarbeiter Verwaltung", ["IT"])
    assert matches_keyword("Elektroingenieur", ["Ingenieur"])