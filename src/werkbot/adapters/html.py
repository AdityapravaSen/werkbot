"""Small helpers shared by the HTML / embedded-data adapters (Group B)."""

import json
import re
from collections.abc import Iterator

from selectolax.lexbor import LexborHTMLParser, LexborNode


def parse_html(html: str) -> LexborHTMLParser:
    return LexborHTMLParser(html)


def text_of(node: LexborNode | None) -> str:
    """Visible text of a node, whitespace collapsed. Empty string if the node is missing."""
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(separator=" ")).strip()


def next_data(html: str) -> dict | None:
    """The JSON that Next.js (pages router) embeds in <script id="__NEXT_DATA__">."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def next_flight_payload(html: str) -> str:
    """The data Next.js (app router) streams via self.__next_f.push([1, "..."]), joined."""
    parts = []
    for raw in re.findall(r"self\.__next_f\.push\((\[.*?\])\)</script>", html, re.S):
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if chunk and isinstance(chunk[-1], str):
            parts.append(chunk[-1])
    return "".join(parts)


def json_objects(text: str, start_pattern: str) -> Iterator[dict]:
    """Decode every JSON object in `text` that starts with `start_pattern` (a regex)."""
    decoder = json.JSONDecoder()
    for m in re.finditer(start_pattern, text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj
