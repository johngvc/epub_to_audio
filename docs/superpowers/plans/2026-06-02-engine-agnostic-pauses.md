# Engine-Agnostic Pauses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert real, deterministic silence between sentences and at author-marked dramatic beats, so pauses no longer depend on unreliable TTS-engine prosody.

**Architecture:** Move all pause logic into the chunker (`audiobook/chunk.py`). Each sentence becomes its own chunk carrying a `trailing_silence_ms`; a `[[beat]]` sentinel emitted by the adapter maps to a larger beat silence and is stripped from the spoken text. Two new `[chunk]` config knobs (`sentence_silence_ms`, `beat_silence_ms`) drive the values.

**Tech Stack:** Python, Pydantic v2, pysbd, pytest, Typer.

Reference spec: `docs/superpowers/specs/2026-06-02-engine-agnostic-pauses-design.md`

---

### Task 1: Config knobs

**Files:**
- Modify: `audiobook/config.py` (`ChunkConfig`)
- Modify: `config.toml` (`[chunk]`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Add fields to `ChunkConfig`** in `audiobook/config.py`, after `section_silence_ms`:

```python
class ChunkConfig(_Strict):
    max_chars: int = Field(default=400, ge=50, le=600)
    sentence_silence_ms: int = Field(default=180, ge=0, le=5000)
    paragraph_silence_ms: int = Field(default=400, ge=0, le=5000)
    section_silence_ms: int = Field(default=1200, ge=0, le=10_000)
    beat_silence_ms: int = Field(default=600, ge=0, le=10_000)
```

- [ ] **Step 2: Add keys to `config.toml`** under `[chunk]`:

```toml
[chunk]
# Affects how the adapted text is sliced into TTS-sized pieces.
max_chars = 400               # smaller → more stable TTS but more chunks
sentence_silence_ms = 180     # beat inserted between sentences within a paragraph
paragraph_silence_ms = 400
section_silence_ms = 1200
beat_silence_ms = 600         # silence for an adapter-emitted [[beat]] sentinel
```

- [ ] **Step 3: Add a test** in `tests/test_config.py` (append):

```python
def test_chunk_config_pause_defaults() -> None:
    from audiobook.config import ChunkConfig

    c = ChunkConfig()
    assert c.sentence_silence_ms == 180
    assert c.beat_silence_ms == 600
```

- [ ] **Step 4: Run** `uv run pytest tests/test_config.py -q` → PASS.

- [ ] **Step 5: Commit** `feat(chunk): add sentence_silence_ms + beat_silence_ms config knobs`

---

### Task 2: Sentence-granular chunker + beat sentinel

**Files:**
- Modify: `audiobook/chunk.py` (`chunk_chapter`, add `_sentence_units`, add `BEAT_TOKEN`; keep `pack_sentences`)
- Test: `tests/test_chunk.py`

- [ ] **Step 1: Write failing tests** — append to `tests/test_chunk.py`:

```python
def _adapt(text: str) -> ChapterAdapted:
    return ChapterAdapted(adapted_text=text, pronunciation_hints=[], notes="")


def _chunk(text: str, **kw):
    defaults = dict(
        index=0, title="t", pronunciation=[], max_chars=400,
        sentence_silence_ms=180, paragraph_silence_ms=400,
        section_silence_ms=1200, beat_silence_ms=600,
    )
    defaults.update(kw)
    return chunk_chapter(adapted=_adapt(text), **defaults)


def test_inter_sentence_silence_within_paragraph() -> None:
    cc = _chunk(
        "First sentence here. Second sentence here. Third sentence here.\n\n"
        "Next paragraph sentence."
    )
    # Para 1's three sentences: first two are mid-paragraph (180), third is
    # the paragraph break (400).
    assert [c.trailing_silence_ms for c in cc.chunks[:3]] == [180, 180, 400]


def test_beat_sentinel_maps_to_beat_silence_and_is_stripped() -> None:
    cc = _chunk("And that changes everything. [[beat]] The rest follows naturally here.")
    assert all("[[beat]]" not in c.text for c in cc.chunks)
    assert cc.chunks[0].text == "And that changes everything."
    # Mid-paragraph base would be 180; the beat overrides to 600.
    assert cc.chunks[0].trailing_silence_ms == 600


def test_beat_at_paragraph_end_takes_max() -> None:
    cc = _chunk(
        "Opening sentence here.\n\nFinal punch line here. [[beat]]\n\nClosing paragraph sentence."
    )
    punch = next(c for c in cc.chunks if c.text == "Final punch line here.")
    assert punch.trailing_silence_ms == 600  # max(paragraph 400, beat 600)


def test_stray_leading_sentinel_dropped() -> None:
    cc = _chunk("[[beat]] Hello there world.")
    assert all("[[beat]]" not in c.text for c in cc.chunks)
    assert len(cc.chunks) == 1
    assert cc.chunks[0].text == "Hello there world."
    assert cc.chunks[0].trailing_silence_ms == 0


def test_orphan_sentence_merges_forward_no_lone_fragment() -> None:
    cc = _chunk("This is a normal sentence. X.\n\nNext paragraph here now.")
    assert not any(c.text == "X." for c in cc.chunks)


def test_long_sentence_internal_pieces_have_zero_silence() -> None:
    big = ("alpha beta, " * 50).strip().rstrip(",") + "."
    cc = _chunk(big + "\n\nShort tail paragraph here.", max_chars=400)
    long_chunks = cc.chunks[:-1]  # everything except the tail paragraph
    assert len(long_chunks) >= 2
    assert all(len(c.text) <= 400 for c in cc.chunks)
    assert long_chunks[-1].trailing_silence_ms == 400  # final piece → paragraph break
    assert all(c.trailing_silence_ms == 0 for c in long_chunks[:-1])  # internal splits
```

- [ ] **Step 2: Run** `uv run pytest tests/test_chunk.py -q` → FAIL (chunk_chapter has no `sentence_silence_ms`/`beat_silence_ms` params).

- [ ] **Step 3: Implement.** In `audiobook/chunk.py` add near the top, after `_SEGMENTER`:

```python
BEAT_TOKEN = "[[beat]]"
```

Add a helper (place above `chunk_chapter`):

```python
def _sentence_units(
    sentences: list[str], max_chars: int, min_orphan_chars: int
) -> list[list[str]]:
    """Turn a segment's sentences into per-sentence units.

    Each unit is a list of sub-pieces: a normal sentence yields ``[sentence]``;
    a sentence longer than ``max_chars`` yields several length-split pieces. Tiny
    sentences (< ``min_orphan_chars``) merge forward into the next sentence (or
    backward into the previous one if they are last) so we never emit a lone
    fragment — the same orphan rule the old packer used, minus cross-sentence
    packing.
    """
    merged: list[str] = []
    pending: str | None = None
    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        if pending is not None:
            s = pending + " " + s
            pending = None
        if len(s) < min_orphan_chars:
            pending = s
            continue
        merged.append(s)
    if pending is not None:
        if merged:
            merged[-1] = merged[-1] + " " + pending
        else:
            merged.append(pending)
    return [split_long_sentence(s, max_chars) for s in merged]
```

Replace the whole `chunk_chapter` function with:

```python
def chunk_chapter(
    *,
    index: int,
    title: str,
    adapted: ChapterAdapted,
    pronunciation: list[PronunciationHint],
    max_chars: int,
    paragraph_silence_ms: int,
    section_silence_ms: int,
    sentence_silence_ms: int = 180,
    beat_silence_ms: int = 600,
) -> ChapterChunks:
    text = apply_pronunciation(adapted.adapted_text, pronunciation + adapted.pronunciation_hints)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    all_chunks: list[Chunk] = []
    chunk_id = 0
    for p_i, paragraph in enumerate(paragraphs):
        if paragraph.strip() == "---":
            if all_chunks:
                last = all_chunks[-1]
                all_chunks[-1] = Chunk(
                    id=last.id,
                    text=last.text,
                    trailing_silence_ms=max(last.trailing_silence_ms, section_silence_ms),
                )
            continue

        is_last_paragraph = p_i == len(paragraphs) - 1

        # Split on beat sentinels first so pysbd never sees the token. A beat
        # follows every segment except the last; it attaches to the unit that
        # precedes it (empty segments collapse, so doubled/edge beats are safe).
        units: list[list] = []  # each: [pieces: list[str], beat_after: bool]
        segments = paragraph.split(BEAT_TOKEN)
        for s_idx, seg in enumerate(segments):
            seg = seg.strip()
            beat_here = s_idx < len(segments) - 1
            if seg:
                sentences = list(_SEGMENTER.segment(seg))
                for pieces in _sentence_units(sentences, max_chars, min_orphan_chars=20):
                    units.append([pieces, False])
            if beat_here and units:
                units[-1][1] = True

        for u_idx, (pieces, beat_after) in enumerate(units):
            is_last_unit = u_idx == len(units) - 1
            for pc_idx, piece in enumerate(pieces):
                is_last_piece = pc_idx == len(pieces) - 1
                if not is_last_piece:
                    trailing = 0  # forced mid-sentence split — no audible pause
                elif is_last_unit:
                    trailing = 0 if is_last_paragraph else paragraph_silence_ms
                else:
                    trailing = sentence_silence_ms
                if is_last_piece and beat_after:
                    trailing = max(trailing, beat_silence_ms)
                all_chunks.append(
                    Chunk(id=f"{chunk_id:04d}", text=piece, trailing_silence_ms=trailing)
                )
                chunk_id += 1

    return ChapterChunks(index=index, title=title, chunks=all_chunks)
```

- [ ] **Step 4: Run** `uv run pytest tests/test_chunk.py -q` → PASS (new + existing tests).

- [ ] **Step 5: Commit** `feat(chunk): sentence-granular chunks + [[beat]] pause sentinel`

---

### Task 3: Thread config through `chunk_work_dir` and the CLI

**Files:**
- Modify: `audiobook/chunk.py` (`chunk_work_dir`)
- Modify: `audiobook/cli.py` (`chunk_cmd`)
- Test: `tests/test_chunk.py` (covered) + manual `uv run pytest`

- [ ] **Step 1:** In `audiobook/chunk.py`, add params to `chunk_work_dir` signature (with defaults so existing callers keep working):

```python
def chunk_work_dir(
    work_dir: Path,
    *,
    max_chars: int,
    paragraph_silence_ms: int,
    section_silence_ms: int,
    sentence_silence_ms: int = 180,
    beat_silence_ms: int = 600,
    progress: Callable[[str], None] | None = None,
    verbose: bool = False,
) -> int:
```

- [ ] **Step 2:** In the `chunk_chapter(...)` call inside `chunk_work_dir`, pass the two new args:

```python
        chunks = chunk_chapter(
            index=raw_data["index"],
            title=raw_data["title"],
            adapted=adapted,
            pronunciation=pron,
            max_chars=max_chars,
            paragraph_silence_ms=paragraph_silence_ms,
            section_silence_ms=section_silence_ms,
            sentence_silence_ms=sentence_silence_ms,
            beat_silence_ms=beat_silence_ms,
        )
```

- [ ] **Step 3:** In `audiobook/cli.py` `chunk_cmd`, pass the config values to `_chunk_dir`:

```python
    n = _chunk_dir(
        work_dir,
        max_chars=cfg.chunk.max_chars,
        paragraph_silence_ms=cfg.chunk.paragraph_silence_ms,
        section_silence_ms=cfg.chunk.section_silence_ms,
        sentence_silence_ms=cfg.chunk.sentence_silence_ms,
        beat_silence_ms=cfg.chunk.beat_silence_ms,
        progress=(lambda line: typer.echo(line, err=True)) if verbose else None,
        verbose=verbose,
    )
```

- [ ] **Step 4: Run** `uv run pytest tests/test_chunk.py tests/test_verbose_progress.py tests/test_config.py -q` → PASS.

- [ ] **Step 5: Commit** `feat(cli): wire sentence/beat silence config into chunk stage`

---

### Task 4: Adapter beat-sentinel rule

**Files:**
- Modify: `prompts/adapt_system.md`

- [ ] **Step 1:** Add a new rule **9** (renumbering the current 9 "Whole-book context" to 10) — insert after the pronunciation rule (rule 8):

```markdown
9. **Dramatic beats — sparingly**: insert the literal token `[[beat]]` at a sentence boundary ONLY where the author clearly intends a dramatic pause or a topic shift (e.g. a one-line punchline, a "but everything changed" pivot). Do NOT use it for ordinary sentence breaks — the engine already inserts a small automatic beat between sentences. When in doubt, omit. Zero beats in a chapter is perfectly acceptable. The token must appear inline inside `adapted_text`; it is stripped before narration and never spoken.
```

- [ ] **Step 2:** In the schema block's `adapted_text` description line, note the allowed control token:

```json
  "adapted_text": "string — the full spoken-form text of the chapter, in plain prose, no markdown. May contain the literal control token [[beat]] to mark a dramatic pause.",
```

- [ ] **Step 3:** Verify by reading the file — no automated test (prompt text).

- [ ] **Step 4: Commit** `feat(adapt): conservative [[beat]] dramatic-pause rule`

---

### Task 5: Full suite + re-render chapter 1 (Kokoro) for evaluation

**Files:** none (operational)

- [ ] **Step 1: Run full suite** `uv run pytest -q` → PASS.

- [ ] **Step 2: Re-chunk + re-render chapter 1 only with Kokoro.** Delete stale chapter-1 chunk + audio artifacts so they regenerate, re-run chunk, then render only chapter 1 with the Kokoro engine. (Exact commands determined at execution time against the live `./work` dir and the Kokoro voice config.)

- [ ] **Step 3:** Hand `./work` chapter-1 output (or assembled snippet) to the user to listen and tune `sentence_silence_ms` / `beat_silence_ms`.

---

## Self-Review

- **Spec coverage:** sentence-granular chunker (Task 2) ✓; long-split 0ms internal + orphan-merge preserved (Task 2 tests) ✓; trailing-silence table incl. section/beat `max` (Task 2) ✓; `[[beat]]` strip + map (Task 2) ✓; config knobs 180/600 (Task 1) ✓; wiring (Task 3) ✓; adapt rule (Task 4) ✓; tests (Task 2) ✓; Kokoro ch1 eval (Task 5) ✓. `Chunk.text` 400/600 mismatch explicitly out of scope.
- **Placeholders:** none — all code shown. Task 5 step 2 commands are intentionally deferred to runtime because they depend on live `./work` state; this is an operational step, not code.
- **Type consistency:** `BEAT_TOKEN`, `_sentence_units` (returns `list[list[str]]`), `chunk_chapter` and `chunk_work_dir` new kwargs `sentence_silence_ms`/`beat_silence_ms` used identically across Tasks 2–3; config fields match names used in Task 3.
