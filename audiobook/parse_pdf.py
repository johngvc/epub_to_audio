"""Stage 1 — PDF parser. Extracts Markdown with pymupdf4llm (Tier 1), cleans it
for TTS, converts to HTML, and emits the same on-disk artifacts as the EPUB
parser: work/chapters/raw/NN_slug.json + book_full_text.md.

Tier 2 (marker-pdf) is deferred to a future release; --parser marker errors and
--parser auto falls back to Tier 1 with a logged warning.
"""
from __future__ import annotations

import re
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import fitz  # type: ignore[import-untyped]
import markdown as md_lib  # type: ignore[import-untyped]
import pymupdf4llm  # type: ignore[import-untyped]
from bs4 import BeautifulSoup

from audiobook.models import ChapterRaw
from audiobook.parse_common import detect_features, likely_skip, strip_for_full_text
from audiobook.pdf_cleanup import FootnotePolicy, clean_pdf_markdown
from audiobook.utils.slugify import slugify

ParserChoice = Literal["auto", "pymupdf", "marker"]

# A page is considered to lack a usable text layer below this many characters.
_SCANNED_MIN_TOTAL_CHARS = 100
_SCANNED_MIN_CHARS_PER_PAGE = 50

# LaTeX-ish math markers pymupdf might pass through (rare without marker).
_MATH_RE = re.compile(r"\$\$|\\\(|\\\[")


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


def _heading_level_to_use(md_text: str, override: int | None) -> int | None:
    """Pick the heading level that delimits chapters. Explicit override wins;
    otherwise prefer H1, fall back to H2, else None (no headings)."""
    if override is not None:
        return override
    if re.search(r"^# +\S", md_text, re.M):
        return 1
    if re.search(r"^## +\S", md_text, re.M):
        return 2
    return None


def _split_sections(md_text: str, level: int) -> list[tuple[str, str]]:
    """Split Markdown into (title, body_markdown) at headings of exactly `level`.
    Content before the first heading becomes a "Front Matter" section."""
    heading_re = re.compile(rf"^#{{{level}}}(?!#)\s+(.+?)\s*#*$")
    sections: list[tuple[str, str]] = []
    current_title = "Front Matter"
    current_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((current_title, body))

    for line in md_text.splitlines():
        m = heading_re.match(line)
        if m:
            flush()
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    return sections


def _markdown_to_html(md_text: str) -> str:
    """Convert cleaned Markdown to HTML. `tables`/`fenced_code` make tables and
    code blocks emit <table> / <pre><code>, which feature-detection keys on."""
    return str(md_lib.markdown(md_text, extensions=["tables", "fenced_code"]))


_LONG_DOC_PAGES = 20
_LOW_TEXT_RATIO_FRACTION = 0.5   # page text below 30% of median
_LOW_TEXT_PAGE_THRESHOLD = 0.4   # fraction of pages that may be low-text before we warn


def _quality_warnings(md_text: str, page_texts: list[str], page_count: int) -> list[str]:
    """Return human-readable reasons Tier-1 extraction looks weak (empty list =
    looks fine). Used to warn the user; a future release feeds this into a
    marker (Tier 2) retry decision."""
    reasons: list[str] = []

    has_headings = re.search(r"^#{1,6} +\S", md_text, re.M) is not None
    if page_count > _LONG_DOC_PAGES and not has_headings:
        reasons.append(
            f"no headings detected in a {page_count}-page document (structure likely missed)"
        )

    lengths = [len(t.strip()) for t in page_texts]
    if len(lengths) >= 4:
        median = statistics.median(lengths)
        if median > 0:
            low = sum(1 for n in lengths if n < _LOW_TEXT_RATIO_FRACTION * median * 0.6)
            if low / len(lengths) > _LOW_TEXT_PAGE_THRESHOLD:
                reasons.append(
                    f"{low}/{len(lengths)} pages have text far below the median "
                    f"(image-heavy or failed extraction)"
                )

    return reasons


def parse_pdf(
    pdf_path: Path,
    out_dir: Path,
    *,
    parser: ParserChoice = "auto",
    footnote_policy: FootnotePolicy = "skip",
    chapter_level: int | None = None,
    book_title: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[ChapterRaw]:
    """Parse a PDF into per-chapter JSON files plus book_full_text.md, matching
    the EPUB parser's on-disk contract exactly."""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    raw_dir = out_dir / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if parser == "marker":
        raise MarkerNotAvailableError(
            "Tier 2 (marker-pdf) is not available in this build; it is deferred to "
            "a future release. Use --parser auto or --parser pymupdf."
        )

    doc, page_texts = _open_and_guard(pdf_path)
    page_count = doc.page_count
    doc.close()

    raw_md = pymupdf4llm.to_markdown(str(pdf_path))

    if parser in ("auto", "pymupdf"):
        reasons = _quality_warnings(raw_md, page_texts, page_count)
        if reasons and progress:
            progress(
                "low-quality extraction signals: "
                + "; ".join(reasons)
                + ". Tier 2 (marker) is deferred to a future release — using Tier 1 output."
            )

    cleaned = clean_pdf_markdown(raw_md, footnote_policy=footnote_policy)
    level = _heading_level_to_use(cleaned, chapter_level)

    if level is None:
        title = book_title or pdf_path.stem
        sections = [(title, cleaned)]
    else:
        sections = _split_sections(cleaned, level)

    chapters: list[ChapterRaw] = []
    full_text_sections: list[str] = []
    index = 0
    for title, body_md in sections:
        html = _markdown_to_html(body_md)
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        if likely_skip(title, text):
            continue

        has_code, has_math, has_tables = detect_features(soup)
        has_math = has_math or bool(_MATH_RE.search(body_md))

        chapter = ChapterRaw(
            index=index,
            title=title,
            source_spine_id=f"pdf:section-{index:02d}",
            html=str(soup),
            word_count_estimate=len(text.split()),
            has_code=has_code,
            has_math=has_math,
            has_tables=has_tables,
        )
        out_path = raw_dir / f"{index:02d}_{slugify(title)}.json"
        out_path.write_text(chapter.model_dump_json(indent=2) + "\n")
        chapters.append(chapter)

        full_text_sections.append(f"# {title}\n\n{strip_for_full_text(str(soup))}")
        index += 1

    (out_dir / "book_full_text.md").write_text("\n\n".join(full_text_sections) + "\n")
    return chapters
