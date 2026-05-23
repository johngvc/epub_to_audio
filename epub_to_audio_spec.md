# Technical Audiobook Pipeline — Project Specification

A local-first pipeline that converts **EPUB** technical books into high-quality `.m4b` audiobooks. Uses **Claude Sonnet 4.6** for content adaptation and **Chatterbox TTS** for narration. Optimized for listenability over speed; designed to run unattended on Apple Silicon (target: M5 MacBook Pro, 32 GB).

---

## How to use this spec (for Claude Code)

Read the spec in full before writing any code. Section dependencies are non-obvious:

- **§18 is prescriptive** — when other sections list a tool choice, §18 is the authoritative version. Older sections may mention alternatives ("Click or Typer") that have since been resolved.
- **§6 (adapt stage) is the highest-leverage section.** The quality of the final audiobook is determined by the system prompt and the validator, not by the TTS. Spend extra care here.
- **§17 (first-iteration deliverable) defines the build order.** Do not implement out of order — the order is designed to surface expensive mistakes early.
- **§15 (acceptance criteria) is the definition of done.** Build until those are met; resist scope creep into §16.
- **`CLAUDE.md` (in §6.2) is a separate artifact** — copy it verbatim from the spec into the project root. It is the contract that the orchestrator Claude Code instance follows at runtime.

Recommended sync point: after bootstrapping the project (per §17 step 1), stop and confirm `pyproject.toml`, `config.toml`, `CLAUDE.md`, and `prompts/adapt_system.md` with the user before implementing any stage logic.

---

## 1. Goals

- Turn an EPUB of a 500-page technical book into a polished `.m4b` audiobook with proper chapter markers and metadata.
- Skip or summarize content that doesn't belong in audio: code blocks, equations, tables, figures.
- Preserve the author's prose, argumentation, and voice.
- Pronounce technical terms correctly via a per-book pronunciation dictionary.
- **Narrate in any voice the user supplies** — including the user's own voice — from a short reference recording. All voice processing happens locally; the reference audio never leaves the machine.
- Use Claude Sonnet 4.6 for adaptation, with three supported transport modes (in order of preference):
  - **Agent mode (recommended)** — A Claude Code instance orchestrates parallel subagents, one per chapter. Runs on the user's Claude Pro/Max subscription. Fully automated, no per-token billing, parallelized.
  - **Chat mode** — User pastes prepared prompts into the Claude.ai chat interface. No per-token cost. Manual paste step required.
  - **API mode** — Anthropic SDK with prompt caching. Fully automated. Per-token billing.
- Be **resumable** at every stage — re-runs should not re-do completed work or re-spend API credits.

## 2. Non-goals

- PDF, MOBI, or other input formats. **EPUB only.**
- Real-time / streaming TTS. This is a batch pipeline.
- Multi-voice character attribution (this is for technical books, not fiction).
- A GUI. CLI only.
- Cloud deployment. Runs on the user's Mac.

## 3. Pipeline overview

```
EPUB
  │
  ▼
[1] Parse            →  chapters/raw/NN_title.json     (HTML + structure per chapter)
                         book_full_text.md             (whole-book context for subagents)
  │
  ▼
[2] Adapt            →  chapters/adapted/NN_title.json (validated, structured output)
  │                    pronunciation.json             (merged across chapters)
  │
  │   Adapt has three transport modes:
  │     • agent (default) — Claude Code orchestrates parallel subagents (recommended)
  │     • chat           — user pastes prepared prompts into Claude.ai
  │     • api            — Anthropic SDK with prompt caching
  ▼
[3] Chunk            →  chapters/chunks/NN_title.json  (sentence-bounded chunks ≤ 400 chars)
  │
  ▼
[4] Render (TTS)     →  audio/chunks/NN_title/MMMM.wav (one WAV per chunk)
  │
  ▼
[5] Assemble         →  out/book.m4b                   (chaptered, tagged, with cover)
```

Stages 1, 3, 4, and 5 are pure Python and identical regardless of adapt mode. Only stage 2's transport varies.

Every stage reads from disk and writes to disk. No stage holds state in memory between runs. Each stage is independently re-runnable and idempotent.

---

## 4. Tech stack (summary)

The full, prescriptive choices live in **§18 — Language and framework choices**. The summary here is informational:

- **Language**: Python 3.12+
- **Package manager**: `uv`
- **CLI**: Typer
- **Config**: TOML via `tomllib` + Pydantic v2
- **EPUB parsing**: `ebooklib` + `beautifulsoup4` + `lxml`
- **LLM**: Claude Sonnet 4.6 in one of three transport modes (agent / chat / API — see §6)
- **TTS**: `chatterbox-tts` on `torch` + MPS
- **Sentence segmentation**: `pysbd`
- **Audio assembly**: `ffmpeg` binary via `subprocess`; `mutagen` for tags; `mp4chaps` for chapter markers
- **Concurrency**: `concurrent.futures.ThreadPoolExecutor` for TTS render; `asyncio` for API-mode LLM calls only

System dependencies (Homebrew on macOS):
```
brew install ffmpeg mp4v2 espeak-ng uv
```

If choices below appear to conflict with §18, §18 wins.

---

## 5. Stage 1 — EPUB parsing

**Module**: `audiobook/parse.py`
**Entry point**: `parse_epub(epub_path: Path, out_dir: Path) -> list[ChapterRaw]`

### Behavior

