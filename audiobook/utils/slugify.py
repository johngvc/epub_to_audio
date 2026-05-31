"""ASCII slug helper used by parse and assemble."""
from __future__ import annotations

import re
import unicodedata


def slugify(value: str, *, max_length: int | None = None) -> str:
    """Return a lowercase ASCII slug suitable for filenames.

    Empty / whitespace-only inputs yield ``"untitled"`` so downstream paths
    never collide on an empty filename. When ``max_length`` is given, the slug
    is truncated at a hyphen boundary so a pathological title (e.g. a flattened
    code line from a PDF) cannot produce a filename that exceeds the OS limit.
    """
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if max_length is not None and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rsplit("-", 1)[0].strip("-") or cleaned[:max_length]
    return cleaned or "untitled"
