# Test fixtures

`tiny.epub`, `tiny.pdf`, `scanned.pdf`, `encrypted.pdf` are committed and small;
regenerate the PDFs with `python tests/fixtures/build_tiny_pdf.py` (inside the container).

## Manual PDF acceptance corpus (not committed — large/licensed)

Drop these under `tests/corpus/` (gitignored) to exercise the real parser end-to-end:

1. A clean digital novel PDF — e.g. a Project Gutenberg title.
2. A multi-column academic paper — e.g. an arXiv PDF.
3. A textbook page containing a table and a figure.
4. A scanned PDF — must fail with the "looks like a scanned PDF … OCR not implemented" error.

Run each through: `bin/audiobook parse tests/corpus/<file>.pdf --out ./work-corpus`
and inspect `work-corpus/chapters/raw/*.json` + `work-corpus/book_full_text.md`.
