# PDF Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.pdf` as a supported Stage-1 input so the existing end-to-end pipeline produces an audiobook from a PDF with no changes to Stages 2–5.

**Architecture:** A new `parse_pdf()` mirrors `parse_epub()`'s on-disk contract exactly — it writes `work/chapters/raw/NN_slug.json` (`ChapterRaw` records) plus `work/book_full_text.md`. The PDF text is extracted as Markdown by `pymupdf4llm` (Tier 1), run through deterministic audio-cleanup, **converted to HTML**, and from there it joins the EPUB code path: the existing feature-detection and full-text helpers are reused verbatim. Tier 2 (`marker-pdf`) is **out of scope for this release** (decided up front) — quality heuristics still run and, when they fire, log a warning and return Tier 1 output. Tables/math/code are flagged (`has_*`) and deferred to the LLM exactly as for EPUB; they are **not** stripped to narration tokens.

**Tech Stack:** Python 3.12, Pydantic v2 (strict), Typer, PyMuPDF / `pymupdf4llm` (extraction), `markdown` (Markdown→HTML), `pyspellchecker` (de-hyphenation dictionary, pure-Python, no system libs), BeautifulSoup/lxml (reused), pytest. Stage 1 runs inside the Docker image.

**Key decisions locked in (from spec review):**
1. **Content format:** convert parser Markdown → HTML and fill `ChapterRaw.html`. Zero downstream changes.
2. **Tables/math/code:** mirror EPUB — set `has_*` flags, defer to the LLM. No `[Table omitted]` tokens.
3. **Tier 2 (`marker-pdf`):** deferred to a future release. `--parser marker` errors cleanly; `auto` falls back to Tier 1 with a warning.
4. The pipeline's inter-stage contract is **files on disk**, not an in-memory `Book` object (the spec's assumption was wrong; verified against the code).

**Module layout (new + modified):**
- Create `audiobook/parse_common.py` — shared helpers extracted from `parse.py` (`detect_features`, `strip_for_full_text`, `likely_skip`, `SKIP_PATTERNS`).
- Create `audiobook/pdf_cleanup.py` — deterministic, unit-tested text cleanup.
- Create `audiobook/parse_pdf.py` — `parse_pdf()`, PDF detection (encrypted/scanned), quality heuristics, chapter splitting, MD→HTML, `ChapterRaw` assembly. Reuses `parse_common`.
- Modify `audiobook/parse.py` — import shared helpers from `parse_common` (keep `parse_epub` public API unchanged).
- Modify `audiobook/config.py` — add `ParseConfig`, wire into `AppConfig`.
- Modify `audiobook/cli.py` — generalize the `parse` command: dispatch on file extension, add `--parser` / `--footnote-policy` / `--chapter-level` / `--config`.
- Modify `bin/audiobook-run` — generalize input default/usage from "EPUB" to "book" (accepts `.pdf`).
- Modify `pyproject.toml` — add `pymupdf4llm`, `markdown`, `pyspellchecker`; refresh `uv.lock`; rebuild image.
- Modify `README.md` — ingestion-formats section.
- Create `tests/fixtures/build_tiny_pdf.py` + generated `tests/fixtures/tiny.pdf`, `tests/fixtures/scanned.pdf`, `tests/fixtures/encrypted.pdf`.
- Create `tests/test_pdf_cleanup.py`, `tests/test_parse_pdf.py`.

**Test command convention:** Stage 1 deps live in the Docker image, so run the suite in Docker via `bin/audiobook-test <path> -v` (equivalent to `pytest` inside the container). After Task 1 changes `pyproject.toml`, the image must be rebuilt before tests will see the new packages.

---

### Task 1: Add dependencies and rebuild the image

**Files:**
- Modify: `pyproject.toml:6-18` (the `[project].dependencies` list)

- [ ] **Step 1: Add the three runtime dependencies**

In `pyproject.toml`, extend the core `dependencies` list (these are needed by Stage 1, which always runs — not optional extras):

```toml
dependencies = [
    "ebooklib>=0.18",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "pydantic>=2.7",
    "typer>=0.12",
    "pysbd>=0.3",
    "markdownify>=0.12",
    "mutagen>=1.47",
    "soundfile>=0.12",
    "numpy>=1.26",
    "rich>=13.7",
    "pymupdf4llm>=0.0.17",
    "markdown>=3.6",
    "pyspellchecker>=0.8",
]
```

- [ ] **Step 2: Refresh the lockfile**

The Dockerfile runs `uv sync --frozen`, which fails closed if `uv.lock` is stale. Regenerate it:

Run: `uv lock`
Expected: `uv.lock` updates with `pymupdf4llm`, `pymupdf`, `markdown`, `pyspellchecker` and their transitive deps; exit 0.

- [ ] **Step 3: Rebuild the Docker image**

Run: `docker compose build audiobook`
Expected: build succeeds; the new packages install in the deps layer.

- [ ] **Step 4: Verify the imports resolve inside the container**