1. Open the EPUB with `ebooklib.epub.read_epub(...)`.
2. Walk the **spine** to get the document order. Use the `nav` document (EPUB 3) or `toc.ncx` (EPUB 2) to map spine items to human-readable chapter titles.
3. For each spine item that is a content document:
   - Parse the XHTML with BeautifulSoup.
   - Strip `<script>`, `<style>`, navigation elements, and copyright/license pages (heuristics: very short, or matches common boilerplate patterns).
   - **Preserve** these as tagged elements for the LLM stage: `<pre>`, `<code>`, `<table>`, `<figure>`, `<img>` (with `alt` attribute), MathML, and inline math in `<span class="math">`.
   - Detect chapter boundaries: prefer the nav doc's structure; fall back to `<h1>` / `<h2>` within the HTML if the spine item contains multiple chapters.
4. Skip non-content matter: cover page, copyright, dedication, acknowledgments by default (configurable). Always skip the index and bibliography.
5. Write one JSON file per chapter to `chapters/raw/`:

```json
{
  "index": 7,
  "title": "Chapter 5: Concurrency Primitives",
  "source_spine_id": "ch05.xhtml",
  "html": "<h1>...</h1><p>...</p><pre><code class=\"python\">...</code></pre>...",
  "word_count_estimate": 4821,
  "has_code": true,
  "has_math": false,
  "has_tables": true
}
```

6. Also emit `work/book_full_text.md` — the entire book's prose, lightly cleaned (no HTML markup, code blocks replaced with `[code block]` markers, equations passed through verbatim). This file gives subagents whole-book context without requiring them to parse HTML. Generated once during parse; not regenerated unless the EPUB changes.

### CLI
```
audiobook parse INPUT.epub --out ./work
```

---

## 6. Stage 2 — LLM adaptation (Claude Sonnet 4.6)

**This is the most important stage.** Quality of the final audiobook is determined here, not in the TTS step.

The pipeline supports three transport modes for this stage, selected by `adapt.mode` in `config.toml`:

- **`mode = "agent"` (default, recommended)** — Claude Code orchestrates parallel subagents. Runs on the user's Pro/Max subscription. Fully automated, parallel, no per-token billing.
- **`mode = "chat"`** — Human-in-the-loop using the Claude.ai chat interface. No API key needed. No per-token cost. Manual paste step required.
- **`mode = "api"`** — Fully automated via the Anthropic SDK. Requires `ANTHROPIC_API_KEY` and accepts the per-token billing.

The system prompt and adapted-output schema are identical across all three modes. Only the transport differs.

### 6.1 System prompt (shared by all modes)

Stored as `prompts/adapt_system.md`. Contains these rules verbatim (rewordable, but semantically identical):

> You are adapting a chapter of a technical book for audiobook narration. The output will be read aloud by a text-to-speech engine. Your job is to produce a version that a listener will find clear, engaging, and free of visual artifacts.
>
> **Rules:**
>
> 1. **Code blocks**: do not read code aloud. If the surrounding prose treats the code as illustrative, replace the block with a one-sentence verbal description (e.g., "The author shows a short Python function that recursively walks the tree."). If the code is incidental or already adequately explained by the surrounding prose, omit it silently with no replacement marker.
> 2. **Equations**: convert to spoken English. "x² + 2x = 5" becomes "x squared plus two x equals five." For complex equations longer than ~15 spoken words, describe the structure instead of reading every symbol (e.g., "an integral from zero to infinity of a Gaussian function").
> 3. **Tables**: replace with a one-sentence summary of what the table shows and the key takeaway. Skip entirely if the table is pure reference data not discussed in the prose.
> 4. **Figures**: skip entirely unless the prose references them. If referenced, describe the figure in one sentence using its alt text and the surrounding context.
> 5. **Inline formatting**: convert lists with visual structure (numbered, bulleted) into prose with verbal transitions ("First… Second… Finally…"). Preserve emphasis through word choice, not markup.
> 6. **Acronyms**: expand on first use per chapter, then use the acronym. Add the acronym to the pronunciation hints if it's commonly mispronounced.
> 7. **Author's voice**: do not summarize prose. Do not paraphrase the author's actual writing. Only adapt non-prose elements and add transitions where needed for audio flow.
> 8. **Pronunciation hints**: as you go, collect any term whose pronunciation a TTS engine is likely to get wrong (library names, CLI tools, acronyms, foreign words, author names). Include them in the structured output.
> 9. **Whole-book context**: you have access to the full book at `work/book_full_text.md`. Consult it when you need cross-chapter context (terminology introduced earlier, recurring concepts, author voice patterns). Only output the adaptation of the chapter explicitly assigned to you.
>
> **Output format**: return a single JSON object with this exact schema. In agent mode, write it to the output file path specified in your dispatch message; in chat and API modes, return it directly:
>
> ```json
> {
>   "adapted_text": "string — the full spoken-form text of the chapter, in plain prose, no markdown",
>   "pronunciation_hints": [
>     {"term": "kubectl", "spoken_as": "cube control", "reason": "CLI tool, commonly mispronounced"},
>     {"term": "SQL", "spoken_as": "sequel", "reason": "acronym"}
>   ],
>   "notes": "string — any editorial decisions worth flagging for human review, or empty string"
> }
> ```
>
> Return only the JSON. No prose before or after.

### 6.2 Agent mode workflow (recommended default)

This mode uses Claude Code as the orchestrator. The user runs Claude Code in the project directory; it reads `CLAUDE.md`, parses the EPUB via bash, dispatches subagents in parallel for each chapter, validates outputs, and continues the pipeline through to assembly.

**Prerequisites:**
- Claude Code installed and logged in with a Pro/Max subscription.
- `CLAUDE.md` exists at the project root (template below).
- `config.toml` has `adapt.mode = "agent"`.
- EPUB placed at `./input/book.epub`, voice reference at `./voice/reference.wav`.

**`CLAUDE.md` template** — stored at the project root, version-controlled. This is the contract between the user, Claude Code (orchestrator), and dispatched subagents:

