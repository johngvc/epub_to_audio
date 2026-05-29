"""Stage 1 — PDF parser. Extracts Markdown with pymupdf4llm (Tier 1), cleans it
for TTS, converts to HTML, and emits the same on-disk artifacts as the EPUB
parser: work/chapters/raw/NN_slug.json + book_full_text.md.

Tier 2 (marker-pdf) is deferred to a future release; --parser marker errors and
--parser auto falls back to Tier 1 with a logged warning.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import fitz  # type: ignore[import-untyped]

ParserChoice = Literal["auto", "pymupdf", "marker"]

# A page is considered to lack a usable text layer below this many characters.
_SCANNED_MIN_TOTAL_CHARS = 100
_SCANNED_MIN_CHARS_PER_PAGE = 50


class PdfParseError(Exception):
    """Base class for fatal PDF ingestion problems."""


class EncryptedPdfError(PdfParseError):
    """The PDF is password-protected / encrypted."""


class ScannedPdfError(PdfParseError):
    """The PDF has no usable text layer (likely scanned images)."""


class MarkerNotAvailableError(PdfParseError):
    """Tier 2 (marker-pdf) was requested but is not available in this build."""


def _open_and_guard(pdf_path: Path) -> tuple[fitz.Document, list[str]]:
    """Open the PDF, fail loudly on encryption or a missing text layer, and
    return (document, per-page extracted text)."""
    doc = fitz.open(str(pdf_path))
    if doc.is_encrypted or doc.needs_pass:
        doc.close()
        raise EncryptedPdfError(
            f"{pdf_path.name} is encrypted/password-protected. Decrypt it first "
            f"(e.g. with qpdf) and re-run; encrypted PDFs are not supported."
        )

    page_texts = [page.get_text() for page in doc]
    total = sum(len(t.strip()) for t in page_texts)
    page_count = max(doc.page_count, 1)
    if total < _SCANNED_MIN_TOTAL_CHARS or total / page_count < _SCANNED_MIN_CHARS_PER_PAGE:
        doc.close()
        raise ScannedPdfError(
            f"{pdf_path.name} looks like a scanned PDF (little or no extractable "
            f"text). OCR support is not yet implemented; this is a separate ticket. "
            f"Refusing to produce empty/garbage audio."
        )
    return doc, page_texts


def parse_pdf(
    pdf_path: Path,
    out_dir: Path,
    *,
    parser: ParserChoice = "auto",
    footnote_policy: str = "skip",
    chapter_level: int | None = None,
    book_title: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[object]:
    doc, page_texts = _open_and_guard(Path(pdf_path))
    doc.close()
    raise NotImplementedError("completed in Task 9")