Run: `docker compose run --rm audiobook python -c "import pymupdf4llm, fitz, markdown, spellchecker; print('ok')"`
Expected: prints `ok` (no ImportError). `fitz` is the import name of PyMuPDF, pulled in by `pymupdf4llm`; `spellchecker` is the import name of `pyspellchecker`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add pymupdf4llm, markdown, pyspellchecker for PDF ingestion"
```

---

### Task 2: Extract shared parse helpers into `parse_common.py`

The PDF path reuses EPUB's feature-detection, full-text-stripping, and skip logic. Move them to a shared module so both ingesters import the same code (DRY). `parse_epub`'s public behavior must not change.

**Files:**
- Create: `audiobook/parse_common.py`
- Modify: `audiobook/parse.py:19-69` (remove the moved helpers, import them instead)
- Test: existing `tests/test_parse.py` is the regression guard

- [ ] **Step 1: Create `audiobook/parse_common.py`**

```python
"""Helpers shared by the EPUB and PDF ingesters (Stage 1).

Both ingesters converge on an HTML string per chapter, then derive the same
artifacts from it. Keeping these here keeps the two parsers behaviorally
identical and DRY.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

SKIP_PATTERNS = (
    re.compile(r"copyright", re.I),
    re.compile(r"acknowledg", re.I),
    re.compile(r"dedication", re.I),
    re.compile(r"^index$", re.I),
    re.compile(r"bibliograph", re.I),
)


def likely_skip(title: str, text: str) -> bool:
    """True for front/back matter and trivially short sections."""
    if any(p.search(title) for p in SKIP_PATTERNS):
        return True
    return len(text.split()) < 10


def detect_features(soup: BeautifulSoup) -> tuple[bool, bool, bool]:
    """Return (has_code, has_math, has_tables) from a parsed HTML soup."""
    has_code = soup.find(["pre", "code"]) is not None
    has_math = bool(soup.find("math")) or bool(soup.find(class_="math"))
    has_tables = soup.find("table") is not None
    return has_code, has_math, has_tables


def strip_for_full_text(html: str) -> str:
    """Convert chapter HTML to the plaintext-ish markdown used as whole-book
    context by the adaptation step (code blocks collapsed to a token)."""
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
```

- [ ] **Step 2: Rewrite `audiobook/parse.py` to import the shared helpers**

Replace the top of `parse.py` (the imports through `_strip_for_full_text`, currently lines 1–69) with the version below. Everything from `def parse_epub(` downward stays exactly as-is, except internal calls now use the imported names (`_likely_skip`→`likely_skip`, `_detect_features`→`detect_features`, `_strip_for_full_text`→`strip_for_full_text`).

```python
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
```

- [ ] **Step 3: Update the three call sites inside `parse_epub`**

In `parse.py`, inside `parse_epub`, change:
- `if _likely_skip(title, text):` → `if likely_skip(title, text):`
- `has_code, has_math, has_tables = _detect_features(soup)` → `has_code, has_math, has_tables = detect_features(soup)`
- `full_text_sections.append(f"# {title}\n\n{_strip_for_full_text(str(soup))}")` → `...{strip_for_full_text(str(soup))}")`

The now-removed `_SKIP_PATTERNS`, `_likely_skip`, `_detect_features`, `_strip_for_full_text`, and the `re` / `markdownify` imports they used should no longer appear in `parse.py` (they live in `parse_common.py` now). `_resolve_titles` stays in `parse.py` (EPUB-specific).

- [ ] **Step 4: Run the EPUB regression tests**

Run: `bin/audiobook-test tests/test_parse.py -v`
Expected: all 4 tests PASS (`test_parse_produces_chapter_files`, `test_parse_emits_book_full_text`, `test_parse_detects_features`, `test_parse_is_idempotent`).

- [ ] **Step 5: Commit**

```bash
git add audiobook/parse_common.py audiobook/parse.py
git commit -m "refactor(parse): extract shared HTML helpers into parse_common"
```

---

### Task 3: Cleanup — punctuation normalization and whitespace collapse

**Files:**
- Create: `audiobook/pdf_cleanup.py`
- Test: `tests/test_pdf_cleanup.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pdf_cleanup.py
from __future__ import annotations

from audiobook.pdf_cleanup import collapse_whitespace, normalize_punctuation


def test_normalize_smart_quotes_to_ascii() -> None:
    assert normalize_punctuation("“Hello,” she said.") == '"Hello," she said.'
    assert normalize_punctuation("it’s fine") == "it's fine"


def test_normalize_dashes_and_ellipsis() -> None:
    # en-dash, em-dash -> hyphen; horizontal ellipsis -> three dots; nbsp -> space
    assert normalize_punctuation("pages 3–5") == "pages 3-5"
    assert normalize_punctuation("wait—what") == "wait-what"
    assert normalize_punctuation("and so on…") == "and so on..."
    assert normalize_punctuation("a b") == "a b"


def test_collapse_whitespace_runs_and_blank_lines() -> None:
    text = "a   b\t c\n\n\n\nd  \n"
    assert collapse_whitespace(text) == "a b c\n\nd"
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audiobook.pdf_cleanup'`.

- [ ] **Step 3: Implement the two functions**

```python
# audiobook/pdf_cleanup.py
"""Deterministic, audio-oriented cleanup applied to PDF-extracted Markdown
before it is converted to HTML and handed to the rest of the pipeline.