```markdown
# Audiobook Pipeline — Orchestrator Instructions

You are orchestrating the conversion of an EPUB book into an audiobook. Follow this workflow exactly.

## Setup verification
1. Confirm `./input/book.epub` exists. If missing, stop and ask the user.
2. Confirm `./voice/reference.wav` exists. If a voice preview has not been generated for it:
   - Run: `audiobook voice validate ./voice/reference.wav`
   - Run: `audiobook voice preview ./voice/reference.wav`
   - Ask the user to listen to `./voice/preview.wav` and confirm before continuing.
3. Read `config.toml` to confirm `adapt.mode = "agent"` and `adapt.concurrency` (default 8).

## Stage 1 — Parse
Run: `audiobook parse ./input/book.epub --out ./work`
Verify `./work/chapters/raw/` is populated and `./work/book_full_text.md` exists.

## Stage 2 — Adapt (your main job as orchestrator)
1. Read `./prompts/adapt_system.md` once. This is the system prompt for every subagent.
2. List all chapter files in `./work/chapters/raw/`.
3. Skip any chapter that already has a valid `./work/chapters/adapted/NN_title.json`. Use `audiobook validate-adapted ./work` to check.
4. For the remaining chapters, dispatch subagents in waves of up to `adapt.concurrency` (8 by default).
5. Each subagent dispatch must include:
   - The full system prompt from `./prompts/adapt_system.md`.
   - The exact input file path (`./work/chapters/raw/NN_title.json`).
   - The exact output file path (`./work/chapters/adapted/NN_title.json`).
   - A pointer to `./work/book_full_text.md` for cross-chapter context.
   - Explicit instruction: "Read the input file, apply all rules in the system prompt, write the JSON to the output file path. Do not print the JSON in your final reply — only write it to the file. Reply with: `DONE` plus a one-line summary of what you wrote, or `FAILED` plus the reason."
6. After each wave, run `audiobook validate-adapted ./work` and parse its output.
   - Chapters that pass move to "done."
   - Chapters that fail (malformed JSON, schema mismatch, length anomalies, markdown artifacts) are queued for retry. Include the validator's specific error in the retry dispatch so the subagent knows what to fix.
7. Each chapter gets at most 2 retries. If still failing, log in `./work/state.json` under `failures` and continue.
8. After all chapters processed, run `audiobook merge-pronunciation ./work`.
9. Report adaptation summary: succeeded / retried / failed counts.

## Stage 3 — Chunk
If adaptation completed without hard failures (or the user explicitly proceeds):
Run: `audiobook chunk ./work`

## Stage 4 — Render
Run: `audiobook render ./work` (2–4 hours; expected).
Then: `audiobook validate-render ./work` to confirm no chunks failed quality checks.

## Stage 5 — Assemble
Run: `audiobook assemble ./work --out ./out/book.m4b`

## Reporting
At each stage transition, write a brief progress summary. Do not spam — one summary per stage.
At the end, report total time per stage, counts (succeeded/retried/failed), final `.m4b` size and duration, and any chapters in `failures` worth reviewing.

## Failure handling
- Parse fails: stop, surface error.
- Adapt fails for >20% of chapters: stop after the first wave, ask user before continuing.
- Render fails for >5% of chunks: continue but warn at the end.
- Assemble fails: stop, surface ffmpeg error.

## Constraints
- Do not paraphrase or modify the system prompt before sending it to subagents.
- Do not put full chapter content into your own context unless debugging — let subagents handle it. Your context stays clean for orchestration.
- Do not advance to the next stage if the previous stage has unaddressed hard failures, unless the user confirms.
```

**Subagent contract** — what each dispatched subagent does:

1. Read its assigned `./work/chapters/raw/NN_title.json`.
2. Optionally consult `./work/book_full_text.md` for cross-chapter context.
3. Apply the system prompt's adaptation rules.
4. Write the JSON result to its assigned `./work/chapters/adapted/NN_title.json`.
5. Reply to the orchestrator with `DONE` + one-line summary, or `FAILED` + reason. Do not include the JSON itself in the reply — keep the orchestrator's context clean.

**Validation gate** — `audiobook validate-adapted ./work` is the orchestrator's quality gate. For every adapted file it checks:

- Parses as JSON.
- Matches the Pydantic schema in `audiobook/models.py`.
- `adapted_text` non-empty.
- No markdown artifacts (`<pre>`, ` ``` `, `<table>`, `$$`, `<h1>`).
- Length ratio vs source is within [0.30, 1.10] (catches over-summarization and accidental code-reading).

Output is grouped per-chapter so the orchestrator can act on specific failures.

**Concurrency** — default 8 in flight, configurable via `adapt.concurrency`. The orchestrator must not exceed this; more subagents in parallel hurts rate limits without speeding anything up.

**Resumability** — the orchestrator skips any chapter that already has a valid adapted file. Re-running Claude Code on the same project after a partial failure picks up exactly where it left off.

**Long-chapter handling** — chapters >6000 source words are pre-split at `<h2>` boundaries during stage 1 (parse), producing multiple `chapters/raw/NN_title.partK.json` files. Each part is dispatched as a separate subagent task. Adapted parts auto-concatenate at the merge step.

### 6.3 Chat mode workflow (when Claude Code is not in use)

**Recommended setup — once per book:**

1. Create a **Project** in Claude.ai called e.g. "Audiobook Adaptation — {Book Title}".
2. Paste the contents of `prompts/adapt_system.md` into the Project's **instructions** field. This makes the system prompt apply automatically to every chat in the Project, so you only paste chapter content from here on.
3. Upload `./work/book_full_text.md` as a Project knowledge file so each chat has whole-book context automatically.

**Per-chapter workflow:**

1. Run `audiobook prepare-prompts ./work`. This generates `chapters/prompts/NN_title.md` files, one per chapter. Each file contains:
   - A short header reminding you of the workflow.
   - A clearly marked `--- COPY BELOW THIS LINE ---` separator.
   - The chapter content formatted as the user message (chapter index, title, HTML content).
   - A footer reminding you to save the JSON response to `chapters/adapted/NN_title.json`.
2. For each chapter file (in order — start with the shortest one to validate your setup):
   - Open `chapters/prompts/NN_title.md`.
   - Copy everything below the `COPY BELOW` line.
   - Start a new chat inside the Project, paste, send.
   - When Claude returns the JSON, click the **Copy** button on the code block.
   - Save it as `chapters/adapted/NN_title.json` (matching the prompt filename, just different extension).
3. Periodically run `audiobook adapt-status ./work` to see progress — which chapters have validated outputs, which are missing, which have schema errors.
4. When all chapters are done, run `audiobook merge-pronunciation ./work` to deduplicate and merge per-chapter pronunciation hints into a single `pronunciation.json`.

**Validation on ingest** — when reading an adapted JSON file, the pipeline:

- Parses it as JSON (catches copy-paste truncation).
- Validates against the Pydantic schema (catches Claude returning prose around the JSON, which sometimes happens).
- Checks `adapted_text` is non-empty and contains no obvious markdown artifacts (`<pre>`, ` ``` `, `<table>`, `$$`).
- Estimates the length ratio vs the source chapter. Flags chapters where the adapted text is <30% or >110% of source word count for human review (could indicate accidental summarization or accidental code-reading).
- On any validation error, prints a specific message and points to the file for the user to fix or regenerate.

