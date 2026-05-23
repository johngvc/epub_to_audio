"""Stage 1 — EPUB parser. Reads an EPUB, emits per-chapter JSON and a
plaintext-ish book_full_text.md used by adaptation subagents."""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import ebooklib  # type: ignore[import-untyped]
from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from ebooklib import epub  # type: ignore[import-untyped]
from markdownify import markdownify  # type: ignore[import-untyped]

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from audiobook.models import ChapterRaw
from audiobook.utils.slugify import slugify

_SKIP_PATTERNS = (
    re.compile(r"copyright", re.I),
    re.compile(r"acknowledg", re.I),
    re.compile(r"dedication", re.I),
    re.compile(r"^index$", re.I),
    re.compile(r"bibliograph", re.I),
)


def _likely_skip(title: str, text: str) -> bool:
    if any(p.search(title) for p in _SKIP_PATTERNS):
        return True
    return len(text.split()) < 10


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


def _detect_features(soup: BeautifulSoup) -> tuple[bool, bool, bool]:
    has_code = soup.find(["pre", "code"]) is not None
    has_math = bool(soup.find("math")) or bool(soup.find(class_="math"))
    has_tables = soup.find("table") is not None
    return has_code, has_math, has_tables


def _strip_for_full_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["pre", "code"]):
        if isinstance(tag, Tag):
            tag.replace_with("[code block]")
    for tag in soup.find_all("script") + soup.find_all("style"):
        if isinstance(tag, Tag):
            tag.decompose()
    md: str = markdownify(str(soup), heading_style="ATX")
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


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
        if _likely_skip(title, text):
            continue

        has_code, has_math, has_tables = _detect_features(soup)
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

        full_text_sections.append(f"# {title}\n\n{_strip_for_full_text(str(soup))}")
        index += 1

    (out_dir / "book_full_text.md").write_text("\n\n".join(full_text_sections) + "\n")
    return chapters