Every artifact left here becomes audible, so the rules are conservative:
when a transform is ambiguous, prefer leaving the text alone.
"""
from __future__ import annotations

import re

_PUNCT_MAP = {
    "“": '"', "”": '"',          # curly double quotes
    "‘": "'", "’": "'",          # curly single quotes / apostrophe
    "–": "-", "—": "-",          # en-dash, em-dash
    "…": "...",                        # horizontal ellipsis
    " ": " ",                          # non-breaking space
}
_PUNCT_RE = re.compile("|".join(re.escape(k) for k in _PUNCT_MAP))


def normalize_punctuation(text: str) -> str:
    """Map TTS-ambiguous Unicode punctuation to plain ASCII equivalents."""
    return _PUNCT_RE.sub(lambda m: _PUNCT_MAP[m.group(0)], text)


def collapse_whitespace(text: str) -> str:
    """Collapse intra-line whitespace runs, trim line ends, and reduce any
    run of blank lines to a single blank line. Trailing/leading blank lines
    are stripped."""
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.splitlines()]
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
```

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add audiobook/pdf_cleanup.py tests/test_pdf_cleanup.py
git commit -m "feat(pdf): punctuation + whitespace cleanup"
```

---

### Task 4: Cleanup — de-hyphenation of line-break splits

Join `re-\ntrieval` → `retrieval` only when the joined form is a real word; keep genuine compounds like `state-of-\nthe-art`. The dictionary check is injectable so tests stay fast and deterministic.

**Files:**
- Modify: `audiobook/pdf_cleanup.py`
- Test: `tests/test_pdf_cleanup.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_pdf_cleanup.py
from audiobook.pdf_cleanup import dehyphenate


def test_dehyphenate_joins_real_words() -> None:
    vocab = {"retrieval", "payment"}
    assert dehyphenate("re-\ntrieval", is_word=vocab.__contains__) == "retrieval"
    assert dehyphenate("pay-\nment due", is_word=vocab.__contains__) == "payment due"


def test_dehyphenate_keeps_genuine_compounds() -> None:
    # "ofthe" is not a word -> collapse the newline but keep the hyphen.
    vocab = {"state", "art"}
    assert dehyphenate("state-of-\nthe-art", is_word=vocab.__contains__) == "state-of-the-art"


def test_dehyphenate_preserves_normal_text() -> None:
    assert dehyphenate("no hyphen breaks here", is_word=lambda w: True) == "no hyphen breaks here"
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -k dehyphenate -v`
Expected: FAIL — `ImportError: cannot import name 'dehyphenate'`.

- [ ] **Step 3: Implement `dehyphenate` plus the default dictionary**

Add to `audiobook/pdf_cleanup.py`:

```python
from collections.abc import Callable
from functools import lru_cache

# A word split across a line break: letters, a hyphen, end-of-line, then letters.
_HYPHEN_BREAK_RE = re.compile(r"([A-Za-z]+)-\n([A-Za-z]+)")


@lru_cache(maxsize=1)
def _default_speller() -> Callable[[str], bool]:
    """Lazily build a pure-Python spell checker (no system libs). Loaded once."""
    from spellchecker import SpellChecker

    spell = SpellChecker()

    def is_word(word: str) -> bool:
        return bool(spell.known([word.lower()]))

    return is_word


def dehyphenate(text: str, *, is_word: Callable[[str], bool] | None = None) -> str:
    """Re-join words broken by a hyphen at a line break.

    `re-\\ntrieval` -> `retrieval` when the joined form is a real word; otherwise
    collapse the newline but keep the hyphen so genuine compounds survive.
    `is_word` is injectable for testing; defaults to a bundled dictionary.
    """
    checker = is_word if is_word is not None else _default_speller()

    def _join(match: re.Match[str]) -> str:
        left, right = match.group(1), match.group(2)
        joined = left + right
        if checker(joined):
            return joined
        return f"{left}-{right}"

    return _HYPHEN_BREAK_RE.sub(_join, text)
```

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -k dehyphenate -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add audiobook/pdf_cleanup.py tests/test_pdf_cleanup.py
git commit -m "feat(pdf): dictionary-aware de-hyphenation of line-break splits"
```

---

### Task 5: Cleanup — strip orphan page numbers and repeated running headers

**Files:**
- Modify: `audiobook/pdf_cleanup.py`
- Test: `tests/test_pdf_cleanup.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_pdf_cleanup.py
from audiobook.pdf_cleanup import strip_page_artifacts


def test_strip_orphan_page_numbers() -> None:
    text = "End of a paragraph.\n42\nStart of the next paragraph."
    assert strip_page_artifacts(text) == "End of a paragraph.\nStart of the next paragraph."


def test_strip_repeated_running_headers() -> None:
    # A short line repeated >= 3 times (a running header) is dropped everywhere.
    text = "\n".join(
        [
            "CHAPTER TITLE",
            "Real body line one.",
            "CHAPTER TITLE",
            "Real body line two.",
            "CHAPTER TITLE",
            "Real body line three.",
        ]
    )
    assert strip_page_artifacts(text) == (
        "Real body line one.\nReal body line two.\nReal body line three."
    )


def test_strip_keeps_markdown_headings_and_long_lines() -> None:
    # Headings (start with #) and long lines are never treated as artifacts.
    text = "# Chapter One\n# Chapter One\n# Chapter One\nbody"
    assert strip_page_artifacts(text) == text
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -k strip -v`
Expected: FAIL — `ImportError: cannot import name 'strip_page_artifacts'`.

- [ ] **Step 3: Implement `strip_page_artifacts`**

Add to `audiobook/pdf_cleanup.py`:

```python
from collections import Counter

_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
_HEADER_REPEAT_THRESHOLD = 3
_HEADER_MAX_LEN = 60


def strip_page_artifacts(text: str) -> str:
    """Remove orphan page-number lines and repeated short running headers/footers
    that survived extraction.

    - Orphan page numbers: a line that is nothing but 1–4 digits.
    - Running headers/footers: a short (<60 char), non-heading line that recurs
      `_HEADER_REPEAT_THRESHOLD`+ times is treated as boilerplate and dropped
      at every occurrence. Markdown headings (`#…`) are never dropped.
    """
    lines = text.splitlines()

    counts: Counter[str] = Counter(
        ln.strip()
        for ln in lines
        if ln.strip()
        and not ln.lstrip().startswith("#")
        and len(ln.strip()) <= _HEADER_MAX_LEN
    )
    repeated = {s for s, n in counts.items() if n >= _HEADER_REPEAT_THRESHOLD}

    kept: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if _PAGE_NUMBER_RE.match(ln):
            continue
        if stripped in repeated:
            continue
        kept.append(ln)
    return "\n".join(kept)
```

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -k strip -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add audiobook/pdf_cleanup.py tests/test_pdf_cleanup.py
git commit -m "feat(pdf): strip orphan page numbers and repeated running headers"
```

---

### Task 6: Cleanup — footnote policy and the `clean_pdf_markdown` orchestrator

Footnote detection on Tier-1 output is best-effort (robust handling arrives with marker in v2). We support the three policies the spec requires over Markdown-style footnote definition lines (`[1] …`, `[^1]: …`).

**Files:**
- Modify: `audiobook/pdf_cleanup.py`
- Test: `tests/test_pdf_cleanup.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_pdf_cleanup.py
from audiobook.pdf_cleanup import apply_footnote_policy, clean_pdf_markdown


def test_footnote_policy_skip_removes_definitions() -> None:
    text = "Body text.\n[1] A footnote definition.\nMore body."
    assert apply_footnote_policy(text, "skip") == "Body text.\nMore body."


def test_footnote_policy_inline_leaves_text_untouched() -> None:
    text = "Body text.\n[1] A footnote definition.\nMore body."
    assert apply_footnote_policy(text, "inline") == text


def test_footnote_policy_endnote_moves_to_end() -> None:
    text = "Body text.\n[^1]: A footnote definition.\nMore body."
    expected = "Body text.\nMore body.\n\n---\n\n## Notes\n\n[^1]: A footnote definition."
    assert apply_footnote_policy(text, "endnote") == expected


def test_clean_pdf_markdown_runs_full_pipeline() -> None:
    vocab = {"retrieval"}
    raw = "“re-\ntrieval”\n\n\n42\nbody"
    out = clean_pdf_markdown(raw, footnote_policy="skip", is_word=vocab.__contains__)
    assert out == '"retrieval"\n\nbody'
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -k "footnote or clean_pdf" -v`
Expected: FAIL — `ImportError: cannot import name 'apply_footnote_policy'`.

- [ ] **Step 3: Implement footnote handling and the orchestrator**

Add to `audiobook/pdf_cleanup.py`:

```python
from typing import Literal

FootnotePolicy = Literal["inline", "endnote", "skip"]

# Markdown-ish footnote *definition* lines: "[1] …" or "[^1]: …" at line start.
_FOOTNOTE_DEF_RE = re.compile(r"^\s*\[\^?\d+\]:?\s+\S")


def apply_footnote_policy(text: str, policy: FootnotePolicy) -> str:
    """Handle footnote definition lines per policy.

    - inline: leave them in place (read where they appear).
    - skip:   drop them entirely (default for fiction).
    - endnote: move them to a "Notes" section at the end (default for nonfiction).

    Detection is heuristic over Tier-1 Markdown; precise extraction is a Tier-2
    (marker) concern, deferred to a future release.
    """
    if policy == "inline":
        return text

    body: list[str] = []
    notes: list[str] = []
    for line in text.splitlines():
        if _FOOTNOTE_DEF_RE.match(line):
            notes.append(line.strip())
        else:
            body.append(line)

    result = "\n".join(body)
    if policy == "endnote" and notes:
        result = result + "\n\n---\n\n## Notes\n\n" + "\n".join(notes)
    return result


def clean_pdf_markdown(
    text: str,
    *,
    footnote_policy: FootnotePolicy = "skip",
    is_word: Callable[[str], bool] | None = None,
) -> str:
    """Full deterministic cleanup, in dependency order: de-hyphenate (needs the
    original line breaks) → normalize punctuation → strip page artifacts →
    apply footnote policy → collapse whitespace."""
    text = dehyphenate(text, is_word=is_word)
    text = normalize_punctuation(text)
    text = strip_page_artifacts(text)
    text = apply_footnote_policy(text, footnote_policy)
    text = collapse_whitespace(text)
    return text
```

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_pdf_cleanup.py -v`
Expected: all tests in the file PASS (punctuation, whitespace, dehyphenate, strip, footnote, orchestrator).

- [ ] **Step 5: Commit**

```bash
git add audiobook/pdf_cleanup.py tests/test_pdf_cleanup.py
git commit -m "feat(pdf): footnote policy + clean_pdf_markdown orchestrator"
```

---

### Task 7: Build PDF test fixtures

We generate fixtures with PyMuPDF (`fitz`) so the repo carries no opaque binaries we can't regenerate, mirroring `tests/fixtures/build_tiny_epub.py`.

**Files:**
- Create: `tests/fixtures/build_tiny_pdf.py`
- Create (generated): `tests/fixtures/tiny.pdf`, `tests/fixtures/scanned.pdf`, `tests/fixtures/encrypted.pdf`

- [ ] **Step 1: Write the fixture builder**

```python
# tests/fixtures/build_tiny_pdf.py
"""Generate small PDF fixtures for Stage-1 PDF parsing tests.

