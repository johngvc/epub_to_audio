"""Deterministic, audio-oriented cleanup applied to PDF-extracted Markdown
before it is converted to HTML and handed to the rest of the pipeline.

Every artifact left here becomes audible, so the rules are conservative:
when a transform is ambiguous, prefer leaving the text alone.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from functools import lru_cache

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


# A word split across a line break: letters, a hyphen, end-of-line, then letters.
_HYPHEN_BREAK_RE = re.compile(r"([A-Za-z]+)-\n([A-Za-z]+)")


@lru_cache(maxsize=1)
def _default_speller() -> Callable[[str], bool]:
    """Lazily build a pure-Python spell checker (no system libs). Loaded once."""
    from spellchecker import SpellChecker

    spell = SpellChecker()

    def is_word(word: str) -> bool:
        return bool(spell.known([word.lower()]))

    return is_word


def dehyphenate(text: str, *, is_word: Callable[[str], bool] | None = None) -> str:
    """Re-join words broken by a hyphen at a line break.

    `re-\\ntrieval` -> `retrieval` when the joined form is a real word; otherwise
    collapse the newline but keep the hyphen so genuine compounds survive.
    `is_word` is injectable for testing; defaults to a bundled dictionary.
    """
    checker = is_word if is_word is not None else _default_speller()

    def _join(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        joined = left + right
        if checker(joined):
            return joined
        return f"{left}-{right}"

    return _HYPHEN_BREAK_RE.sub(_join, text)


_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
_HEADER_REPEAT_THRESHOLD = 3
_HEADER_MAX_LEN = 60


def strip_page_artifacts(text: str) -> str:
    """Remove orphan page-number lines and repeated short running headers/footers
    that survived extraction.

    - Orphan page numbers: a line that is nothing but 1–4 digits.
    - Running headers/footers: a short (<60 char), non-heading line that recurs
      `_HEADER_REPEAT_THRESHOLD`+ times is treated as boilerplate and dropped
      at every occurrence. Markdown headings (`#…`) are never dropped.
    """
    lines = text.splitlines()

    counts: Counter[str] = Counter(
        ln.strip()
        for ln in lines
        if ln.strip()
        and not ln.lstrip().startswith("#")
        and len(ln.strip()) <= _HEADER_MAX_LEN
    )
    repeated = {s for s, n in counts.items() if n >= _HEADER_REPEAT_THRESHOLD}

    kept: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if _PAGE_NUMBER_RE.match(ln):
            continue
        if stripped in repeated:
            continue
        kept.append(ln)
    return "\n".join(kept)
