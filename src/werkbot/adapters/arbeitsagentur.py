"""Bundesagentur für Arbeit (Jobsuche): public REST API (Group A).

- Search:  GET {base}/pc/v6/jobs?was=...&wo=...&umkreis=...&page=N&size=100
- Details: GET {base}/pc/v4/jobdetails/{base64(referenznummer)}   (has the description)
- Header:  X-API-Key: jobboerse-jobsuche   (public client id, not a secret)

Response format (checked live, Oct 2026; the GitHub spec at bundesAPI/jobsuche-api is outdated):
    {"ergebnisliste": [{"referenznummer", "stellenangebotsTitel", "firma",
                        "stellenlokationen": [{"adresse": {"ort", ...}}],
                        "datumErsteVeroeffentlichung", "homeofficemoeglich", ...}],
     "maxErgebnisse", "page", "size", "facetten", "woOutput"}
Old field names are still read as a fallback.

The search result has no description, so we fetch details per job (capped by
`max_details`). If details get rate-limited, we keep the jobs without descriptions.
"""

import base64
from datetime import UTC, datetime

import httpx
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job
from werkbot.text import html_to_text

API_KEY = "jobboerse-jobsuche"
SEARCH_PATH = "/pc/v6/jobs"
DETAILS_PATH = "/pc/v4/jobdetails/"
JOB_PAGE = "https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"

# Parameters passed straight to the search API (anything else in params is ours)
SEARCH_PARAMS = {
    "was",
    "wo",
    "umkreis",
    "veroeffentlichtseit",
    "berufsfeld",
    "angebotsart",
    "arbeitszeit",
    "befristung",
    "zeitarbeit",
}


@register
class ArbeitsagenturAdapter(Adapter):
    name = "arbeitsagentur"

    def client(self) -> httpx.Client:
        client = super().client()
        client.headers["X-API-Key"] = API_KEY
        return client

    def fetch(self) -> list[Job]:
        params = self.source.params
        base = self.source.url.rstrip("/")
        size = int(params.get("size", 100))
        max_pages = int(params.get("max_pages", 20))
        fetch_details = bool(params.get("fetch_details", True))
        max_details = int(params.get("max_details", 200))
        query = {k: v for k, v in params.items() if k in SEARCH_PARAMS}

        jobs: dict[str, Job] = {}  # keyed by referenznummer, so the same job is never added twice
        with self.client() as client:
            for page in range(1, max_pages + 1):
                try:
                    payload = self.get(
                        client, base + SEARCH_PATH, **query, page=page, size=size
                    ).json()
                except httpx.HTTPError as exc:
                    self._warn(f"search stopped at page {page} ({exc.__class__.__name__})")
                    break

                # The API renamed this list in 2026 (stellenangebote -> ergebnisliste)
                items = payload.get("ergebnisliste") or payload.get("stellenangebote") or []
                for item in items:
                    job = _to_job(item)
                    if job and job.source_job_id not in jobs:
                        jobs[job.source_job_id] = job

                total = _to_int(payload.get("maxErgebnisse"))
                if page == 1 and not items and total:
                    # Results exist but we found no list: the response format changed again.
                    self._warn(
                        f"API reports {total} jobs but none were readable; response keys: "
                        f"{sorted(payload)}. The adapter needs updating."
                    )
                logger.info(
                    "arbeitsagentur page {}: {} items, {} of {} total",
                    page,
                    len(items),
                    len(jobs),
                    total,
                )
                if not items or (total is not None and page * size >= total):
                    break
                self.pause()

            if fetch_details:
                self._add_details(client, base, list(jobs.values())[:max_details])
                if len(jobs) > max_details:
                    self._warn(f"descriptions fetched for the first {max_details} jobs only")

        return list(jobs.values())

    def _add_details(self, client: httpx.Client, base: str, jobs: list[Job]) -> None:
        for i, job in enumerate(jobs, start=1):
            encoded = base64.b64encode(job.source_job_id.encode()).decode()
            try:
                details = self.get(client, base + DETAILS_PATH + encoded).json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    self._warn(f"details rate-limited after {i - 1} jobs; rest have no description")
                    return
                logger.debug("arbeitsagentur details failed for {}: {}", job.source_job_id, exc)
                continue
            except httpx.HTTPError as exc:
                logger.debug("arbeitsagentur details failed for {}: {}", job.source_job_id, exc)
                continue

            text = details.get("stellenangebotsBeschreibung") or details.get("stellenbeschreibung")
            job.description = html_to_text(text)
            if details.get("homeofficemoeglich"):
                job.remote = True
            models = " ".join(details.get("arbeitszeitmodelle") or [])  # older format
            if "HEIM" in models or "TELE" in models:
                job.remote = True
            self.pause()

    def _warn(self, message: str) -> None:
        logger.warning("arbeitsagentur: {}", message)
        self.warnings.append(message)


def _to_job(item: dict) -> Job | None:
    """Map one search result to a Job. New field names first, old ones as fallback."""
    refnr = item.get("referenznummer") or item.get("refnr")
    title = (
        item.get("stellenangebotsTitel")
        or item.get("titel")
        or item.get("hauptberuf")
        or item.get("beruf")
    )
    if not refnr or not title:
        return None
    published = (item.get("veroeffentlichungszeitraum") or {}).get("von")
    return Job(
        title=title.strip(),
        company=(item.get("firma") or item.get("arbeitgeber") or "Unknown").strip(),
        url=JOB_PAGE.format(refnr=refnr),
        source="arbeitsagentur",
        source_job_id=refnr,
        location=_location(item),
        remote=bool(item.get("homeofficemoeglich")),
        date_posted=_parse_date(
            item.get("datumErsteVeroeffentlichung")
            or published
            or item.get("aktuelleVeroeffentlichungsdatum")
        ),
    )


def _location(item: dict) -> str | None:
    places = item.get("stellenlokationen") or []
    if places:
        return ((places[0] or {}).get("adresse") or {}).get("ort")
    return (item.get("arbeitsort") or {}).get("ort")  # older format


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).replace(tzinfo=UTC)
    except ValueError:
        return None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None