Run inside the container:
    docker compose run --rm audiobook python tests/fixtures/build_tiny_pdf.py
Produces tiny.pdf (2 heading-delimited chapters with a hyphen line-break),
scanned.pdf (image-only, no text layer), and encrypted.pdf (password-locked).
"""
from __future__ import annotations

from pathlib import Path

import fitz  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent


def _write_heading(page: "fitz.Page", y: float, text: str) -> None:
    page.insert_text((72, y), text, fontsize=22, fontname="helv")


def _write_body(page: "fitz.Page", y: float, text: str) -> None:
    page.insert_text((72, y), text, fontsize=11, fontname="helv")


def build_tiny() -> None:
    doc = fitz.open()
    p1 = doc.new_page()
    _write_heading(p1, 80, "Chapter One")
    # A hyphenated line-break split that de-hyphenation should re-join.
    _write_body(p1, 120, "The information re-")
    _write_body(p1, 135, "trieval system worked well across the morning hours.")
    p2 = doc.new_page()
    _write_heading(p2, 80, "Chapter Two")
    _write_body(p2, 120, "On the second day the visitor returned with a wooden box.")
    doc.save(str(HERE / "tiny.pdf"))
    doc.close()


def build_scanned() -> None:
    # A page with no extractable text layer (simulates a scanned page).
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 300, 200)
    page.draw_rect(rect, color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    doc.save(str(HERE / "scanned.pdf"))
    doc.close()


def build_encrypted() -> None:
    doc = fitz.open()
    page = doc.new_page()
    _write_body(page, 100, "Secret contents that require a password.")
    doc.save(
        str(HERE / "encrypted.pdf"),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()


if __name__ == "__main__":
    build_tiny()
    build_scanned()
    build_encrypted()
    print("wrote tiny.pdf, scanned.pdf, encrypted.pdf")
```

- [ ] **Step 2: Generate the fixtures**

Run: `docker compose run --rm audiobook python tests/fixtures/build_tiny_pdf.py`
Expected: prints `wrote tiny.pdf, scanned.pdf, encrypted.pdf`; the three files appear in `tests/fixtures/`.

- [ ] **Step 3: Sanity-check the generated PDFs**

Run: `docker compose run --rm audiobook python -c "import fitz; d=fitz.open('tests/fixtures/tiny.pdf'); print(d.page_count, len(d[0].get_text())); e=fitz.open('tests/fixtures/encrypted.pdf'); print('enc', e.is_encrypted, e.needs_pass)"`
Expected: prints `2 <nonzero>` for tiny, then `enc True True` for encrypted.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/build_tiny_pdf.py tests/fixtures/tiny.pdf tests/fixtures/scanned.pdf tests/fixtures/encrypted.pdf
git commit -m "test(pdf): add tiny/scanned/encrypted PDF fixtures + builder"
```

---

### Task 8: `parse_pdf` — fail-loud detection for encrypted and scanned PDFs

**Files:**
- Create: `audiobook/parse_pdf.py`
- Test: `tests/test_parse_pdf.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parse_pdf.py
from __future__ import annotations

from pathlib import Path

import pytest

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
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_parse_pdf.py -k "encrypted or scanned" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audiobook.parse_pdf'`.

- [ ] **Step 3: Create `audiobook/parse_pdf.py` with the exceptions and detection**

```python
# audiobook/parse_pdf.py
"""Stage 1 — PDF parser. Extracts Markdown with pymupdf4llm (Tier 1), cleans it
for TTS, converts to HTML, and emits the same on-disk artifacts as the EPUB
parser: work/chapters/raw/NN_slug.json + book_full_text.md.