**If Claude's response is malformed** (e.g., it returned prose before the JSON, or hit max length and got cut off):

- For prose-around-JSON: open the file and trim it to just the JSON block. The validator will accept it.
- For truncation: go back to the Project chat and ask "please continue the JSON from where you cut off." Concatenate the parts, then save. Or split the chapter at `<h2>` and process each section separately.

**Helpful Claude.ai-specific tips for the user:**

- Long chapters may hit the chat's output length cap. If a chapter is >6000 source words, the `prepare-prompts` step will pre-split it into sections (at `<h2>` boundaries) so each fits comfortably. The output files for these will be `chapters/adapted/NN_title.part1.json`, `.part2.json`, etc., and the pipeline auto-concatenates them at the merge step.
- Don't edit the JSON manually — better to ask Claude to regenerate. Any hand-edit risks invalidating the schema.
- The Project remembers the system prompt automatically. If you ever start a chat outside the Project by accident, the output won't follow the rules. The prompt files include the system prompt at the top as a safety fallback for this case.

### 6.4 API mode workflow (alternative for scripted automation outside Claude Code)

Activated with `adapt.mode = "api"` in config. Requires `ANTHROPIC_API_KEY`.

- Uses the `anthropic` Python SDK with `AsyncAnthropic`.
- Uses **prompt caching** on the system prompt (`cache_control: {"type": "ephemeral"}`).
- Includes `book_full_text.md` in the cached system content (paid once per cache window, then ~10% of input cost on subsequent calls — this is why API mode for a 500-page book stays under $10).
- `max_tokens = 8192`. Auto-splits chapters >6000 source words at `<h2>` boundaries.
- Concurrency: up to **4 chapters in parallel** (`asyncio.Semaphore(4)`).
- Tracks cumulative input/output tokens and dollar cost; refuses to exceed `adapt.budget_usd` without `--confirm-budget`.

The same validation and merge logic from agent/chat modes runs on the API output.

### 6.5 CLI summary

```
# Agent mode (default) — Claude Code handles everything
# User just opens the project in Claude Code and says "process the book"
# No specific CLI commands needed; the orchestrator runs them via bash per CLAUDE.md

# Chat mode
audiobook prepare-prompts ./work [--chapters 1-5,8]
audiobook adapt-status ./work
audiobook merge-pronunciation ./work
audiobook validate-adapted ./work        # re-run validation after manual edits

# API mode
audiobook adapt ./work [--chapters 1-5,8] [--budget-usd 10]

# Shared across modes
audiobook validate-adapted ./work        # quality gate; orchestrator uses this in agent mode
```

### 6.6 Resumability

In all three modes, the pipeline skips any chapter whose `chapters/adapted/NN_title.json` (or all of its `.partN.json` files) exists and passes validation. `--force` overrides.

---

## 7. Stage 3 — Chunking

**Module**: `audiobook/chunk.py`
**Entry point**: `chunk_chapter(adapted: ChapterAdapted) -> list[Chunk]`

### Behavior

1. Apply the pronunciation dictionary as a find-and-replace pass on the adapted text. Match whole words only, case-sensitive for acronyms.
2. Segment into sentences with `pysbd` (English).
3. Greedy-pack sentences into chunks. Constraints:
   - Hard max: 400 characters per chunk (Chatterbox optimal range is 100–300; 400 is the ceiling).
   - Never split mid-sentence.
   - Never put a sentence shorter than 20 chars alone — merge with the next.
4. Annotate each chunk with the trailing silence to insert after rendering:
   - End of paragraph: 400 ms
   - End of section (`---` in adapted text, or detected from headings): 1200 ms
   - End of chapter: 2000 ms (handled at assembly stage, not in chunk metadata)
   - Default sentence-end: 0 (let TTS handle the natural pause)
5. Write `chapters/chunks/NN_title.json`:

```json
{
  "index": 7,
  "title": "...",
  "chunks": [
    {"id": "0000", "text": "...", "trailing_silence_ms": 0},
    {"id": "0001", "text": "...", "trailing_silence_ms": 400},
    ...
  ]
}
```

### CLI
```
audiobook chunk ./work
```

---

## 8. Stage 4 — TTS rendering (Chatterbox)

**Module**: `audiobook/render.py`
**Entry point**: `render_chapter(chapter_chunks: ChapterChunks, voice: Voice, config: RenderConfig) -> None`

