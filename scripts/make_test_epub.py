"""Build a minimal valid EPUB 3 with 3 short chapters for pipeline smoke-testing."""

from __future__ import annotations

import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "input" / "book.epub"

TITLE = "The Three Small Hills"
AUTHOR = "Test Author"
BOOK_ID = "urn:uuid:test-book-0001"
LANG = "en"

CHAPTERS = [
    (
        "ch1",
        "Chapter One: The Morning",
        "The sun rose slowly over the first hill. A small fox padded down the path, "
        "sniffing the cold grass. She had not eaten since the day before, and the village "
        "below was already stirring with smoke from morning fires.",
    ),
    (
        "ch2",
        "Chapter Two: The Visitor",
        "On the second hill stood an old stone hut. Inside, a woman named Mira poured tea "
        "for a stranger who had arrived in the night. He spoke little, but his eyes never "
        "left the small wooden box on the table between them.",
    ),
    (
        "ch3",
        "Chapter Three: The Answer",
        "By the time they reached the third hill, the sun was high. Mira opened the box. "
        "Inside was a single folded letter, the ink faded but still legible. She read it "
        "twice, then handed it to the stranger without a word.",
    ),
]


def xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">\n'
        f'<head><meta charset="utf-8"/><title>{title}</title></head>\n'
        f'<body><h1>{title}</h1><p>{body}</p></body>\n'
        '</html>\n'
    )


def container_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>\n'
    )


def content_opf() -> str:
    manifest_items = "\n".join(
        f'    <item id="{cid}" href="{cid}.xhtml" media-type="application/xhtml+xml"/>'
        for cid, _, _ in CHAPTERS
    )
    spine_items = "\n".join(f'    <itemref idref="{cid}"/>' for cid, _, _ in CHAPTERS)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="book-id">{BOOK_ID}</dc:identifier>\n'
        f'    <dc:title>{TITLE}</dc:title>\n'
        f'    <dc:creator>{AUTHOR}</dc:creator>\n'
        f'    <dc:language>{LANG}</dc:language>\n'
        '    <meta property="dcterms:modified">2026-05-23T00:00:00Z</meta>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        f'{manifest_items}\n'
        '  </manifest>\n'
        '  <spine>\n'
        f'{spine_items}\n'
        '  </spine>\n'
        '</package>\n'
    )


def nav_xhtml() -> str:
    links = "\n".join(
        f'      <li><a href="{cid}.xhtml">{title}</a></li>' for cid, title, _ in CHAPTERS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">\n'
        '<head><meta charset="utf-8"/><title>Contents</title></head>\n'
        '<body>\n'
        '  <nav epub:type="toc" id="toc"><h1>Contents</h1><ol>\n'
        f'{links}\n'
        '  </ol></nav>\n'
        '</body>\n'
        '</html>\n'
    )


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w") as z:
        # mimetype must be first and STORED (uncompressed) per EPUB spec.
        z.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr("META-INF/container.xml", container_xml(), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", content_opf(), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav_xhtml(), compress_type=zipfile.ZIP_DEFLATED)
        for cid, title, body in CHAPTERS:
            z.writestr(f"OEBPS/{cid}.xhtml", xhtml(title, body), compress_type=zipfile.ZIP_DEFLATED)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