Tier 2 (marker-pdf) is deferred to a future release; --parser marker errors and
--parser auto falls back to Tier 1 with a logged warning.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import fitz  # type: ignore[import-untyped]

ParserChoice = Literal["auto", "pymupdf", "marker"]

# A page is considered to lack a usable text layer below this many characters.
_SCANNED_MIN_TOTAL_CHARS = 100
_SCANNED_MIN_CHARS_PER_PAGE = 50


class PdfParseError(Exception):
    """Base class for fatal PDF ingestion problems."""


class EncryptedPdfError(PdfParseError):
    """The PDF is password-protected / encrypted."""


class ScannedPdfError(PdfParseError):
    """The PDF has no usable text layer (likely scanned images)."""


class MarkerNotAvailableError(PdfParseError):
    """Tier 2 (marker-pdf) was requested but is not available in this build."""


def _open_and_guard(pdf_path: Path) -> tuple["fitz.Document", list[str]]:
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
```

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_parse_pdf.py -k "encrypted or scanned" -v`
Expected: 2 tests PASS. (`parse_pdf` doesn't fully exist yet — it's added in Task 9; these tests only exercise the guard, which `parse_pdf` will call first. To make them pass now, add the minimal `parse_pdf` stub below.)

Add this stub to `audiobook/parse_pdf.py` (it will be fully implemented in Task 9):

```python
def parse_pdf(
    pdf_path: Path,
    out_dir: Path,
    *,
    parser: ParserChoice = "auto",
    footnote_policy: str = "skip",
    chapter_level: int | None = None,
    book_title: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> list:  # noqa: ANN201 (return type tightened in Task 9)
    doc, page_texts = _open_and_guard(Path(pdf_path))
    doc.close()
    raise NotImplementedError("completed in Task 9")
```

Re-run: `bin/audiobook-test tests/test_parse_pdf.py -k "encrypted or scanned" -v`
Expected: 2 tests PASS (the guard raises before `NotImplementedError`).

- [ ] **Step 5: Commit**

```bash
git add audiobook/parse_pdf.py tests/test_parse_pdf.py
git commit -m "feat(pdf): fail loudly on encrypted and scanned PDFs"
```

---

### Task 9: `parse_pdf` — extraction, chapter splitting, MD→HTML, artifact emission

This is the core. It produces the exact on-disk contract `parse_epub` produces.

**Files:**
- Modify: `audiobook/parse_pdf.py`
- Test: `tests/test_parse_pdf.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_parse_pdf.py
from audiobook.models import ChapterRaw


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
    # "re-\ntrieval" must be re-joined before reaching the chapter HTML.
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
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_parse_pdf.py -k "produces or splits or dehyphenates or full_text or marker_choice" -v`
Expected: FAIL — `NotImplementedError: completed in Task 9` (or marker test fails because the guard runs before the marker check).

