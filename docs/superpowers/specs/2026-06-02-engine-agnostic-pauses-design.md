# Engine-Agnostic Pauses — Design

**Date:** 2026-06-02
**Status:** Approved (design); pending spec review

## Problem

When listening to rendered chapters, two kinds of pauses are missing:

1. **Sentence-to-sentence run-on inside a paragraph** — consecutive sentences
   blur together; each new sentence needs a small breath.
2. **Author-intended dramatic / topic-shift beats** — rhetorical pauses the
   author clearly intends ("And that changes everything.") get no weight.

Both are *within-chunk* problems. Today the pipeline inserts real silence only
**between chunks** (`trailing_silence_ms`): 400ms at paragraph ends, 1200ms at
`---` section breaks. *Within* a chunk there is zero inserted silence — pauses
rely entirely on the TTS engine's prosody reacting to punctuation. Neither
Chatterbox nor Kokoro does this reliably, so these spots come out flat.

## Approach

Stop relying on engine prosody. Make **all** pauses real inserted silence at
chunk boundaries — the mechanism that already works between paragraphs. Because
a chunk is one TTS utterance = one WAV, and silence can only be appended at
chunk boundaries, getting silence *between sentences* means **sentences become
chunk-level units** (sentence-granular chunking).

This keeps all pause logic in one place (`audiobook/chunk.py`), is config-driven
and unit-testable, is fully engine-agnostic and deterministic, and leaves the
render loop unchanged (one chunk = one WAV = one TTS call).

Rejected alternative: split each packed chunk inside the render loop and concat
sub-renders with silence. Avoids more chunks but complicates render and breaks
the clean one-chunk-one-WAV model. Not chosen.

## Changes

### 1. Chunker — sentence-granular (`audiobook/chunk.py`)

`chunk_chapter` changes so each chunk corresponds to one sentence, preserving
the existing safety behaviors:

- **Long sentences** (`> max_chars`) still split at clause boundaries via the
  existing `split_long_sentence`. Sub-pieces of *one* sentence get **0ms**
  between them — a forced length split must not sound like a pause. Only the
  *final* sub-piece of the sentence carries the structural trailing silence.
- **Tiny sentences** (`< min_orphan_chars`, currently 20) still merge **forward**
  into the next sentence, exactly as today, so we never emit a lone fragment and
  never wrap a beat around one. The merged unit is a single chunk / single TTS
  call.
- Cross-sentence **greedy packing is removed.** Orphan-merge and long-split are
  retained. The old `pack_sentences` is replaced by a routine that yields one
  unit per logical sentence (each unit possibly multiple sub-pieces for length),
  carrying a flag for whether a beat sentinel follows it.

**Trailing-silence assignment**, computed from structural position then beat
override:

| Position | Base silence |
|---|---|
| Sentence, mid-paragraph | `sentence_silence_ms` (new) |
| Last sentence of a paragraph (paragraph is not the last in chapter) | `paragraph_silence_ms` (unchanged) |
| Last sentence of the last paragraph | `0` |
| Sub-piece of a long sentence (not the final sub-piece) | `0` |

Then overrides, applied as a `max` so a larger pause never shrinks a structural
one:

- **Section break** (`---` paragraph): preceding chunk →
  `max(base, section_silence_ms)` (preserves existing 1200ms behavior).
- **Beat sentinel** follows the sentence → `max(base, beat_silence_ms)`.

Precedence with defaults (180 / 400 / 600 / 1200) therefore reads naturally:
sentence < paragraph < beat < section.

### 2. Beat sentinel (the adapt-phase change)

The adapter may emit the literal token **`[[beat]]`** at a sentence boundary
where the author intends dramatic weight or a topic shift. It lives inline in
`adapted_text` (survives the JSON round-trip).

Chunker handling:

- Detect `[[beat]]` tokens, robust to surrounding whitespace.
- Attach `beat_silence_ms` (via the `max` rule above) to the chunk for the
  **preceding** sentence.
- **Strip the token** from chunk text so it is never spoken. Verified by a test
  asserting `[[beat]]` appears in no `chunk.text`.
- A stray sentinel with no preceding sentence (paragraph start, or a paragraph
  that is only the sentinel) is dropped harmlessly.

`prompts/adapt_system.md` gains a new rule, worded as conservatively as the
existing pronunciation-hint rule (Rule 8):

> **Dramatic beats — sparingly**: insert the literal token `[[beat]]` at a
> sentence boundary ONLY where the author clearly intends a dramatic pause or a
> topic shift (e.g. a one-line punchline, a "but everything changed" pivot). Do
> NOT use it for ordinary sentence breaks — the engine already gets a small
> automatic beat between sentences. When in doubt, omit. Zero beats in a chapter
> is perfectly acceptable.

The output-schema description notes `[[beat]]` is an allowed control token
inside `adapted_text`. The system prompt is not otherwise reworded (orchestrator
constraint: subagents receive it verbatim).

### 3. Config (`audiobook/config.py` + `config.toml`)

Add to `ChunkConfig` / `[chunk]`:

```python
sentence_silence_ms: int = Field(default=180, ge=0, le=5000)
beat_silence_ms: int = Field(default=600, ge=0, le=10_000)
```

`config.toml` `[chunk]` gains the two keys with brief comments. Existing knobs
(`max_chars`, `paragraph_silence_ms`, `section_silence_ms`) unchanged.

### 4. Wiring

- `chunk_chapter(...)` and `chunk_work_dir(...)` gain `sentence_silence_ms` and
  `beat_silence_ms` parameters, threaded through.
- `cli.py chunk_cmd` passes `cfg.chunk.sentence_silence_ms` and
  `cfg.chunk.beat_silence_ms`.

## Testing (`tests/test_chunk.py`)

- A multi-sentence paragraph → N chunks; non-final sentences carry
  `sentence_silence_ms`; the paragraph-final sentence carries
  `paragraph_silence_ms` (or `section_silence_ms` before `---`); last sentence of
  last paragraph carries 0.
- `[[beat]]` is stripped from all `chunk.text` and maps to `beat_silence_ms` on
  the preceding chunk.
- Beat at a paragraph end → `max(paragraph_silence_ms, beat_silence_ms)`.
- Stray/leading sentinel is dropped without error.
- Orphan-merge preserved: a tiny sentence merges forward; no lone fragment chunk.
- Long-sentence split preserved: `> max_chars` splits; internal sub-pieces have
  0ms; final sub-piece carries structural silence.

## Out of scope

- `Chunk.text` Pydantic cap is 400 while `max_chars` config allows up to 600 — a
  pre-existing latent mismatch, not touched here.
- No render-loop changes.

## Evaluation

After implementation: re-chunk and re-render **chapter 1 only**, with the
**Kokoro** engine, deleting its stale `chunks/` and `audio/chunks/` artifacts so
they regenerate. User listens and tunes `sentence_silence_ms` / `beat_silence_ms`
if needed.
