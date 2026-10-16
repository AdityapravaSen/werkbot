"""Page watcher (Group D): alert when a page's main text changes.

For pages that don't list jobs, e.g. a research group's "HiWi positions" page.
Each run:
  1. download the page and extract its main text (menus/footers removed by trafilatura),
  2. compare it with the snapshot saved last time in output/watch/<source>.txt,
  3. if it changed, return ONE item titled "[Page changed] ..." whose description
     lists the added lines, so it shows up next to the real jobs.
The first run only saves a snapshot (nothing to compare yet).
"""

import difflib
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import trafilatura
from loguru import logger

from werkbot.adapters.base import Adapter, register
from werkbot.models import Job
from werkbot.text import html_to_text


@register
class PageWatchAdapter(Adapter):
    name = "page_watch"

    def fetch(self) -> list[Job]:
        try:
            with self.client() as client:
                html = self.get(client, self.source.url).text
        except httpx.HTTPError as exc:
            self.warn(f"page could not be loaded ({exc.__class__.__name__})")
            return []

        text = main_text(html)
        if not text:
            self.warn("no readable text on the page; it may now need JavaScript")
            return []

        snapshot = self.snapshot_path()
        old = snapshot.read_text(encoding="utf-8") if snapshot.exists() else None
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(text, encoding="utf-8")

        if old is None:
            logger.info("{}: first snapshot saved ({} characters)", self.source.name, len(text))
            return []
        if _digest(old) == _digest(text):
            logger.info("{}: unchanged", self.source.name)
            return []

        added = [
            line[1:].strip()
            for line in difflib.unified_diff(old.splitlines(), text.splitlines(), lineterm="")
            if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
        ]
        logger.info("{}: CHANGED ({} new lines)", self.source.name, len(added))
        label = self.source.params.get("label") or self.source.name
        return [
            Job(
                title=f"[Page changed] {label}",
                company=self.source.params.get("organization") or "Watched page",
                url=self.source.url,
                source=self.source.name,
                source_job_id=f"{self.source.name}-{_digest(text)[:12]}",
                description="New or changed lines:\n" + "\n".join(added[:50]),
                date_posted=datetime.now(UTC),
                job_types=["page-change"],
            )
        ]

    def snapshot_path(self) -> Path:
        return self.settings.output_dir / "watch" / f"{self.source.name}.txt"


def main_text(html: str) -> str:
    """Main content of the page, without navigation, sidebars and footer."""
    text = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    if not text.strip():
        text = html_to_text(html)  # fallback for pages trafilatura can't handle
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
