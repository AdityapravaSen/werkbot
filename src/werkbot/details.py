"""Job page details: fetch the description (and the company, if unknown) from job pages.

Most list pages have no description, but the language filter and fit scoring need one.
Job pages usually carry it in a machine-readable form, so one generic reader covers
most sites, tried in this order:

    1. JSON-LD JobPosting  (<script type="application/ld+json">; made for Google Jobs)
    2. microdata           (itemprop="description", e.g. Fraunhofer)
    3. known page parts    (CSS selectors for a few sites, e.g. LinkedIn)
    4. the page's main text (trafilatura)
"""

import html as html_lib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
import trafilatura
from loguru import logger
from selectolax.lexbor import LexborHTMLParser

from werkbot.config import Settings
from werkbot.dedup.normalize import UNKNOWN_COMPANIES, _words, fold
from werkbot.filters.config import DetailRules
from werkbot.text import html_to_text

# Page parts that hold the description on sites without structured data.
SELECTORS = {
    "linkedin.com": [".show-more-less-html__markup", ".description__text"],
    "foorilla.com": [".job-description", "#job-description", "article"],
    "wearedevelopers.com": ["[data-testid='job-description']", ".job-description", "article"],
    "jobs.dfki.de": [".job-description", ".vacancy-description", "#job-details", "main"],
}


@dataclass
class Details:
    description: str = ""
    company: str | None = None
    date_posted: datetime | None = None
    remote: bool | None = None  # JSON-LD jobLocationType TELECOMMUTE
    employment_types: list[str] = field(default_factory=list)
    method: str = ""  # jsonld | microdata | selector | text | none


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    parts = host.removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def extract_details(page: str, url: str = "", title: str = "") -> Details:
    """Read the description from a job page. With `title`, the page's main text (the last
    resort) is only used if it mentions the job's title; otherwise it is probably a list of
    other jobs or a cookie banner."""
    tree = LexborHTMLParser(page)
    found = _from_json_ld(tree)
    if found.description:
        return found
    microdata = tree.css_first("[itemprop='description']")
    if microdata is not None:
        text = html_to_text(microdata.html)
        if text:
            org = tree.css_first("[itemprop='hiringOrganization']")
            company = _clean_company(org.text(strip=True) if org else None)
            return Details(text, company or found.company, method="microdata")
    host = urlsplit(url).hostname or ""
    for site, selectors in SELECTORS.items():
        if host.endswith(site):
            for selector in selectors:
                node = tree.css_first(selector)
                text = html_to_text(node.html) if node is not None else ""
                if len(text) >= 200:
                    return Details(text, found.company, method="selector")
    text = (trafilatura.extract(page, include_comments=False, include_tables=True) or "").strip()
    if text and title and not _mentions_title(text, title):
        return Details("", found.company, method="none")
    return Details(text, found.company, method="text" if text else "none")


def _mentions_title(text: str, title: str) -> bool:
    """At least half of the title's words (without "Werkstudent", gender tags ...) appear."""
    from werkbot.dedup.normalize import normalize_title

    words = [w for w in normalize_title(title).split() if w not in {"student", "intern"}]
    if not words:
        return True
    body = normalize_title(text[:20000])
    found = sum(1 for w in words if f" {w} " in f" {body} ")
    return found * 2 >= len(words)


