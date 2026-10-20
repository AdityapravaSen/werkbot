"""Small text helpers shared by adapters."""

import re

from selectolax.lexbor import LexborHTMLParser

_BLOCK_TAGS = "p, div, br, li, ul, ol, h1, h2, h3, h4, h5, h6, tr"


def html_to_text(html: str | None) -> str:
    """Turn an HTML job description into readable plain text."""
    if not html:
        return ""
    # Line breaks in the HTML source are just spaces (pages wrap long paragraphs);
    # real line breaks come from the block elements below.
    html = re.sub(r"\s+", " ", html)
    tree = LexborHTMLParser(html)
    for tag in ("script", "style"):
        for node in tree.css(tag):
            node.decompose()
    # Put a newline after block elements so paragraphs and bullets don't run together
    for node in tree.css(_BLOCK_TAGS):
        node.insert_after("\n")
    text = tree.text(separator="")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)  # no spaces at the start or end of a line
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def contains_any(text: str, keywords: list[str]) -> bool:
    """Case-insensitive check whether any keyword appears in the text."""
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


def matches_keyword(text: str, keywords: list[str]) -> bool:
    """Like contains_any, but short keywords (<= 3 letters, e.g. "IT") must be whole words.

    So "IT" matches "IT-Support" but not "Mitarbeiter", while "Ingenieur"
    still matches inside "Elektroingenieur".
    """
    for k in keywords:
        if len(k) <= 3:
            if re.search(rf"(?<![A-Za-zÄÖÜäöüß]){re.escape(k)}(?![A-Za-zÄÖÜäöüß])", text, re.I):
                return True
        elif k.lower() in text.lower():
            return True
    return False
