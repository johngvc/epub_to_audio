from __future__ import annotations

from pathlib import Path

import pytest

from audiobook.models import ChapterRaw
from audiobook.parse_pdf import (
    EncryptedPdfError,
    ScannedPdfError,
    parse_pdf,
)


def test_parse_pdf_rejects_encrypted(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "encrypted.pdf"
    with pytest.raises(EncryptedPdfError):
        parse_pdf(src, scratch)


def test_parse_pdf_rejects_scanned(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "scanned.pdf"
    with pytest.raises(ScannedPdfError) as exc:
        parse_pdf(src, scratch)
    assert "scanned" in str(exc.value).lower()
    assert "ocr" in str(exc.value).lower()


def test_parse_pdf_produces_chapter_files(repo_root: Path, scratch: Path) -> None:
    chapters = parse_pdf(repo_root / "tests" / "fixtures" / "tiny.pdf", scratch)
    raw_dir = scratch / "chapters" / "raw"
    files = sorted(raw_dir.glob("*.json"))
    assert len(files) == 2          # two H1 chapters
    assert len(chapters) == 2
    for f in files:
        ChapterRaw.model_validate_json(f.read_text())


def test_parse_pdf_splits_on_headings_and_titles_them(repo_root: Path, scratch: Path) -> None:
    chapters = parse_pdf(repo_root / "tests" / "fixtures" / "tiny.pdf", scratch)
    titles = [c.title for c in chapters]
    assert titles == ["Chapter One", "Chapter Two"]


def test_parse_pdf_dehyphenates_into_html(repo_root: Path, scratch: Path) -> None:
    chapters = parse_pdf(repo_root / "tests" / "fixtures" / "tiny.pdf", scratch)
    assert "retrieval" in chapters[0].html
    assert "re-" not in chapters[0].html


def test_parse_pdf_emits_book_full_text(repo_root: Path, scratch: Path) -> None:
    parse_pdf(repo_root / "tests" / "fixtures" / "tiny.pdf", scratch)
    md = (scratch / "book_full_text.md").read_text()
    assert "# Chapter One" in md
    assert "# Chapter Two" in md


def test_parse_pdf_marker_choice_raises(repo_root: Path, scratch: Path) -> None:
    from audiobook.parse_pdf import MarkerNotAvailableError

    with pytest.raises(MarkerNotAvailableError):
        parse_pdf(repo_root / "tests" / "fixtures" / "tiny.pdf", scratch, parser="marker")
