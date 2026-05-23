from pathlib import Path

import ebooklib  # type: ignore[import-untyped]
from ebooklib import epub


def test_tiny_epub_exists_and_opens(repo_root: Path) -> None:
    path = repo_root / "tests" / "fixtures" / "tiny.epub"
    assert path.exists(), "Run tests/fixtures/build_tiny_epub.py to generate it."
    book = epub.read_epub(str(path))
    docs = [it for it in book.get_items() if it.get_type() == ebooklib.ITEM_DOCUMENT]
    titles = [d.get_name() for d in docs]
    assert len(docs) >= 3
    assert any("code" in t or "chap" in t.lower() for t in titles)
