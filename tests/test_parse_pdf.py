from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from audiobook.cli import app
from audiobook.models import ChapterRaw
from audiobook.parse_pdf import (
    EncryptedPdfError,
    ScannedPdfError,
    _quality_warnings,
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
    assert len(files) == 2          # two heading-delimited chapters
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


def test_parse_pdf_marker_choice_raises_before_opening(repo_root: Path, scratch: Path) -> None:
    from audiobook.parse_pdf import MarkerNotAvailableError

    # Using the encrypted fixture proves the marker guard fires BEFORE the PDF is
    # opened: we get MarkerNotAvailableError, not EncryptedPdfError.
    with pytest.raises(MarkerNotAvailableError):
        parse_pdf(repo_root / "tests" / "fixtures" / "encrypted.pdf", scratch, parser="marker")


def test_parse_pdf_surfaces_quality_warning_via_progress(
    repo_root: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import audiobook.parse_pdf as pdf_mod

    monkeypatch.setattr(pdf_mod, "_quality_warnings", lambda *a, **k: ["forced reason"])
    lines: list[str] = []
    parse_pdf(
        repo_root / "tests" / "fixtures" / "tiny.pdf",
        scratch,
        progress=lines.append,
    )
    assert any("forced reason" in ln and "Tier 2" in ln for ln in lines)


def test_quality_warns_on_long_doc_without_headings() -> None:
    md = "Just flowing prose with no headings at all. " * 50
    reasons = _quality_warnings(md, page_texts=[md] * 25, page_count=25)
    assert any("heading" in r.lower() for r in reasons)


def test_quality_warns_on_many_low_text_pages() -> None:
    pages = ["full of text " * 40] * 5 + ["x"] * 5  # half the pages near-empty
    reasons = _quality_warnings("# H\n\nbody", page_texts=pages, page_count=10)
    assert any("low-text" in r.lower() or "below" in r.lower() for r in reasons)


def test_quality_silent_on_clean_short_doc() -> None:
    md = "# Chapter One\n\nA normal paragraph of several words goes here.\n"
    reasons = _quality_warnings(md, page_texts=[md], page_count=1)
    assert reasons == []


def test_parse_pdf_chapter_level_override_selects_level(repo_root: Path, scratch: Path) -> None:
    # tiny.pdf's headings are H2; an explicit chapter_level=2 override must split
    # on them exactly like the auto-fallback does.
    chapters = parse_pdf(
        repo_root / "tests" / "fixtures" / "tiny.pdf",
        scratch,
        chapter_level=2,
    )
    assert [c.title for c in chapters] == ["Chapter One", "Chapter Two"]


def test_parse_pdf_book_title_used_when_no_headings(
    repo_root: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import audiobook.parse_pdf as pdf_mod

    # Force headingless extraction so the single-section book_title path runs.
    # Body must exceed the likely_skip word floor (>=10 words) to be kept.
    body = "This is a paragraph of prose with no headings whatsoever in the entire document."
    monkeypatch.setattr(pdf_mod.pymupdf4llm, "to_markdown", lambda _p: body)
    chapters = parse_pdf(
        repo_root / "tests" / "fixtures" / "tiny.pdf",
        scratch,
        book_title="My Book",
    )
    assert [c.title for c in chapters] == ["My Book"]


def test_parse_pdf_threads_footnote_policy(
    repo_root: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import audiobook.parse_pdf as pdf_mod

    seen: dict[str, str] = {}
    real_clean = pdf_mod.clean_pdf_markdown

    def spy(text: str, *, footnote_policy: str = "skip", **kw: object) -> str:
        seen["policy"] = footnote_policy
        return real_clean(text, footnote_policy=footnote_policy)  # type: ignore[arg-type]

    monkeypatch.setattr(pdf_mod, "clean_pdf_markdown", spy)
    parse_pdf(
        repo_root / "tests" / "fixtures" / "tiny.pdf",
        scratch,
        footnote_policy="endnote",
    )
    assert seen["policy"] == "endnote"


def test_cli_parse_dispatches_pdf(repo_root: Path, scratch: Path) -> None:
    runner = CliRunner()
    src = repo_root / "tests" / "fixtures" / "tiny.pdf"
    result = runner.invoke(app, ["parse", str(src), "--out", str(scratch)])
    assert result.exit_code == 0, result.output
    assert (scratch / "chapters" / "raw" / "00_chapter-one.json").exists()


def test_cli_parse_rejects_unknown_extension(
    repo_root: Path, scratch: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    bogus = tmp_path / "book.mobi"
    bogus.write_text("not supported")
    result = runner.invoke(app, ["parse", str(bogus), "--out", str(scratch)])
    assert result.exit_code != 0
    assert "unsupported" in result.output.lower()
