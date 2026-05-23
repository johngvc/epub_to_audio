from __future__ import annotations

from pathlib import Path

from audiobook.models import ChapterRaw
from audiobook.parse import parse_epub


def test_parse_produces_chapter_files(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "tiny.epub"
    chapters = parse_epub(src, scratch)

    raw_dir = scratch / "chapters" / "raw"
    files = sorted(raw_dir.glob("*.json"))
    assert len(files) == 3
    assert len(chapters) == 3
    for f in files:
        ChapterRaw.model_validate_json(f.read_text())


def test_parse_emits_book_full_text(repo_root: Path, scratch: Path) -> None:
    parse_epub(repo_root / "tests" / "fixtures" / "tiny.epub", scratch)
    md = (scratch / "book_full_text.md").read_text()
    assert "[code block]" in md
    assert "Tiny Technical Book" in md or "Introduction" in md


def test_parse_detects_features(repo_root: Path, scratch: Path) -> None:
    parse_epub(repo_root / "tests" / "fixtures" / "tiny.epub", scratch)
    raw_dir = scratch / "chapters" / "raw"
    files = sorted(raw_dir.glob("*.json"))
    chapters = [ChapterRaw.model_validate_json(f.read_text()) for f in files]
    by_index = {c.index: c for c in chapters}
    # tiny.epub: chapter 1 has <pre><code>; chapter 2 has <table>
    # Indexes start at 0 in our schema, but fixture uses 1-based chapter numbers in titles.
    # We assert by feature presence, not by exact index.
    assert any(c.has_code for c in chapters)
    assert any(c.has_tables for c in chapters)


def test_parse_is_idempotent(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "tiny.epub"
    parse_epub(src, scratch)
    first = sorted((scratch / "chapters" / "raw").glob("*.json"))
    first_bytes = [p.read_bytes() for p in first]
    parse_epub(src, scratch)
    second = sorted((scratch / "chapters" / "raw").glob("*.json"))
    assert [p.read_bytes() for p in second] == first_bytes
