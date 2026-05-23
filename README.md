# epub_to_audio

Local-first pipeline that converts technical EPUB books into `.m4b` audiobooks using Claude Sonnet 4.6 for content adaptation and Chatterbox TTS for narration.

See `epub_to_audio_spec.md` for the full spec, `docs/superpowers/specs/` for design decisions, and `docs/superpowers/plans/` for the implementation plan.

## How it runs

- **One Docker container** (`audiobook:dev`) handles parse, validation, chunk, and assemble.
- **The host** runs Stage 4 (TTS) so it can use Apple Silicon's MPS GPU, plus Claude Code itself (the orchestrator).
- **One wrapper**, `bin/audiobook`, routes each subcommand to the right place automatically.

You never need to type `docker compose` or `uv run` directly. Inside Claude Code, the orchestrator (`CLAUDE.md`) just uses `audiobook ...` everywhere.

## Prerequisites

On the host (macOS):

- [colima](https://github.com/abiosoft/colima) + Docker CLI — `brew install colima docker docker-compose`
- [uv](https://github.com/astral-sh/uv) — `brew install uv` (only required for Stage 4 / TTS)
- [Claude Code](https://docs.claude.com/en/docs/claude-code) — the orchestrator. Logged in with a Pro or Max subscription (no API key needed in agent mode).
- (Optional) tmux — `bin/dev` uses it if present.

## First-time setup

```sh
bin/dev
```

That single command:

1. Starts colima if it isn't already.
2. Builds the `audiobook:dev` Docker image if it doesn't exist (first run only — a few minutes).
3. Opens a tmux session with a project shell and a Claude Code window.

For Stage 4 (TTS on the host) — run once, when you're ready to render audio:

```sh
scripts/host-install.sh
```

This creates `.venv/` and installs `chatterbox-tts` + `torch` with the MPS backend.

## Step-by-step: process your first EPUB

### 1. Drop the inputs in place

```
input/book.epub          # your EPUB
voice/reference.wav      # 10–15s WAV of the desired narrator voice (24 kHz mono PCM ideally; other formats are auto-converted)
```

The pipeline narrates in whatever voice you supply — your own voice works well. Record in a quiet room, ~15 cm from the mic, reading calmly. See spec §8 "Recording a reference sample" for a suggested script.

### 2. Check the voice reference

```sh
bin/audiobook voice validate ./voice/reference.wav
bin/audiobook voice preview ./voice/reference.wav
```

`validate` runs in Docker (no GPU needed) and checks format/duration/clipping/SNR.
`preview` runs on the host (needs MPS) and writes a 30-second sample to `voice/preview.wav`. Listen to it. If it sounds like the speaker you wanted, continue. If not, re-record and re-run.

### 3. Open Claude Code and let it drive

From the project root (or from the Claude Code window that `bin/dev` opens):

```
> process the book
```

That's the whole instruction. Claude Code reads `CLAUDE.md`, then:

- Runs `audiobook parse ./input/book.epub --out ./work` (Docker)
- Dispatches one subagent per chapter to adapt the prose — these are Claude Code subagents, running on your Pro/Max subscription, in parallel waves of 8 by default
- Runs `audiobook validate-adapted ./work` after each wave and retries any chapter that failed (up to 2 retries)
- Runs `audiobook merge-pronunciation ./work`
- Runs `audiobook chunk ./work` (Docker)
- Runs `audiobook render ./work` (**host**, MPS — 2-4 hours expected for a 500-page book)
- Runs `audiobook assemble ./work --out ./out/book.m4b` (Docker)

You'll see a progress summary at each stage transition. When it's done you'll find your audiobook at `out/<slug>.m4b`.

### 4. Resume after interruption

Everything is resumable. If you stop mid-run, just say "continue" — completed work is on disk and gets skipped automatically:

- Already-adapted chapters are detected via `audiobook validate-adapted`.
- Already-rendered WAVs are skipped chunk-by-chunk.
- `--force` overrides if you want to redo something.

### 5. Iterating on quality (highest-leverage loop)

If a chapter doesn't sound right when you read it aloud, edit `prompts/adapt_system.md`, delete the offending `work/chapters/adapted/NN_*.json`, and say "re-adapt chapter N." Resumability does the rest. Do this *before* running Stage 4 — synthesizing bad text is the most expensive way to find out it's bad.

## Direct command reference

```sh
bin/audiobook parse INPUT.epub --out ./work          # Docker — Stage 1
bin/audiobook validate-adapted ./work                # Docker — Stage 2 gate (orchestrator uses this)
bin/audiobook merge-pronunciation ./work             # Docker
bin/audiobook chunk ./work                           # Docker — Stage 3
bin/audiobook voice validate ./voice/reference.wav   # Docker
bin/audiobook voice preview ./voice/reference.wav    # Host (MPS)
bin/audiobook render ./work                          # Host (MPS) — Stage 4
bin/audiobook assemble ./work --title T --author A --out ./out/book.m4b  # Docker — Stage 5
bin/audiobook status ./work                          # Read work/state.json
```

## Development

```sh
bin/audiobook-test            # Run pytest in Docker
bin/audiobook-test -k parse   # Run a subset
docker compose build          # Rebuild image (only needed when pyproject.toml changes)
```

Source edits are picked up automatically via the bind mount — no rebuild needed for `.py` changes.

## Project layout

```
audiobook/           Python package (CLI + each stage)
prompts/             Adaptation system prompt (shared by all subagents)
tests/               pytest suite + tiny.epub fixture
scripts/             Host install script for Stage 4
bin/                 dev launcher + audiobook/audiobook-test wrappers
docs/superpowers/    Design doc + implementation plan
input/ voice/ work/ out/   Pipeline I/O (gitignored)
```