### Voice setup

The user supplies a reference audio sample of the desired narrator voice. This can be:

- **Their own voice** (the primary use case). Recorded with any decent microphone in a quiet room.
- A licensed voice sample they have rights to use.
- A public-domain voice recording.

**Privacy guarantee**: Chatterbox runs entirely on-device. The reference audio is never sent to Anthropic, never uploaded anywhere, and never leaves the user's machine. Only the adapted book text is sent to the Sonnet 4.6 API.

### Recording a reference sample

- File location: `./voice/reference.wav`
- Duration: **10–15 seconds** (Chatterbox can work with 5s but 10–15s gives noticeably better results for long-form narration)
- Format: WAV, 24 kHz, mono, 16-bit PCM. The pipeline auto-converts from common formats (mp3, m4a, 48 kHz, stereo) on import, but starting clean avoids quality loss.
- Recording conditions:
  - Quiet room, no echo. A closet with hanging clothes works well as an improvised booth.
  - Mouth ~15 cm from the mic. Avoid plosives ("p", "b") going straight into the capsule.
  - No background music, fan noise, typing, or HVAC. Listen back with headphones before accepting.
  - Single speaker, no overlapping voices.
  - 0.3–0.5 s of silence at start and end, no longer (Chatterbox will treat very long silences as part of the voice signature).
- Content: read calmly, in the voice and pace you want the audiobook delivered. Don't perform or exaggerate — read like you're explaining something to a colleague.

**Suggested reference script** (covers a broad phoneme range and matches technical-book delivery style):

> "When we examine the architecture of a distributed system, three concerns dominate: consistency, availability, and partition tolerance. The classical result, known as CAP, states that you can guarantee at most two of these simultaneously. In practice, modern systems make finer-grained trade-offs across different operations, and the choice is rarely as binary as the theorem suggests."

This runs about 18 seconds at a natural pace — record it, then trim to your best 12 seconds.

### Voice validation and preview

Before the full pipeline runs, the user must validate the reference and listen to a sample render:

```
audiobook voice validate ./voice/reference.wav
audiobook voice preview ./voice/reference.wav --text "Optional custom text to preview."
```

`voice validate` checks format, duration, sample rate, channel count, silence boundaries, and signal-to-noise ratio. It reports problems with actionable fixes ("file is 48 kHz stereo, will be downmixed and resampled on use" vs. "audio is clipping at 0:03, please re-record at lower gain").

`voice preview` renders a 30-second sample using a fixed paragraph that exercises varied prosody (questions, lists, technical terms, long sentences). Output: `./voice/preview.wav`. The user listens to this and decides whether to re-record or proceed.

The full pipeline refuses to start rendering (stage 4) unless a successful `voice preview` has been generated for the current reference file (tracked via SHA256 hash in `work/state.json`). Override with `--skip-voice-check`.

### Chatterbox configuration

- Load on `mps` device.
- Use Chatterbox's standard `ChatterboxTTS` model (English, 500M). For multilingual books, switch to `chatterbox-multilingual` via config.
- Cache the encoded voice conditioning so it's computed once per reference, not per chunk. Store in `work/voice_conditioning.pt` keyed by the reference file's SHA256.
- Settings tuned for technical narration:
  - `exaggeration = 0.4` (lower than default; technical content is not dramatic)
  - `cfg_weight = 0.5` (default; balance between fidelity to reference and naturalness)
  - `temperature = 0.7`
- These are configurable per book via `config.toml`.

### Rendering loop

1. For each chunk, render to `audio/chunks/{chapter_index:02d}_{slug}/{chunk_id}.wav`.
2. Output: 24 kHz, mono, 16-bit PCM.
3. Apply the chunk's `trailing_silence_ms` by appending silence to the WAV.
4. Concurrency: render with `concurrent.futures.ThreadPoolExecutor(max_workers=N)` where N is configurable, default 2 on M5. Don't go higher than 4 — VRAM pressure and thermal throttling hurt quality.
5. After each chunk, write a small sidecar `.json` with the chunk's text and config — this enables targeted re-renders.

### Quality validation

Each rendered chunk passes through these checks:
- Duration sanity: no chunk shorter than 0.5 s or longer than (chunk_chars / 10) seconds.
- Silence ratio: reject if >50% of the audio is below -50 dBFS.
- Failed chunks are retried up to 3 times with `temperature += 0.1` each retry. After 3 failures, log the chunk and continue — the user reviews the failure log at the end.

### Resumability

Skip any chunk whose WAV already exists and passes validation. Re-render only the failures.

### CLI
```
audiobook render ./work [--workers 2] [--chapters 1-5]
```

---

## 9. Stage 5 — Assembly

**Module**: `audiobook/assemble.py`
**Entry point**: `assemble_book(work_dir: Path, metadata: BookMetadata, out_path: Path) -> None`

### Behavior

1. For each chapter, concatenate its chunk WAVs in order using ffmpeg's `concat` demuxer. Insert section-break and chapter-end silences here.
2. Concatenate all chapter audio into a single AAC stream.
3. Build an `ffmetadata` file with chapter markers — start time, end time, title per chapter.
4. Mux into an `.m4b` container (audio codec: AAC, 64 kbps mono is plenty for speech; configurable).
5. Embed metadata: title, author, narrator (from `config.toml`; defaults to "Synthetic voice via Chatterbox" but the user can set their own name when narrating personally), publisher, year, genre, description.
6. Embed cover art from `./input/cover.jpg` if present; otherwise extract from the EPUB.
7. Write final file to `out/{slugified_title}.m4b`.

### CLI
```
audiobook assemble ./work --title "..." --author "..." --out ./out/book.m4b
```

---

## 10. End-to-end orchestration

The shape of the end-to-end flow depends on the adapt mode.