- [ ] **Step 3: Implement the full `parse_pdf`**

Add these imports at the top of `audiobook/parse_pdf.py` (alongside the existing ones):

```python
import re

import markdown as md_lib  # type: ignore[import-untyped]
import pymupdf4llm  # type: ignore[import-untyped]
from bs4 import BeautifulSoup

from audiobook.models import ChapterRaw
from audiobook.parse_common import detect_features, likely_skip, strip_for_full_text
from audiobook.pdf_cleanup import FootnotePolicy, clean_pdf_markdown
from audiobook.utils.slugify import slugify

# LaTeX-ish math markers pymupdf might pass through (rare without marker).
_MATH_RE = re.compile(r"\$\$|\\\(|\\\[")
```

Add the section-splitting helpers:

```python
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
    return md_lib.markdown(md_text, extensions=["tables", "fenced_code"])
```

Replace the Task-8 `parse_pdf` stub with the full implementation:

```python
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

    # Quality heuristics (Task 10 fills these in). When Tier 1 looks weak we only
    # warn, because Tier 2 fallback is deferred.
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
```

Add a temporary no-op heuristics function so this task's tests pass (Task 10 implements it for real):

```python
def _quality_warnings(md_text: str, page_texts: list[str], page_count: int) -> list[str]:
    return []
```

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_parse_pdf.py -v`
Expected: all `parse_pdf` tests PASS (chapter files, heading split + titles, de-hyphenation into HTML, book_full_text, marker raises, plus the Task-8 guard tests).

- [ ] **Step 5: Commit**

```bash
git add audiobook/parse_pdf.py tests/test_parse_pdf.py
git commit -m "feat(pdf): extract, split on headings, MD->HTML, emit chapter artifacts"
```

---

### Task 10: `parse_pdf` — quality heuristics for the warning signal

These detect weak Tier-1 extraction. With Tier 2 deferred they drive a warning (not a fallback), but the logic is the same one a future marker retry will gate on.

**Files:**
- Modify: `audiobook/parse_pdf.py`
- Test: `tests/test_parse_pdf.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_parse_pdf.py
from audiobook.parse_pdf import _quality_warnings


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
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_parse_pdf.py -k quality -v`
Expected: FAIL — the no-op `_quality_warnings` returns `[]`, so the first two tests fail.

- [ ] **Step 3: Replace the no-op `_quality_warnings` with the real heuristics**

```python
import statistics

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
```

(Drop the old one-line `_quality_warnings` stub.)

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_parse_pdf.py -v`
Expected: all `parse_pdf` tests PASS, including the three `quality` tests.

- [ ] **Step 5: Commit**

```bash
git add audiobook/parse_pdf.py tests/test_parse_pdf.py
git commit -m "feat(pdf): Tier-1 extraction-quality warning heuristics"
```

---

### Task 11: Add the `[parse]` config section

