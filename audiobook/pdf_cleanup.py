"""Deterministic, audio-oriented cleanup applied to PDF-extracted Markdown
before it is converted to HTML and handed to the rest of the pipeline.

Every artifact left here becomes audible, so the rules are conservative:
when a transform is ambiguous, prefer leaving the text alone.
"""
from __future__ import annotations

import re

_PUNCT_MAP = {
    "“": '"',
    "”": '"',  # curly double quotes
    "‘": "'",
    "’": "'",  # curly single quotes / apostrophe
    "–": "-",
    "—": "-",  # en-dash, em-dash
    "…": "...",  # horizontal ellipsis
    " ": " ",  # non-breaking space
}
_PUNCT_RE = re.compile("|".join(re.escape(k) for k in _PUNCT_MAP))


def normalize_punctuation(text: str) -> str:
    """Map TTS-ambiguous Unicode punctuation to plain ASCII equivalents."""
    return _PUNCT_RE.sub(lambda m: _PUNCT_MAP[m.group(0)], text)


def collapse_whitespace(text: str) -> str:
    """Collapse intra-line whitespace runs, trim line ends, and reduce any
    run of blank lines to a single blank line. Trailing/leading blank lines
    are stripped."""
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines()]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
