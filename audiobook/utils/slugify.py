"""ASCII slug helper used by parse and assemble."""
from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """Return a lowercase ASCII slug suitable for filenames.

    Empty / whitespace-only inputs yield ``"untitled"`` so downstream paths
    never collide on an empty filename.
    """
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned or "untitled"