**Files:**
- Modify: `audiobook/config.py:36-73` (add `ParseConfig`, wire into `AppConfig`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_config.py
from audiobook.config import load_config


def test_parse_config_defaults_and_override(tmp_path) -> None:
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(
        '[book]\ntitle = "T"\nauthor = "A"\n'
        '[parse]\nparser = "pymupdf"\nfootnote_policy = "endnote"\nchapter_level = 2\n'
    )
    cfg = load_config(cfg_path)
    assert cfg.parse.parser == "pymupdf"
    assert cfg.parse.footnote_policy == "endnote"
    assert cfg.parse.chapter_level == 2


def test_parse_config_defaults_when_absent(tmp_path) -> None:
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text('[book]\ntitle = "T"\nauthor = "A"\n')
    cfg = load_config(cfg_path)
    assert cfg.parse.parser == "auto"
    assert cfg.parse.footnote_policy == "skip"
    assert cfg.parse.chapter_level is None
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_config.py -k parse_config -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'parse'`.

- [ ] **Step 3: Add `ParseConfig` and wire it in**

In `audiobook/config.py`, add the import `Literal` is already imported. Add this class (e.g. just after `BookConfig`):

```python
class ParseConfig(_Strict):
    parser: Literal["auto", "pymupdf", "marker"] = "auto"
    footnote_policy: Literal["inline", "endnote", "skip"] = "skip"
    chapter_level: int | None = Field(default=None, ge=1, le=6)
```

Then add the field to `AppConfig`:

```python
class AppConfig(_Strict):
    book: BookConfig = Field(default_factory=BookConfig)
    parse: ParseConfig = Field(default_factory=ParseConfig)
    adapt: AdaptConfig = Field(default_factory=AdaptConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    assemble: AssembleConfig = Field(default_factory=AssembleConfig)
```

- [ ] **Step 4: Run to verify pass**

Run: `bin/audiobook-test tests/test_config.py -k parse_config -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Document the section in `config.toml`**

Add to `config.toml` (after the `[book]` block):

```toml
[parse]
# PDF ingestion (ignored for EPUB input).
parser = "auto"            # "auto" | "pymupdf" | "marker" (marker deferred to a future release)
footnote_policy = "skip"   # "inline" | "endnote" | "skip"
# chapter_level = 1        # force the heading level used as chapter boundaries (1-6)
```

- [ ] **Step 6: Commit**

```bash
git add audiobook/config.py config.toml tests/test_config.py
git commit -m "feat(config): add [parse] section for PDF ingestion options"
```

---

### Task 12: CLI dispatch by extension + flags, and the run wrapper

**Files:**
- Modify: `audiobook/cli.py:13` (import), `:194-201` (the `parse` command)
- Modify: `bin/audiobook-run:11-12,36-41,113-117` (generalize input from EPUB to book)
- Test: `tests/test_parse_pdf.py`

- [ ] **Step 1: Write the failing test (extension dispatch routes .pdf to parse_pdf)**

```python
# add to tests/test_parse_pdf.py
from typer.testing import CliRunner

from audiobook.cli import app


def test_cli_parse_dispatches_pdf(repo_root: Path, scratch: Path) -> None:
    runner = CliRunner()
    src = repo_root / "tests" / "fixtures" / "tiny.pdf"
    result = runner.invoke(app, ["parse", str(src), "--out", str(scratch)])
    assert result.exit_code == 0, result.output
    assert (scratch / "chapters" / "raw" / "00_chapter-one.json").exists()


def test_cli_parse_rejects_unknown_extension(repo_root: Path, scratch: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    bogus = tmp_path / "book.mobi"
    bogus.write_text("not supported")
    result = runner.invoke(app, ["parse", str(bogus), "--out", str(scratch)])
    assert result.exit_code != 0
    assert "unsupported" in result.output.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `bin/audiobook-test tests/test_parse_pdf.py -k cli_parse -v`
Expected: FAIL — the current `parse` command always calls `_parse_epub`, so the `.pdf` invocation errors and the `.mobi` one is not rejected with a clear message.

- [ ] **Step 3: Update the import in `cli.py`**

Change line 13 area to also import the PDF parser and its error type:

```python
from audiobook.parse import parse_epub as _parse_epub
from audiobook.parse_pdf import PdfParseError
from audiobook.parse_pdf import parse_pdf as _parse_pdf
```

- [ ] **Step 4: Replace the `parse` command**

Replace the existing `parse` command (currently `cli.py:194-201`) with:

```python
@app.command("parse")
def parse(
    input_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, readable=True,
        help="Input book: .epub or .pdf",
    ),
    out: Path = typer.Option(Path("./work"), "--out", help="Output work directory."),  # noqa: B008
    config: Path = typer.Option(Path("./config.toml"), "--config"),  # noqa: B008
    parser: str | None = typer.Option(None, "--parser", help="PDF only: auto|pymupdf|marker."),
    footnote_policy: str | None = typer.Option(
        None, "--footnote-policy", help="PDF only: inline|endnote|skip."
    ),
    chapter_level: int | None = typer.Option(
        None, "--chapter-level", help="PDF only: heading level used as chapter boundaries (1-6)."
    ),
) -> None:
    """Stage 1 — parse an EPUB or PDF into per-chapter JSON + book_full_text.md."""
    suffix = input_path.suffix.lower()
    if suffix == ".epub":
        chapters = _parse_epub(input_path, out)
        typer.echo(f"parsed {len(chapters)} chapters -> {out}")
        return
    if suffix == ".pdf":
        # CLI flags override config; config supplies the defaults.
        cfg = load_config(config) if config.exists() else load_config_default()
        p = parser or cfg.parse.parser
        fp = footnote_policy or cfg.parse.footnote_policy
        cl = chapter_level if chapter_level is not None else cfg.parse.chapter_level
        try:
            chapters = _parse_pdf(
                input_path,
                out,
                parser=p,  # type: ignore[arg-type]
                footnote_policy=fp,  # type: ignore[arg-type]
                chapter_level=cl,
                book_title=cfg.book.title or None,
                progress=lambda line: typer.echo(line, err=True),
            )
        except PdfParseError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"parsed {len(chapters)} chapters -> {out}")
        return
    typer.echo(
        f"error: unsupported input format {suffix!r}. Supported: .epub, .pdf", err=True
    )
    raise typer.Exit(2)
```

Note: `load_config_default()` already exists in `cli.py` (line ~19). `load_config` is already imported.

- [ ] **Step 5: Run to verify pass**

Run: `bin/audiobook-test tests/test_parse_pdf.py -k cli_parse -v`
Expected: 2 tests PASS.

- [ ] **Step 6: Generalize `bin/audiobook-run` input from EPUB to book**

Make these edits in `bin/audiobook-run`:

- Line 12: `INPUT="./input/book.epub"` → leave the default but update the comment/usage. Change the default to auto-pick a book file. Replace line 12 with:
  ```bash
  INPUT=""   # default resolved below: ./input/book.epub or ./input/book.pdf
  ```
- After arg parsing (after line 69, the `done`), add default resolution:
  ```bash
  if [[ -z "$INPUT" ]]; then
    if [[ -f ./input/book.epub ]]; then INPUT="./input/book.epub"
    elif [[ -f ./input/book.pdf ]]; then INPUT="./input/book.pdf"
    else INPUT="./input/book.epub"; fi
  fi
  ```
- Usage text (lines 36, 40-41): change `INPUT_EPUB` → `INPUT_BOOK` and "Path to input EPUB" → "Path to input EPUB or PDF (default: ./input/book.epub, then ./input/book.pdf)".
- Preflight (lines 113-117): change the `--- EPUB input ---` comment and error message `err "EPUB not found: $INPUT"` → `err "input book not found: $INPUT"`. The existence check (`[[ ! -f "$INPUT" ]]`) is format-agnostic and stays.

- [ ] **Step 7: Smoke-test the run wrapper's dispatch (no full pipeline)**

Run: `bin/audiobook parse ./tests/fixtures/tiny.pdf --out ./work-pdf-smoke`
Expected: `parsed 2 chapters -> work-pdf-smoke` (the low-quality warning may print to stderr — fine). Then clean up: `rm -rf ./work-pdf-smoke`.

- [ ] **Step 8: Commit**

```bash
git add audiobook/cli.py bin/audiobook-run
git commit -m "feat(cli): dispatch parse by extension (.epub/.pdf) + PDF flags; generalize run input"
```

---

### Task 13: Documentation, full type/lint check, and acceptance corpus

**Files:**
- Modify: `README.md` (ingestion-formats)
- Create: `tests/fixtures/README.md` (where to drop the manual acceptance corpus)

- [ ] **Step 1: Update `README.md` ingestion section**

Under "How to use → 2. Drop your inputs in place", add after the existing input block:

```markdown
**Input formats:** `.epub` (default) and `.pdf`. For PDF, name the file `input/book.pdf`
(or pass the path: `bin/audiobook run ./input/book.pdf`). PDF options live in
`config.toml`'s `[parse]` block, overridable per run:

    bin/audiobook parse ./input/book.pdf --parser auto --footnote-policy skip --chapter-level 1