### Agent mode (default, recommended)

The user opens the project in Claude Code and gives a single instruction such as:

> Process the book. Follow CLAUDE.md.

Claude Code reads `CLAUDE.md`, verifies setup, then runs every stage (parse → adapt with parallel subagents → chunk → render → assemble) using bash and its Task tool. No further user interaction needed unless something fails. The user comes back to find `./out/book.m4b`.

### Chat mode

```
audiobook run ./input/book.epub --voice ./voice/reference.wav --out ./out/
```

Runs stages 1 and the chat-mode prep, then **stops** with:

```
Prepared 18 chapter prompts in ./work/chapters/prompts/
Paste each into Claude.ai (Project: "Audiobook Adaptation"), save responses to ./work/chapters/adapted/
Then re-run: audiobook run ./input/book.epub  (will resume from chunking)
Track progress: audiobook adapt-status ./work
```

Re-running the same command after manual adaptation finishes the pipeline unattended.

### API mode

```
audiobook run ./input/book.epub --voice ./voice/reference.wav --out ./out/
```

Runs end-to-end without interruption.

---

## 11. Project structure

```
audiobook-pipeline/
├── pyproject.toml
├── README.md
├── SPEC.md                         # this file
├── CLAUDE.md                       # orchestrator instructions for Claude Code (agent mode)
├── config.toml                     # per-book config (TTS settings, mode, budget, chapter filters)
├── prompts/
│   └── adapt_system.md             # the Sonnet 4.6 system prompt (shared by all modes)
├── input/
│   ├── book.epub                   # user-provided EPUB (gitignored)
│   └── cover.jpg                   # optional override cover art (gitignored)
├── audiobook/
│   ├── __init__.py
│   ├── cli.py                      # Typer-based CLI (see §18)
│   ├── parse.py
│   ├── adapt.py                    # API-mode SDK calls; validate/merge utilities used by all modes
│   ├── chunk.py
│   ├── render.py                   # Chatterbox
│   ├── assemble.py                 # ffmpeg
│   ├── models.py                   # Pydantic models for all intermediate schemas
│   └── utils/
│       ├── slugify.py
│       ├── audio.py                # silence, validation, format conversion
│       └── cost.py                 # API cost tracking (API mode only)
├── voice/
│   ├── reference.wav               # user-provided narrator voice sample
│   └── preview.wav                 # generated preview render (gitignored)
├── work/                           # all intermediate artifacts (gitignored)
│   ├── book_full_text.md           # whole-book context for subagents / chat Project / API cache
│   ├── chapters/
│   │   ├── raw/                    # per-chapter parsed JSON
│   │   ├── prompts/                # ready-to-paste chat prompts (chat mode only)
│   │   ├── adapted/                # JSON outputs (subagent-written, manually-saved, or API-written)
│   │   └── chunks/
│   ├── audio/chunks/
│   ├── voice_conditioning.pt       # cached encoded voice (keyed by reference SHA256)
│   └── state.json                  # pipeline progress + cost ledger
├── out/                            # final .m4b output (gitignored)
└── tests/
    ├── fixtures/
    │   └── tiny.epub               # small test EPUB
    ├── test_parse.py
    ├── test_chunk.py
    └── test_assemble.py
```

---

## 12. Configuration

`config.toml` at the project root:

```toml
[book]
title = "..."
author = "..."
narrator = ""  # leave empty for "Synthetic voice via Chatterbox", or set your name when narrating personally
skip_sections = ["copyright", "dedication", "index", "bibliography"]

[adapt]
mode = "agent"               # "agent" (default, Claude Code orchestrates), "chat" (manual paste), or "api" (Anthropic SDK)
model_label = "claude-sonnet-4-6"   # informational
concurrency = 8              # agent mode: subagents in flight at once. api mode: parallel SDK calls (clamped to 4)
split_long_chapters_at_words = 6000  # auto-split at <h2> for chapters longer than this
# API-mode-only settings (ignored in agent and chat modes):
max_tokens_per_call = 8192
budget_usd = 15.0
prompt_cache = true

[chunk]
max_chars = 400
paragraph_silence_ms = 400
section_silence_ms = 1200

[render]
device = "mps"
workers = 2
exaggeration = 0.4
cfg_weight = 0.5
temperature = 0.7
multilingual = false

[assemble]
audio_bitrate_kbps = 64
sample_rate_hz = 24000
```

---

## 13. State & cost tracking

`work/state.json` is updated after every chapter completion:

```json
{
  "epub_sha256": "...",
  "started_at": "2026-05-20T10:00:00Z",
  "adapt_mode": "agent",
  "stages_completed": {
    "parse": true,
    "adapt": {"00": "done", "01": "done", "02": "retry:1", "03": "failed"},
    "chunk": ["00", "01"],
    "render": {"00": "done", "01": "partial:42/87"},
    "assemble": false
  },
  "cost": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cached_input_tokens": 0,
    "usd_total": 0.0,
    "note": "agent mode — no per-token billing; counts against Claude Code subscription limits"
  },
  "voice_reference_sha256": "...",
  "voice_preview_done": true,
  "failures": [
    {"stage": "adapt", "chapter": "03_internals", "reason": "schema validation failed after 2 retries: adapted_text contained markdown table"}
  ]
}
```

In **agent mode**, the `cost` fields stay zero (the note explains why). The orchestrator updates `stages_completed.adapt` per chapter with values like `done`, `retry:N`, or `failed`.

In **chat mode**, the `cost` fields stay zero with the chat-mode note.

In **API mode**, `cost` is populated from real SDK response metadata.

`audiobook status ./work` prints a readable summary including which chapters need attention.

---

## 14. Error handling

