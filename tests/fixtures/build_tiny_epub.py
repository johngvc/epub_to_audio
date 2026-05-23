"""Build tests/fixtures/tiny.epub — three short chapters that exercise prose,
code, equations, a table, and a figure. Run inside Docker:

    docker compose run --rm audiobook python tests/fixtures/build_tiny_epub.py
"""
from __future__ import annotations

from pathlib import Path

from ebooklib import epub  # type: ignore[import-untyped]


def _chapter(idx: int, file_name: str, title: str, body: str) -> epub.EpubHtml:
    c = epub.EpubHtml(title=title, file_name=file_name, lang="en")
    c.content = (
        f"<html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1>{body}</body></html>"
    )
    return c


def build(out: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("tiny-fixture-001")
    book.set_title("Tiny Technical Book")
    book.set_language("en")
    book.add_author("Test Author")

    c1 = _chapter(
        1,
        "ch01_intro.xhtml",
        "Chapter 1: Introduction",
        "<p>This short book demonstrates the parser. It contains prose, code, "
        "equations, a table, and a figure.</p>"
        "<p>The author writes in a measured, deliberate tone.</p>",
    )
    c2 = _chapter(
        2,
        "ch02_code.xhtml",
        "Chapter 2: A Code Example",
        "<p>The following Python function illustrates recursion.</p>"
        "<pre><code class=\"python\">def fact(n):\n    return 1 if n == 0 else n * fact(n-1)</code></pre>"
        "<p>The equation <span class=\"math\">n! = n * (n-1)!</span> captures the same idea.</p>",
    )
    c3 = _chapter(
        3,
        "ch03_table.xhtml",
        "Chapter 3: A Table and a Figure",
        "<p>The table below compares two approaches.</p>"
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<figure><img src=\"placeholder.png\" alt=\"A placeholder figure\" />"
        "<figcaption>Figure 1: placeholder.</figcaption></figure>"
        "<p>The figure above is referenced from the prose, so it should be described.</p>",
    )

    for c in (c1, c2, c3):
        book.add_item(c)

    book.toc = (
        epub.Link("ch01_intro.xhtml", "Chapter 1: Introduction", "ch1"),
        epub.Link("ch02_code.xhtml", "Chapter 2: A Code Example", "ch2"),
        epub.Link("ch03_table.xhtml", "Chapter 3: A Table and a Figure", "ch3"),
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", c1, c2, c3]

    out.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out), book)
    print(f"wrote {out}")


if __name__ == "__main__":
    build(Path(__file__).resolve().parent / "tiny.epub")