def _from_json_ld(tree: LexborHTMLParser) -> Details:
    for script in tree.css("script[type='application/ld+json']"):
        try:
            data = json.loads(script.text(strip=False) or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _items(data):
            types = item.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" not in types:
                continue
            raw = str(item.get("description") or "")
            if "&lt;" in raw:
                raw = html_lib.unescape(raw)
            org = item.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else org
            employment = item.get("employmentType") or []
            return Details(
                description=html_to_text(raw),
                company=_clean_company(company if isinstance(company, str) else None),
                date_posted=_date(item.get("datePosted")),
                remote=str(item.get("jobLocationType") or "").upper() == "TELECOMMUTE" or None,
                employment_types=employment if isinstance(employment, list) else [employment],
                method="jsonld",
            )
    return Details()


def _items(data) -> list[dict]:
    if isinstance(data, list):
        return [i for d in data for i in _items(d)]
    if isinstance(data, dict):
        return [data, *_items(data.get("@graph", []))]
    return []


def _clean_company(name: str | None) -> str | None:
    if not name or _words(fold(name)) in UNKNOWN_COMPANIES:
        return None
    return name.strip()


def _date(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------- fetching ----------


@dataclass
class FetchStats:
    tried: int = 0
    ok: int = 0
    no_description: int = 0
    failed: int = 0
    companies_found: int = 0
    merged: int = 0
    skipped_sites: set[str] = field(default_factory=set)


def jobs_needing_details(conn, rules: DetailRules, job_ids: list[int], now: datetime) -> list:
    """Jobs (from `job_ids`) with no real description that were not tried recently."""
    if not job_ids:
        return []
    retry_before = (now - timedelta(days=rules.retry_failed_after_days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    marks = ",".join("?" * len(job_ids))
    rows = conn.execute(
        f"""SELECT id, main_url, title, company, company_norm FROM jobs
            WHERE id IN ({marks}) AND length(description) < ?
              AND (details_status IS NULL
                   OR (details_status != 'ok' AND details_at < ?))
            ORDER BY first_seen_at DESC, id""",
        (*job_ids, rules.min_description_chars, retry_before),
    ).fetchall()
    skip = {site for site, limit in rules.per_site.items() if limit.skip}
    return [r for r in rows if site_of(r["main_url"]) not in skip]


def fetch_details(
    conn,
    rows: list,
    rules: DetailRules,
    settings: Settings,
    aliases=(),
    transport: httpx.BaseTransport | None = None,
    sleep=time.sleep,
) -> FetchStats:
    """Fetch the job page of each row and store what was found. Polite: one site at a
    time is paused between requests, every site has a per-run maximum, and a site that
    rate-limits us is skipped for the rest of the run."""
    from werkbot.dedup.store import adopt_company, timestamp

    stats = FetchStats()
    per_site_count: dict[str, int] = {}
    last_request: dict[str, float] = {}
    client = httpx.Client(
        timeout=settings.request_timeout_s,
        headers={"User-Agent": settings.user_agent, "Accept-Language": "en,de;q=0.8"},
        follow_redirects=True,
        transport=transport,
    )
    with client:
        for row in rows:
            if stats.tried >= rules.max_per_run:
                break
            url = row["main_url"]
            site = site_of(url)
            limit = rules.per_site.get(site)
            delay = limit.delay_s if limit and limit.delay_s is not None else rules.delay_s
            cap = limit.max_per_run if limit and limit.max_per_run is not None else None
            if limit and limit.skip:
                continue
            if site in stats.skipped_sites or (
                cap is not None and per_site_count.get(site, 0) >= cap
            ):
                continue
            wait = last_request.get(site, 0) + delay - time.monotonic()
            if wait > 0:
                sleep(wait)
            per_site_count[site] = per_site_count.get(site, 0) + 1
            stats.tried += 1
            last_request[site] = time.monotonic()
            now = timestamp(datetime.now(UTC))
            try:
                response = client.get(url)
                if response.status_code == 429:
                    stats.skipped_sites.add(site)
                    logger.warning("details: {} rate-limits us; skipping it for this run", site)
                    raise httpx.HTTPStatusError("429", request=response.request, response=response)
                response.raise_for_status()
                details = extract_details(response.text, str(response.url), row["title"])
            except (httpx.HTTPError, ValueError) as exc:
                stats.failed += 1
                logger.debug("details failed for {}: {}", url, exc)
                conn.execute(
                    "UPDATE jobs SET details_status = 'failed', details_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                conn.commit()
                continue

            status = (
                "ok"
                if len(details.description) >= rules.min_description_chars
                else "no_description"
            )
            if status == "ok":
                stats.ok += 1
            else:
                stats.no_description += 1
            logger.debug(
                "details {} via {}: {} chars", url, details.method, len(details.description)
            )
            conn.execute(
                """UPDATE jobs SET details_status = ?, details_at = ?,
                       description = CASE WHEN length(?) > length(description) THEN ?
                                          ELSE description END
                   WHERE id = ?""",
                (status, now, details.description, details.description, row["id"]),
            )
            _update_source_record(conn, row["id"], url, details)
            if details.company and not row["company_norm"]:
                stats.companies_found += 1
                if adopt_company(conn, row["id"], details.company, aliases):
                    stats.merged += 1
            conn.commit()
    return stats


def _update_source_record(conn, job_id: int, url: str, details: Details) -> None:
    """Keep the fetched description in that board's own record too (job_sources.data)."""
    row = conn.execute(
        "SELECT id, data FROM job_sources WHERE job_id = ? AND url = ?", (job_id, url)
    ).fetchone()
    if row is None:
        return
    data = json.loads(row["data"] or "{}")
    if len(details.description) > len(data.get("description") or ""):
        data["description"] = details.description
    if details.company and str(data.get("company", "")).strip().lower() in ("", "unknown"):
        data["company"] = details.company
    conn.execute("UPDATE job_sources SET data = ? WHERE id = ?", (json.dumps(data), row["id"]))