- **EPUB parse errors**: fail fast with a clear message naming the bad spine item. Suggest opening the file in Calibre to check validity.
- **Adapted file validation errors**: on ingest, if a JSON file is malformed, the validator prints the chapter name, the line/column of the parse error, and a hint about the most common cause (prose before/after JSON, truncated output, manually edited file). In **agent mode**, the orchestrator passes the validator error back to a retry subagent. In **chat mode**, the error is surfaced to the user. In **API mode**, the call is retried up to 2 times. Other chapters continue processing.
- **Subagent failures (agent mode)**: each chapter gets at most 2 retries. After that the orchestrator logs the failure in `state.json` and continues with remaining chapters. The orchestrator stops the whole pipeline only if >20% of chapters fail in the first wave (indicates a systemic issue, e.g. bad system prompt or malformed EPUB).
- **Anthropic API errors (API mode)**: SDK retries handle transient 429/529. Persistent errors halt the stage with the chapter index logged. User can resume with `--force` after fixing.
- **TTS failures**: as described in §8. Failed chunks are logged but don't halt the pipeline. Final assembly proceeds with a warning if any chunks are missing; the user decides whether to ship as-is or re-render.
- **Budget exceeded (API mode)**: stop processing, write state, exit with non-zero code and a clear message showing tokens used.

---

## 15. Acceptance criteria

The project is considered complete when:

1. End-to-end produces a valid `.m4b` from a real technical EPUB without manual intervention:
   - **Agent mode**: opening the project in Claude Code with `./input/book.epub` and `./voice/reference.wav` present, and giving the instruction "process the book," runs the full pipeline to completion.
   - **API mode**: `audiobook run` exits successfully end-to-end.
   - **Chat mode**: `audiobook run` plus the manual paste step plus a second `audiobook run` produces the final file.
2. The output `.m4b` opens correctly in Apple Books, Bookplayer, and VLC, with all chapter markers navigable.
3. Re-running the same command after deleting `out/` produces the same file in under 60 seconds (everything cached).
4. Re-running after `rm -rf work/audio/` re-renders only audio; preprocessing is not redone.
5. A 500-page book completes end-to-end in under 8 hours wall-clock on an M5 MacBook Pro / 32 GB.
6. Manual listening of a randomly selected 10-minute span sounds like a human narrator, with no read-aloud code, no LaTeX, no mispronounced library names from the pronunciation dictionary.
7. When using a clean 12-second sample of the user's own voice as reference, the rendered narration is recognizably the user — friends/family identify the speaker correctly in a blind listen.
8. **Cost**:
   - In **agent mode** (default): zero per-token billing. The pipeline runs entirely on the user's Claude Pro/Max subscription via Claude Code. A 500-page book consumes a meaningful chunk of subscription rate-limit budget but no money.
   - In **chat mode**: zero per-token billing beyond the user's Claude.ai subscription.
   - In **API mode**: total Sonnet 4.6 cost for a 500-page book is under $10 with prompt caching enabled.

---

## 16. Out-of-scope / future work

Listed here so Claude Code doesn't accidentally build them:

- Web UI / GUI
- Multi-voice character attribution
- Other input formats (PDF, MOBI, AZW3, HTML)
- Cloud deployment / API service
- Streaming output
- Translation
- Background music or sound effects

---

## 17. First-iteration deliverable

The order in which to build the project matters — it protects against expensive late-stage surprises. Build in this order:

1. **Bootstrap and project skeleton.** `uv init`, all dependencies, empty modules per §11, `CLAUDE.md`, `config.toml`, `prompts/adapt_system.md`. Confirm `audiobook --help` runs. No real logic yet.
2. **Stage 1 — Parse.** Implement against the `tests/fixtures/tiny.epub` fixture. Verify it produces `chapters/raw/*.json` and `book_full_text.md`.
3. **Stage 2 validator.** Build `audiobook validate-adapted` against hand-crafted malformed JSON fixtures *before* dispatching any real LLM work. This is the quality gate the orchestrator relies on; it must be trustworthy first.
4. **Stage 2 agent-mode smoke test.** With the tiny EPUB (10–20 pages, 3–5 chapters), open the project in Claude Code and have it run the agent-mode workflow. Confirm: subagents dispatch correctly, write to the right paths, validate cleanly, and the orchestrator stays within its own context budget.
5. **Manual quality review.** Read the adapted text yourself, *aloud*. Iterate on `prompts/adapt_system.md` until the output reads naturally. This is the highest-leverage iteration loop in the whole project — do not skip it.
6. **Stage 3 — Chunk.** Straightforward once stage 2's output is stable.
7. **Stages 4 and 5.** TTS render and assembly. Begin with a one-chapter end-to-end render to validate the chunk → render → assemble path before processing the whole tiny book.
8. **Real book.** Only after the tiny EPUB produces a clean `.m4b` end-to-end, run a real 500-page technical book.

This sequencing protects against the two most expensive failure modes: spending hours on TTS only to discover the adapted text was the bottleneck, and discovering subagent dispatch issues on a 20-chapter book instead of a 3-chapter test.

---

## 18. Language and framework choices

This section is prescriptive. The pipeline is **Python 3.12+** end-to-end. Deviations require a specific reason; the integration tax of mixing languages outweighs any per-stage ergonomic gains, because every meaningful TTS engine, EPUB library, and ML framework ships Python-first.

### Summary

