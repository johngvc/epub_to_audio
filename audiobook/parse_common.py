"""Helpers shared by the EPUB and PDF ingesters (Stage 1).

Both ingesters converge on an HTML string per chapter, then derive the same
artifacts from it. Keeping these here keeps the two parsers behaviorally
identical and DRY.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

SKIP_PATTERNS = (
    re.compile(r"copyright", re.I),
    re.compile(r"acknowledg", re.I),
    re.compile(r"dedication", re.I),
    re.compile(r"^index$", re.I),
    re.compile(r"bibliograph", re.I),
)


def likely_skip(title: str, text: str) -> bool:
    """True for front/back matter and trivially short sections."""
    if any(p.search(title) for p in SKIP_PATTERNS):
        return True
    return len(text.split()) < 10


def detect_features(soup: BeautifulSoup) -> tuple[bool, bool, bool]:
    """Return (has_code, has_math, has_tables) from a parsed HTML soup."""
    has_code = soup.find(["pre", "code"]) is not None
    has_math = bool(soup.find("math")) or bool(soup.find(class_="math"))
    has_tables = soup.find("table") is not None
    return has_code, has_math, has_tables


def strip_for_full_text(html: str) -> str:
    """Convert chapter HTML to the plaintext-ish markdown used as whole-book
    context by the adaptation step (code blocks collapsed to a token)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["pre", "code"]):
        if isinstance(tag, Tag):
            tag.replace_with("[code block]")
    for tag in soup.find_all("script") + soup.find_all("style"):
        if isinstance(tag, Tag):
            tag.decompose()
    md: str = markdownify(str(soup), heading_style="ATX")
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md
