"""Record the hidden data requests a JavaScript-heavy job page makes.

Opens each URL in headless Chromium (Playwright), waits for the page to settle,
optionally clicks through filter buttons, and saves to output/probe/network/<name>/:
  - requests.txt       every XHR/fetch request: method, status, type, size, URL (+ POST body)
  - NN_<host>.json/... the response bodies of those requests
  - rendered.html      the page HTML after JavaScript ran

Usage (once):  uv sync --group dev --extra browser
               uv run playwright install chromium
Then:          uv run python scripts/probe_network.py
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

PAGES = {
    "foorilla": {
        "url": "https://foorilla.com/hiring/",
        # Filter labels to click, in order (best effort; skipped if not found)
        "clicks": ["Part-time", "English", "Germany"],
    },
    "dfki": {
        "url": "https://jobs.dfki.de/ausschreibungen.html#filter=p_web_site_id%3D6635"
        "%26p_published_to%3DWWW%26p_language%3DDEFAULT%26p_direct%3DY%26p_format%3DMOBILE"
        "%26p_include_exclude_from_list%3DN%26p_category_code_arr%3D23427-662551%26p_search%3D",
        "clicks": [],
    },
}
OUT = Path("output/probe/network")
SKIP = re.compile(
    r"google|doubleclick|facebook|hotjar|plausible|matomo|analytics|sentry|cookie|consent", re.I
)


def probe(browser, name: str, url: str, clicks: list[str]) -> None:
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    counter = {"n": 0}

    page = browser.new_page(locale="en-US")

    def on_response(response):
        request = response.request
        if request.resource_type not in ("xhr", "fetch", "document") or SKIP.search(request.url):
            return
        try:
            body = response.body()
        except Exception:
            body = b""
        ctype = response.headers.get("content-type", "")
        counter["n"] += 1
        n = counter["n"]
        line = (
            f"{n:02d} {request.method:4} {response.status} {request.resource_type:8} "
            f"{ctype[:30]:30} {len(body):>8} B  {request.url}"
        )
        if request.post_data:
            line += f"\n      POST body: {request.post_data[:500]}"
        lines.append(line)
        ext = "json" if "json" in ctype else "html" if "html" in ctype else "txt"
        host = urlparse(request.url).netloc.replace(":", "_")
        (out / f"{n:02d}_{host}.{ext}").write_bytes(body[:2_000_000])

    page.on("response", on_response)
    print(f"\n=== {name}: {url}")
    page.goto(url, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(3000)

    for label in clicks:
        try:
            target = page.get_by_text(label, exact=True).first
            target.click(timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_timeout(1500)
            lines.append(f"-- clicked '{label}' (page URL now: {page.url})")
        except Exception as exc:
            lines.append(f"-- could not click '{label}': {type(exc).__name__}")

    (out / "rendered.html").write_text(page.content(), encoding="utf-8")
    (out / "requests.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    page.close()


def main() -> None:
    only = set(sys.argv[1:])
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, cfg in PAGES.items():
            if only and name not in only:
                continue
            try:
                probe(browser, name, cfg["url"], cfg["clicks"])
            except Exception as exc:
                print(f"{name}: ERROR {type(exc).__name__}: {exc}")
        browser.close()
    print(f"\nSaved to {OUT.resolve()}")


if __name__ == "__main__":
    main()