| Concern | Choice |
|---|---|
| Language | Python 3.12+ |
| Package manager | `uv` (Astral) |
| CLI framework | Typer |
| Config format | TOML via stdlib `tomllib` + Pydantic v2 |
| Schema validation | Pydantic v2 (strict mode) |
| EPUB parsing | `ebooklib` + `beautifulsoup4` with `lxml` parser |
| HTML → Markdown | `markdownify` (for `book_full_text.md`) |
| LLM (API mode) | `anthropic` SDK |
| LLM (agent mode) | No SDK; Claude Code calls Python CLI commands via bash |
| Sentence segmentation | `pysbd` |
| TTS | `chatterbox-tts` on `torch` + MPS |
| Audio I/O | `numpy` + `soundfile` |
| Audio assembly | `ffmpeg` binary via `subprocess` |
| `.m4b` tagging | `mutagen` |
| Chapter markers | `mp4chaps` (from `mp4v2`, brew-installable) |
| Terminal UI | `rich` |
| Logging | stdlib `logging` (file) + `rich` (terminal) |
| Test runner | `pytest` + `pytest-asyncio` |
| Lint + format | `ruff` |
| Type checker | `mypy --strict` |

### Stage-by-stage notes

**Stage 1 — Parse**
- `ebooklib` reads EPUB structure (spine, nav doc, ToC, metadata, cover).
- `beautifulsoup4` with the `lxml` parser handles XHTML. `lxml` is 5–10× faster than the stdlib `html.parser` and tolerates malformed markup.
- `markdownify` converts HTML to Markdown when emitting `work/book_full_text.md`.

**Stage 2 — Adapt**
- API mode: `anthropic` SDK with async client and prompt caching.
- Agent mode: no SDK. Claude Code invokes Python CLI commands (`audiobook validate-adapted`, `audiobook merge-pronunciation`) via bash. The orchestration logic lives in `CLAUDE.md`, not in Python.
- Chat mode: Python generates prompt files; the user handles transport.
- All modes share the same Pydantic v2 schemas in `audiobook/models.py`. Pydantic v2's strict mode catches schema drift early and produces error messages that can be fed directly back to a retry subagent.

**Stage 3 — Chunk**
- `pysbd` for sentence segmentation. Rule-based, handles abbreviations ("Dr. Smith", "e.g.", "i.e.", "U.S.") better than NLTK Punkt and far faster than spaCy.
- Standard `re` for the pronunciation dictionary find-and-replace.

**Stage 4 — Render**
- `chatterbox-tts` directly. Auto-detects MPS on Apple Silicon.
- `torch` with MPS device — already a transitive dependency.
- `concurrent.futures.ThreadPoolExecutor` for parallel chunk rendering. **Threads, not processes** — model loading is expensive and the GPU is shared, so spinning up multiple processes wastes memory and yields no speedup.
- `numpy` + `soundfile` for the silence-padding step and chunk WAV writes.

**Stage 5 — Assemble**
- Don't attempt audio assembly in pure Python. `pydub` and `pyav` produce subtle codec and container bugs in `.m4b` output. Shell out to the `ffmpeg` binary instead.
- `mutagen` for final tag writing (title, author, narrator, cover art).
- `mp4chaps` for chapter markers — more reliable than ffmpeg's `ffmetadata` approach for the `.m4b` container.

### CLI: Typer over Click

Typer is preferred because:
- Argument types come from Python type hints, so the CLI surface and the internal Pydantic models stay in sync automatically.
- Less boilerplate for the ~15 commands the pipeline exposes.
- Self-generating `--help`.

Click is acceptable if a contributor strongly prefers it; the surface stays roughly equivalent.

### Environment with `uv`

`uv` handles the Python interpreter, virtualenv, dependency resolution, and lockfile. Roughly 10× faster than pip + venv and removes the need for pyenv.

```
uv init audiobook-pipeline
uv add ebooklib beautifulsoup4 lxml anthropic pydantic typer pysbd \
       chatterbox-tts torch mutagen markdownify rich soundfile numpy
uv add --dev pytest pytest-asyncio ruff mypy
uv run audiobook parse ./input/book.epub --out ./work
uv tool install .   # optional: expose `audiobook` globally
```

### Configuration: TOML + Pydantic, nothing else

`config.toml` is parsed by stdlib `tomllib` into a Pydantic v2 model. No YAML, no JSON, no `.env`. TOML's syntax is unambiguous, comments are first-class, and the format already exists in `pyproject.toml`. Pydantic v2 gives validated, typed config objects.

### Testing

- `pytest` + `pytest-asyncio`.
- Fixture EPUB (~5 chapters: prose, code, equations, a table, a figure) at `tests/fixtures/tiny.epub`.
- **For Stage 2, test the validator, not the LLM.** Hand-crafted malformed JSON files (truncated, prose-wrapped, schema-mismatched, length-anomalous) verify that the validation gate correctly rejects bad outputs. The LLM behavior itself is out of scope for unit tests.
- **For Stage 4, test chunking and silence-padding logic with fixed inputs.** Skip integration tests for the actual TTS render — too slow and too non-deterministic for CI.
- `mypy --strict` covers the schema-mismatch class of bugs at type-check time.

### Code quality

- `ruff` replaces black + isort + flake8. Fast enough to run on save.
- `mypy --strict`. The Pydantic models make this nearly free.
- `pre-commit` hooks for both. Configure once.

### What not to introduce

- **No async outside the API-mode adapter.** Parse, chunk, and assemble are disk-bound and trivially synchronous; render is GPU-bound and uses threads. Making them `async def` adds complexity for zero benefit.
- **No web framework.** `audiobook status ./work` as a CLI command covers the entire status-reporting need. FastAPI/Flask are irrelevant here.
- **No database.** State is JSON on disk. SQLite is tempting and unnecessary at this scale.
- **No Pydantic v1.** Use v2 from day one. The strict-by-default behavior and improved error formatting are genuinely better for this use case.
- **No custom logging library.** stdlib `logging` + `rich.console` covers everything.
- **No hand-written native extensions.** All speed-sensitive paths go through TTS (already optimized) or ffmpeg (already optimized).
- **No `multiprocessing` for TTS.** Threads share the loaded model and GPU; processes don't. Use `ThreadPoolExecutor`.