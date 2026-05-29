# epub_to_audio

Local-first pipeline that converts EPUB books into `.m4b` audiobooks. Uses Chatterbox TTS for narration (on-device, Apple Silicon MPS) and either a local OpenAI-compatible LLM (LM Studio) or Claude Code subagents for content adaptation.

See `epub_to_audio_spec.md` for the full spec, `docs/superpowers/specs/` for design decisions, and `docs/superpowers/plans/` for implementation plans.

## How it runs

- **One Docker container** (`audiobook:dev`) handles parse, validation, chunk, and assemble.
- **The host** runs Stage 4 (TTS) so it can use Apple Silicon's MPS GPU, plus either Claude Code or the local LM Studio HTTP API for Stage 2.
- **One wrapper**, `bin/audiobook`, routes each subcommand to the right place automatically.

You never need to type `docker compose` or `uv run` directly.

## Quick start (fully offline)

EPUB in → `.m4b` out, no cloud. Everything runs locally: Docker, a local LLM (LM Studio), and on-device TTS.

1. **Install** (macOS, one-time):
   ```sh
   brew install colima docker docker-compose uv   # runtime
   # + install LM Studio: https://lmstudio.ai/
   bin/dev                  # start Colima, build the Docker image
   scripts/host-install.sh  # create .venv (torch + chatterbox + openai)
   ```

2. **Add your inputs:**
   ```
   input/book.epub       # the book
   voice/reference.wav   # 10–15s mono WAV of the narrator voice
   ```
   Not a WAV? Convert (e.g. from Voice Memos `.m4a`):
   ```sh
   afconvert -f WAVE -d LEI16@24000 -c 1 voice/input.m4a voice/reference.wav
   ```

3. **Edit `config.toml`** — set the book metadata and confirm offline mode:
   ```toml
   [book]
   title  = "Your Book Title"
   author = "The Author"

   [adapt]
   mode = "api"                      # fully offline (local LLM)

   [adapt.api]
   base_url = "http://localhost:1234/v1"
   model    = "qwen3.6-35b-a3b-mtp"  # the model id loaded in LM Studio
   ```

4. **Start LM Studio**, load that model, and confirm its local server is running at `http://localhost:1234`.

5. **Check the voice** (cheap; do this before the multi-hour render):
   ```sh
   bin/audiobook voice validate ./voice/reference.wav
   bin/audiobook voice preview  ./voice/reference.wav
   ```
   Listen to `voice/preview.wav`. Re-record if it sounds wrong.

6. **Run everything:**
   ```sh
   bin/audiobook run
   ```
   Runs all stages (parse → adapt → chunk → render → assemble) and writes `out/book.m4b`. Render takes ~2–4h on Apple Silicon. Every stage is idempotent — re-run `bin/audiobook run` to resume after an interruption.

That's it. For per-stage control, multiple voices, model choice, and every config knob, read on.

## Prerequisites

On the host (macOS):

