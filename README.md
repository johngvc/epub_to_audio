# epub_to_audio

Local-first pipeline that turns an **EPUB or PDF into a tagged `.m4b` audiobook** with chapter markers — no cloud, no API cost. Narration is on-device via Chatterbox TTS (Apple Silicon MPS); content adaptation runs against either a local OpenAI-compatible LLM (LM Studio) or Claude Code subagents.

See `epub_to_audio_spec.md` for the full spec, `docs/superpowers/specs/` for design decisions, and `docs/superpowers/plans/` for implementation plans.

## How it runs

| Where | Stages |
|---|---|
| **Docker** (`audiobook:dev`) | parse, validate-adapted, merge-pronunciation, chunk, validate-render, assemble, status, `voice validate` |
| **Host** (`uv` venv, needs MPS / LM Studio) | render (TTS), api-mode adapt, `lms-load`/`lms-unload`, `voice` save/list/rm/preview |

`bin/audiobook` routes every subcommand automatically — you never type `docker compose` or `uv run`.

## Prerequisites

macOS host:

- [colima](https://github.com/abiosoft/colima) + Docker CLI — `brew install colima docker docker-compose`
- [uv](https://github.com/astral-sh/uv) — `brew install uv`
- One of, for Stage 2 (adapt):
  - **api mode** (unattended, default): [LM Studio](https://lmstudio.ai/) with a capable model loaded — see [Picking a model](#picking-a-model).
  - **agent mode** (interactive): [Claude Code](https://docs.claude.com/en/docs/claude-code) on a Pro/Max plan — see [Agent mode](#agent-mode).
- (Optional) tmux — `bin/dev` uses it if present.

## Quick start

The one-command happy path, api mode:

1. **Install** (one-time):
   ```sh
   brew install colima docker docker-compose uv
   bin/dev                  # start Colima, build the audiobook:dev image
   scripts/host-install.sh  # create .venv (torch + chatterbox + openai)
   ```

2. **Add inputs:**
   ```
   input/book.epub   (or input/book.pdf)   # the book
   voice/reference.wav                      # 10-15s mono WAV of the narrator
   ```
   Not a WAV? Convert (e.g. Voice Memos `.m4a`):
   ```sh
   afconvert -f WAVE -d LEI16@24000 -c 1 voice/input.m4a voice/reference.wav
   ```

3. **Edit `config.toml`** — set metadata and confirm offline mode (full key list under [Configuration](#configuration)):
   ```toml
   [book]
   title  = "Your Book Title"
   author = "The Author"

   [adapt]
   mode = "api"                      # "api" = local LLM, unattended; "agent" = Claude Code

   [adapt.api]
   base_url = "http://localhost:1234/v1"
   model    = "qwen3.6-35b-a3b-mtp"  # the model id loaded in LM Studio
   ```

4. **Start LM Studio**, load that model, and confirm its server is up at the base URL.

5. **Check the voice** (cheap — do this before the multi-hour render):
   ```sh
   bin/audiobook voice validate ./voice/reference.wav
   bin/audiobook voice preview  ./voice/reference.wav
   ```
   Listen to `voice/preview.wav`; re-record if it sounds wrong.

6. **Run everything:**
   ```sh
   bin/audiobook run
   ```
   Defaults: `input/book.epub` (then `input/book.pdf`) → `out/book.m4b`, work dir `./work`, config `./config.toml`. Override with `--out`, `--work`, `--config`, `--voice`. Use `--fresh` to wipe `work/` first, `--skip-preflight` to bypass dependency checks.

`run` auto-installs prerequisites (Colima, Docker image, host venv), runs all 8 stages in order, and aborts on the first failure with a resume hint. Render is ~2-4h on Apple Silicon. **Every stage is idempotent** — re-run to resume after an interruption.

## Input formats

**EPUB** (default) and **PDF**. Drop the file at `input/book.epub` or `input/book.pdf`, or pass a path to `run`/`parse`. PDF knobs live in `config.toml`'s `[parse]` block and are overridable per run:

```sh
bin/audiobook parse ./input/book.pdf --parser auto --footnote-policy skip --chapter-level 1
```

**Chapter splitting (PDF).** Chapters come from the PDF's **embedded outline (bookmarks)** when present — the reliable path for code-heavy books, where literal `#` code comments would otherwise be misread as Markdown headings and shatter the book into bogus sections. Without an outline it falls back to Markdown headings (H1, else H2).

| Flag | Values | Default / notes |
|---|---|---|
| `--parser` | `auto` \| `pymupdf` \| `marker` | `auto` extracts via pymupdf4llm and warns on low-quality signals. `marker` is deferred and currently errors. |
| `--footnote-policy` | `inline` \| `endnote` \| `skip` | `skip` |
| `--chapter-level` | `1`-`6` | Forces a heading level and **bypasses the outline**. Unset: outline if present, else H1, else H2. |

**Not supported:** scanned/image-only PDFs (no OCR — fails clearly) and encrypted PDFs (decrypt first, e.g. `qpdf`). EPUB ignores all `[parse]` options.

## Manual stages

To drive the pipeline stage by stage (same order as `run`):

```sh
bin/audiobook parse ./input/book.epub --out ./work   # 1  Docker
bin/audiobook adapt ./work                            # 2  Host  (api mode only)
bin/audiobook validate-adapted ./work                 # 3  Docker (gate: exit 0/1)
bin/audiobook merge-pronunciation ./work              # 4  Docker
bin/audiobook chunk ./work                            # 5  Docker
bin/audiobook render ./work --voice default           # 6  Host (MPS)
bin/audiobook validate-render ./work                  # 7  Docker (gate: exit 0/1)
bin/audiobook assemble ./work --out ./out/book.m4b    # 8  Docker
```

`assemble` reads `title`/`author`/`narrator` from `[book]`; pass `--title`/`--author`/`--narrator` to override, `--cover PATH` to embed art. Every stage skips chapters/chunks whose outputs already exist and pass validation, so re-running resumes; `adapt` re-runs only the missing/invalid chapters.

**Progress:** add `-v` / `--verbose` to `parse`, `adapt`, `chunk`, `render`, or `assemble` for per-step lines with completion percentages (e.g. `[render] 152/387 (39%)`). Default output is unchanged.

## Iterating on quality

If a chapter sounds off, before re-rendering:

1. Inspect `work/chapters/adapted/NN_*.json` — the exact text Chatterbox will speak.
2. Inspect `work/pronunciation.json` — substitutions applied to every chunk.
3. Edit either by hand, **or** delete the file and re-run the prior stage.

Because stages are idempotent, only the touched chapters/chunks are redone.

## Voices

Saved voices live in `voices/<name>.wav` (24 kHz mono PCM); raw recordings can stay in `voice/`.

| Command | What it does |
|---|---|
| `bin/audiobook voice save SAMPLE --name NAME` | Convert a raw sample to 24 kHz mono PCM, save as `voices/NAME.wav`. `--force` to overwrite, `--preview` to also write a sample. |
| `bin/audiobook voice list` | List saved voices (duration / sample rate / size); marks the default pick with `*`. |
| `bin/audiobook voice rm NAME` | Remove a voice (`--force` skips the confirm). |
| `bin/audiobook voice preview [REF] --voice NAME` | Render a short MPS preview to tune accent/params. |
| `bin/audiobook voice validate PATH` | Check a WAV's format / duration / SNR / clipping. |

Pick a voice with `bin/audiobook run --voice grandpa` or pin `[render].voice = "grandpa"`.

**Resolution order:** `--voice` arg → `[render].voice` → `voices/default.wav` → `voice/reference.wav` (legacy).

## Configuration

All knobs live in `config.toml`. The most useful (defaults shown are the values in the shipped `config.toml`):

| Key | Purpose | Notes |
|---|---|---|
| `[book].title`, `.author`, `.narrator` | `.m4b` metadata | title/author required for `assemble` unless overridden on CLI |
| `[book].skip_sections` | Chapter titles dropped at parse | Default: copyright/dedication/index/bibliography |
| `[adapt].mode` | `"api"` or `"agent"` | `"api"` default; needs LM Studio running |
| `[adapt].concurrency` | Parallel subagents in agent mode | Default 8; ignored in api mode (sequential) |
| `[adapt.api].base_url` | OpenAI-compatible endpoint | Default `http://localhost:1234/v1` |
| `[adapt.api].model` | Model id loaded in LM Studio | See [Picking a model](#picking-a-model) |
| `[adapt.api].context_window` | Decides whether `book_full_text.md` is included | Default 131072; match what LM Studio actually loaded |
| `[adapt.api].max_output_tokens` | Caps one response (incl. reasoning) | Default 24576; must exceed a chapter's adapted length — see [Large chapters](#large-chapters) |
| `[adapt.api].temperature`, `.request_timeout_s` | LLM creativity / per-chapter timeout | Defaults 0.3, 600s |
| `[adapt.api].ttl_seconds` | Idle seconds before LM Studio auto-unloads (`ttl` per request) | Default 300; 0 = stay loaded |
| `[adapt.api].manage_model` | `run` loads before adapt, unloads before render | Default true; host-only, needs `lms` |
| `[adapt.api].load_context_length` | Load context (`lms load -c`) | Unset = LM Studio default; lower = much less KV-cache RAM |
| `[chunk].max_chars` | TTS chunk size | 400 is stable |
| `[chunk].paragraph_silence_ms` / `.section_silence_ms` | Pauses between paragraphs / `---` breaks | |
| `[render].engine` | TTS engine: `chatterbox` or `kokoro` | Default `chatterbox`; see [TTS engines](#tts-engines) |
| `[render].device`, `.workers` | `mps`/`cuda`/`cpu`; parallel TTS workers | Apple Silicon = `mps`; 1-2 workers per GPU |
| `[render].exaggeration`, `.cfg_weight`, `.temperature` | Chatterbox voice knobs | Tuned for spoken word |
| `[render].kokoro_voice`, `.kokoro_speed` | Kokoro built-in voice + speed | e.g. `af_heart`, `bm_george`; speed 1.0 |
| `[assemble].audio_bitrate_kbps` | AAC bitrate | 64 kbps fine for speech |
| `[parse].parser`, `.footnote_policy`, `.chapter_level` | PDF ingestion | EPUB ignores these |

**Env-var overrides** for `[adapt.api]` (only when set and non-empty): `OPENAI_BASE_URL` → `base_url`, `OPENAI_MODEL` → `model`, `OPENAI_API_KEY` → `api_key`.

## TTS engines

Stage 4 supports two engines, selected by `[render].engine` or `render --engine`:

- **chatterbox** (default) — clones a narrator voice from a reference WAV (the voice
  library / `--voice` WAV). Runs on MPS.
- **kokoro** — fast 82M model with ~50 fixed built-in voices (no cloning). Pick one
  with `[render].kokoro_voice` or `--voice` (e.g. `bm_george`, `af_heart`). Install
  the extra: `uv pip install -e '.[kokoro]'`. On Apple Silicon it runs on **CPU**
  (its iSTFT op isn't implemented on MPS) — still ~8× realtime.

```sh
bin/audiobook render ./work --engine kokoro --voice bm_george
```

## Controlling RAM

On one Apple Silicon machine, an LLM and Chatterbox cannot share RAM during render — this is the make-or-break operational detail. Adapt and render are separate stages, so the LLM should not be resident during render.

With `[adapt.api].manage_model = true` (default), `run` **loads the model before adapt and unloads it before render** via the `lms` CLI (host-only; no-op if `lms` is absent). Each adapt request also carries a `ttl` (`ttl_seconds`, default 300) so the model self-unloads when idle between stage-by-stage runs. In smoke tests, freeing the LLM before render reliably recovers the RAM the model held.

Manual control:

```sh
bin/audiobook lms-load --context-length 32768   # load configured model, small KV cache
bin/audiobook lms-unload                         # free all loaded models
```

**`load_context_length` / `lms load -c` is the single biggest RAM lever** — the KV-cache reservation scales with context, so loading at a smaller context dramatically cuts memory.

## Large chapters

api-mode adapt sends each chapter in **one call**, so a chapter must fit alongside its similarly-sized adapted output. A ~9,500-word chapter is ~13k tokens in and ~13k out. It needs a model loaded with a large context **and** a matching `max_output_tokens`, or the response truncates mid-JSON (`json_parse_error`) or comes back empty (`length_anomaly`). Defaults assume a large-context model (`context_window = 131072`, `max_output_tokens = 24576`); load LM Studio with a generous context to match. Oversized chapters must be split manually (not yet automated).

## Picking a model

Benchmarked on a stress chapter mixing proper names, foreign places, technical terms, code, equations, tables, and lists:

| Model | Recall | Format quality | Speed/chapter | Verdict |
|---|---|---|---|---|
| **qwen3.6-35b-a3b-mtp** (3-bit) | excellent (15/16 hard terms) | best — clean equations, no hallucinations | ~25s | **Recommended** |
| qwen3.6-35b-a3b-ud-mlx (4-bit) | excellent | slightly cleaner; rare `...→...` placeholder regression | ~25s | Strong alternative |
| gemma-4-26b-a4b-it | strong on tech | leans on dashes (handled by sanitizer) | ~7-25s | Solid runner-up |
| qwen3.5-9b-mlx | good with `forcing` rule | heavy reliance on the sanitizer | ~5-15s | Workable |
| qwen/qwen3.6-27b | comparable | comparable | ~50s | Too slow (dense 27B) |
| openai/gpt-oss-20b | **broken** | emits literal `"..."` placeholders | ~2s | **Do not use** |

A `sanitize_spoken_as` post-processor normalizes dashes and stress marks (e.g. `MEER-ah` → `meer ah`) before substitution, so even format-leaning models stay TTS-clean. Pure ALL-CAPS acronyms (`MIT`) are preserved.

## Agent mode

To have Claude Code drive Stage 2 via subagents (no local LLM, uses your Pro/Max subscription):

```toml
[adapt]
mode = "agent"
```

Open Claude Code from the project root and say `> process the book`. It reads `CLAUDE.md` and runs the pipeline interactively, dispatching one subagent per chapter (parallelism per `[adapt].concurrency`). Note: `bin/audiobook run` aborts in preflight when `mode = "agent"` — use the interactive workflow instead.

## Command reference

```sh
bin/audiobook run [INPUT]                            # All 8 stages, auto-install, strict failure
bin/audiobook parse INPUT --out ./work               # Docker — Stage 1 (.epub or .pdf)
bin/audiobook adapt ./work                           # Host  — Stage 2 (api mode)
bin/audiobook validate-adapted ./work                # Docker — Stage 2 gate
bin/audiobook merge-pronunciation ./work             # Docker
bin/audiobook chunk ./work                           # Docker — Stage 3
bin/audiobook render ./work --voice NAME             # Host (MPS) — Stage 4
bin/audiobook validate-render ./work                 # Docker — Stage 4 gate
bin/audiobook assemble ./work --out ./out/book.m4b   # Docker — Stage 5
bin/audiobook status ./work                          # Read work/state.json
bin/audiobook voice save|list|rm|preview|validate    # Voice library (see Voices)
bin/audiobook lms-load [--context-length N]          # Host — load configured LLM
bin/audiobook lms-unload                             # Host — unload all LLMs to free RAM
```

Add `-v` / `--verbose` to `parse`, `adapt`, `chunk`, `render`, or `assemble` for per-step progress.

## Development

```sh
bin/audiobook-test            # pytest in Docker
bin/audiobook-test -k parse   # subset
.venv/bin/python -m pytest    # host-side (tests needing the openai SDK)
docker compose build          # rebuild image (only when pyproject.toml changes)
```

Source edits are picked up via the bind mount — no rebuild for `.py` changes.

## Project layout

```
audiobook/                Python package (CLI + each stage)
prompts/                  Adaptation system prompt (rule 8 = pronunciation guidance)
tests/                    pytest suite + tiny.epub fixture
scripts/                  Host install + helpers (make_test_epub.py)
bin/                      dev launcher + audiobook/audiobook-test wrappers
docs/superpowers/         Design docs + implementation plans
input/ voice/ voices/ work/ out/   Pipeline I/O + voice library (gitignored)
scratch/                  Throwaway probes (gitignored)
```