- `--parser auto|pymupdf|marker` — `auto` (default) extracts with pymupdf4llm and warns
  if the result looks low-quality. `marker` (better multi-column/equation handling) is
  deferred to a future release and currently errors.
- `--footnote-policy inline|endnote|skip` — default `skip`.
- `--chapter-level N` — heading level (1–6) used as chapter boundaries (default: H1, else H2).

**Not supported:** scanned/image-only PDFs (no OCR — fails with a clear message) and
encrypted PDFs (decrypt first, e.g. with `qpdf`).
```

Also add `pdf` to the Configuration reference table with a one-line `[parse]` entry:

```markdown
| `[parse].parser`, `.footnote_policy`, `.chapter_level` | PDF ingestion options | EPUB ignores these |
```

- [ ] **Step 2: Document the acceptance corpus location**

Create `tests/fixtures/README.md`:

```markdown
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
```

- [ ] **Step 3: Add the gitignore entry for the corpus**

Append to `.gitignore`:

```
tests/corpus/
```

- [ ] **Step 4: Full lint + type-check + test suite**

Run: `bin/audiobook-test -v`
Expected: entire suite PASSES (EPUB regression, all PDF cleanup, all parse_pdf, config).

Run: `docker compose run --rm audiobook ruff check audiobook tests`
Expected: no lint errors.

Run: `docker compose run --rm audiobook mypy audiobook`
Expected: no type errors. (New untyped third-party imports — `fitz`, `pymupdf4llm`, `markdown`, `spellchecker` — must carry `# type: ignore[import-untyped]` as written in the tasks above.)

- [ ] **Step 5: Commit**

```bash
git add README.md tests/fixtures/README.md .gitignore
git commit -m "docs(pdf): document PDF ingestion, options, and acceptance corpus"
```

---

## Self-Review (completed against the spec)

**Spec coverage:**
- Tiered parser w/ `--parser {auto,pymupdf,marker}` → Tasks 9, 11, 12. Tier 2 deferred per decision; `marker` errors cleanly (Task 9), `auto` warns instead of falling back (Tasks 9–10).
- Fall-back heuristics (no headings in long doc, low-text pages, single-line paragraphs, multi-column) → Task 10 implements no-headings + low-text. **Scope note:** single-line-paragraph and multi-column (x-clustering) heuristics are **intentionally omitted from v1** — with Tier 2 deferred they would only add to a warning string, not change behavior, and multi-column clustering is the highest-effort/lowest-payoff piece. They are the first thing to add alongside marker in v2. This is a deliberate cut, logged here so it isn't mistaken for full coverage.
- Scanned → fail loud (Task 8); encrypted → fail loud (Task 8). ✓
- Deterministic cleanup: de-hyphenation w/ dictionary (Task 4), page-number/header strip (Task 5), quote/dash/ellipsis normalize (Task 3), blank-line collapse (Task 3). ✓
- Footnotes inline|endnote|skip (Task 6) — heuristic, honestly scoped. ✓
- Tables/figures/code → **flag and defer to LLM** per decision (Task 9 sets `has_*`); the spec's `[Table omitted]` token is intentionally **not** implemented. ✓ (decision recorded)
- Math → LaTeX sets `has_math` and passes through unchanged for the LLM (Task 9). The spec's `[MATH: …]` sentinel is unnecessary given marker is deferred and pymupdf rarely emits LaTeX; revisit with marker in v2.
- Interface: `parse_pdf(path, out_dir, *, parser, footnote_policy, chapter_level, …) -> list[ChapterRaw]` (Task 9) — corrected from the spec's `-> Book` to match the real file-based contract.
- Chapter detection via heading levels, H1→H2 fallback, `--chapter-level` override (Tasks 9, 12). ✓
- Dependencies + optional-extra gating: marker deferred, so no torch extra needed; the three required deps are core (Task 1). ✓
- Acceptance criteria: end-to-end on a `.pdf` (Task 12 dispatch + reused downstream), test corpus documented (Task 13), unit tests for cleanup (Tasks 3–6), CLI help + README updated (Tasks 12–13). ✓

**Placeholder scan:** No TBD/TODO; every code step carries complete code; every test step shows the assertion and the expected run output.

**Type/signature consistency:** `FootnotePolicy`/`ParserChoice` literals are shared between `pdf_cleanup`, `parse_pdf`, and `config`; `clean_pdf_markdown`, `dehyphenate`, `apply_footnote_policy`, `_quality_warnings`, `parse_pdf`, `detect_features`, `strip_for_full_text`, `likely_skip` names match across all tasks that reference them.

**Known deliberate cuts (v2 candidates):** marker Tier 2, single-line-paragraph + multi-column heuristics, precise footnote extraction, `[MATH:]` sentinel handling. All only matter once marker lands.
