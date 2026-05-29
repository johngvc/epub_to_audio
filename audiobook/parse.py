"""Stage 1 — EPUB parser. Reads an EPUB, emits per-chapter JSON and a
plaintext-ish book_full_text.md used by adaptation subagents."""
from __future__ import annotations

import warnings
from pathlib import Path

import ebooklib  # type: ignore[import-untyped]
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from ebooklib import epub

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from audiobook.models import ChapterRaw  # noqa: E402
from audiobook.parse_common import (  # noqa: E402
    detect_features,
    likely_skip,
    strip_for_full_text,
)
from audiobook.utils.slugify import slugify  # noqa: E402


def _resolve_titles(book: epub.EpubBook) -> dict[str, str]:
    titles: dict[str, str] = {}

    def walk(items: list) -> None:  # type: ignore[type-arg]
        for entry in items:
            if isinstance(entry, tuple):
                section, children = entry
                if hasattr(section, "href") and section.href:
                    titles[section.href.split("#")[0]] = section.title
                walk(children)
            elif hasattr(entry, "href") and entry.href:
                titles[entry.href.split("#")[0]] = entry.title

    if book.toc:
        walk(list(book.toc))
    return titles


def parse_epub(epub_path: Path, out_dir: Path) -> list[ChapterRaw]:
    """Parse an EPUB into per-chapter JSON files plus book_full_text.md."""
    epub_path = Path(epub_path)
    out_dir = Path(out_dir)
    raw_dir = out_dir / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    book = epub.read_epub(str(epub_path))
    nav_titles = _resolve_titles(book)

    chapters: list[ChapterRaw] = []
    full_text_sections: list[str] = []
    index = 0

    for spine_id, _linear in book.spine:
        item = book.get_item_with_id(spine_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        href = item.get_name()
        soup = BeautifulSoup(item.get_content(), "lxml")
        for tag in soup.find_all(["script", "style", "nav"]):
            if isinstance(tag, Tag):
                tag.decompose()

        title = nav_titles.get(href)
        if not title:
            h = soup.find(["h1", "h2"])
            title = h.get_text(strip=True) if h else item.get_name()

        text = soup.get_text(" ", strip=True)
        if likely_skip(title, text):
            continue

        has_code, has_math, has_tables = detect_features(soup)
        chapter = ChapterRaw(
            index=index,
            title=title,
            source_spine_id=href,
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