- [colima](https://github.com/abiosoft/colima) + Docker CLI — `brew install colima docker docker-compose`
- [uv](https://github.com/astral-sh/uv) — `brew install uv` (required for Stage 4 / TTS and for api-mode adapt)
- One of (for Stage 2 adapt):
  - **API mode (recommended for unattended runs)**: [LM Studio](https://lmstudio.ai/) with a capable model loaded — see "Picking a model" below
  - **Agent mode (interactive)**: [Claude Code](https://docs.claude.com/en/docs/claude-code) logged in with a Pro or Max subscription
- (Optional) tmux — `bin/dev` uses it if present.

## First-time setup

```sh
bin/dev                  # starts Colima, builds the Docker image, opens tmux
scripts/host-install.sh  # creates .venv with torch + chatterbox + openai (api mode)
```

---

## How to use

End-to-end run from a fresh checkout, in **API mode** (the unattended path):

### 1. Edit `config.toml`

Open `config.toml`. The fields marked `TWEAK` are the ones you almost always change:

```toml
[book]
title  = "Your Book Title"        # embedded in the final .m4b metadata
author = "The Author"
narrator = ""                     # optional credit

[adapt]
mode = "api"                      # "api" = unattended; "agent" = Claude Code drives

[adapt.api]
base_url = "http://localhost:1234/v1"
model    = "qwen3.6-35b-a3b-mtp"  # whichever model you have loaded in LM Studio
```

Everything else has sensible defaults. Full key-by-key reference is at the bottom of this section.

### 2. Drop your inputs in place

```
input/book.epub          # your EPUB
voice/reference.wav      # 10-15s mono WAV of the narrator voice (any audio format also works)
```

If your recording is in m4a (Voice Memos) or another format, convert:

```sh
afconvert -f WAVE -d LEI16@24000 -c 1 voice/JohnVoiceRecording.m4a voice/reference.wav
```

### 3. Start LM Studio and load the model

Launch LM Studio, load the model named in `[adapt.api].model`, and confirm the local server is running at the base URL (default `http://localhost:1234`).

### 4. Validate the voice reference

```sh
bin/audiobook voice validate ./voice/reference.wav
bin/audiobook voice preview  ./voice/reference.wav
```

Listen to `voice/preview.wav`. If it sounds wrong, re-record before spending hours on render.

### 5. Run the pipeline

**One command:**

```sh
bin/audiobook run
```

Defaults: reads `./input/book.epub`, writes `./out/book.m4b`, uses `./work` as the work directory, reads `./config.toml`. Override any of these with `--out`, `--work`, `--config`, `--voice`. Pass `--fresh` to wipe the work directory first, or `--skip-preflight` to bypass dependency checks.

The orchestrator auto-installs anything it needs: starts Colima on macOS, builds the Docker image, creates the host venv, installs the `[api]` extra. It then runs all 8 stages in sequence (parse → adapt → validate-adapted → merge-pronunciation → chunk → render → validate-render → assemble), aborting on the first failure with a hint to resume. Re-running picks up where the previous run stopped (every stage is idempotent).

**Or run each stage manually:**

```sh
bin/audiobook parse ./input/book.epub --out ./work
bin/audiobook adapt ./work
bin/audiobook validate-adapted ./work
bin/audiobook merge-pronunciation ./work
bin/audiobook chunk ./work
bin/audiobook render ./work --voice default
bin/audiobook validate-render ./work
bin/audiobook assemble ./work --out ./out/book.m4b
```

`title`/`author` for `assemble` come from `config.toml`'s `[book]` block; pass `--title`/`--author` to override per-run.

### 6. Iterating on quality

If a chapter sounds off when read aloud, before re-rendering:

1. Inspect `work/chapters/adapted/NN_*.json` (the text Chatterbox will speak).
2. Inspect `work/pronunciation.json` (substitutions applied to every chunk).
3. Edit either by hand, OR delete the file and re-run the prior stage.

The pipeline is idempotent — every stage skips chapters/chunks whose outputs already exist and pass validation. `bin/audiobook adapt ./work` re-runs only the missing/invalid chapters.

### Working with voices

Saved voices live in `voices/<name>.wav` (24 kHz mono PCM). Raw recordings can stay in `voice/` — they're separate from the curated library.

| Command | What it does |
|---|---|
| `bin/audiobook voice save SAMPLE --name NAME` | Converts a raw sample to 24 kHz mono PCM and saves it as `voices/NAME.wav`. Add `--force` to overwrite, `--preview` to also generate a sample audio file. |
| `bin/audiobook voice list` | Lists all saved voices with duration / sample rate / size. The one that `audiobook run` would pick by default is marked with `*`. |
| `bin/audiobook voice rm NAME` | Deletes a saved voice. |
| `bin/audiobook voice preview --voice NAME` | Generates a 30-second preview using a saved voice. Also accepts a path. |
| `bin/audiobook voice validate PATH` | Checks an arbitrary audio file's format/duration/SNR/clipping. |

**Picking a voice for a run:**

```sh
bin/audiobook run --voice grandpa             # uses voices/grandpa.wav
```

Or pin a default in `config.toml`:

```toml
[render]
voice = "grandpa"
```

Resolution order: `--voice` arg → `[render].voice` config → `voices/default.wav` → `voice/reference.wav` (legacy).

### Configuration reference

All knobs live in `config.toml`. The most useful ones:

| Key | Purpose | Notes |
|---|---|---|
| `[book].title`, `.author`, `.narrator` | Metadata embedded in the `.m4b` | Required for `assemble` unless overridden on CLI |
| `[book].skip_sections` | Chapter titles (case-insensitive) dropped at parse | Default: copyright/dedication/index/bibliography |
| `[adapt].mode` | `"api"` (unattended) or `"agent"` (Claude Code subagents) | `"api"` is the default; needs LM Studio running |
| `[adapt].concurrency` | Parallel subagents in `agent` mode | Default 8; ignored in api mode (sequential) |
| `[adapt.api].base_url` | OpenAI-compatible endpoint | Default LM Studio's `http://localhost:1234/v1` |
| `[adapt.api].model` | Model id as loaded in LM Studio | See "Picking a model" below |
| `[adapt.api].api_key` | Sent to satisfy the SDK; LM Studio ignores it | Leave as `"lm-studio"` |
| `[adapt.api].context_window` | Used to decide whether to include `book_full_text.md` | Set to your model's actual window |
| `[adapt.api].temperature` | LLM creativity | Default 0.3 (strict JSON adherence) |
| `[adapt.api].max_output_tokens` | Caps a single response | Default 8192 |
| `[adapt.api].request_timeout_s` | Per-chapter timeout | Default 600s; raise for very large chapters on slow GPUs |
| `[chunk].max_chars` | TTS chunk size in characters | 400 is stable; smaller = more chunks but more reliable |
| `[chunk].paragraph_silence_ms` / `.section_silence_ms` | Pause durations between paragraphs / `---` section breaks | |
| `[render].device` | `"mps"`/`"cuda"`/`"cpu"` | Apple Silicon = `mps`; NVIDIA = `cuda` |
| `[render].workers` | Parallel TTS workers | 1-2 typical for a single GPU |
| `[render].exaggeration`, `.cfg_weight`, `.temperature` | Chatterbox voice knobs | Defaults are tuned for spoken word |
| `[assemble].audio_bitrate_kbps` | AAC bitrate in the output `.m4b` | 64 kbps is fine for speech |

Env-var overrides for `[adapt.api]` (useful for not committing secrets):

- `OPENAI_BASE_URL` → `[adapt.api].base_url`
- `OPENAI_MODEL` → `[adapt.api].model`
- `OPENAI_API_KEY` → `[adapt.api].api_key`

Env vars only override when set and non-empty.

### Picking a model (for `[adapt.api].model`)

We benchmarked several local models on a stress chapter mixing proper names, foreign places, technical terms, code blocks, equations, tables, and lists. Findings:

| Model | Recall | Format quality | Speed (per chapter) | Verdict |
|---|---|---|---|---|
| **qwen3.6-35b-a3b-mtp** (3-bit) | excellent (15/16 hard terms) | best — clean equations, no hallucinations | ~25s | **Recommended** |
| qwen3.6-35b-a3b-ud-mlx (4-bit) | excellent | slightly cleaner format, very occasional `...→...` placeholder regression | ~25s | Strong alternative |
| gemma-4-26b-a4b-it | strong on tech | uses dashes more (handled by sanitizer) | ~7s/short, ~25s/stress | Solid runner-up |
| qwen3.5-9b-mlx | good with `forcing` rule | format-heavy reliance on the sanitizer | ~5-15s | Workable but not ideal |
| qwen/qwen3.6-27b | comparable recall | comparable format | ~50s | Too slow (dense 27B) |
| openai/gpt-oss-20b | **broken** | emits literal `"..."` placeholders | ~2s | Do not use |

The pipeline applies a `sanitize_spoken_as` post-processor that normalizes dashes and stress marks (e.g. `MEER-ah` → `meer ah`, `BYAR-neh stroo-stroop` → `byar neh stroo stroop`) before substitution, so even models that lean on those patterns produce TTS-clean output. Pure ALL-CAPS acronyms (`MIT`) are preserved.

### Agent mode (interactive, Claude Code subagents)

If you'd rather have Claude Code drive Stage 2 via subagents (no local LLM needed):

```toml
[adapt]
mode = "agent"
```

Then open Claude Code from the project root and say:

```
> process the book
```

Claude Code reads `CLAUDE.md` and runs the pipeline interactively, dispatching one subagent per chapter (parallelism per `[adapt].concurrency`). Uses your Pro/Max subscription, no API key needed.

---

## Direct command reference

```sh
bin/audiobook run [INPUT_EPUB]                        # All stages, auto-install, strict failure
bin/audiobook parse INPUT.epub --out ./work           # Docker — Stage 1
bin/audiobook adapt ./work                            # Host  — Stage 2 (api mode)
bin/audiobook validate-adapted ./work                 # Docker — Stage 2 gate
bin/audiobook merge-pronunciation ./work              # Docker
bin/audiobook chunk ./work                            # Docker — Stage 3
bin/audiobook voice save SAMPLE --name NAME           # Host — add a voice to the library
bin/audiobook voice list                              # Host — list saved voices
bin/audiobook voice rm NAME                           # Host — remove a saved voice
bin/audiobook voice validate ./voice/reference.wav    # Docker — check format/SNR
bin/audiobook voice preview --voice NAME              # Host (MPS) — preview a saved voice
bin/audiobook render ./work --voice NAME              # Host (MPS) — Stage 4
bin/audiobook validate-render ./work                  # Docker — per-chunk WAV check
bin/audiobook assemble ./work --out ./out/book.m4b    # Docker — Stage 5
bin/audiobook status ./work                           # Read work/state.json
```

## Development

```sh
bin/audiobook-test            # Run pytest in Docker
bin/audiobook-test -k parse   # Run a subset
.venv/bin/python -m pytest    # Run host-side (for tests that need openai SDK)
docker compose build          # Rebuild image (only needed when pyproject.toml changes)
```

Source edits are picked up automatically via the bind mount — no rebuild for `.py` changes.

## Project layout

```
audiobook/                Python package (CLI + each stage)
prompts/                  Adaptation system prompt (rule 8 = pronunciation guidance)
tests/                    pytest suite + tiny.epub fixture
scripts/                  Host install + helpers (make_test_epub.py for smoke testing)
bin/                      dev launcher + audiobook/audiobook-test wrappers
docs/superpowers/         Design docs + implementation plans
input/ voice/ work/ out/  Pipeline I/O (gitignored)
scratch/                  Throwaway probes (gitignored)
```
