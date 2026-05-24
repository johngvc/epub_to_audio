# Audiobook Pipeline — Orchestrator Instructions

You are orchestrating the conversion of an EPUB book into an audiobook. Follow this workflow exactly.

## How this project runs

This project uses one Docker container for stages 1, 2, 3, and 5, plus the host environment for stage 4 (TTS) and the orchestration you are performing right now. A wrapper script (`bin/audiobook`) routes each subcommand to the right place automatically — you never need to type `docker compose` or `uv run` directly.

- All paths in commands are **relative to the project root**, and resolve identically inside and outside the container thanks to a bind mount at `/workspace`.
- Stage 4 (`audiobook render`, `audiobook voice preview`) runs on the host via `uv` so it can use Apple Silicon's MPS GPU. Every other `audiobook ...` call runs in Docker. The wrapper handles this distinction; just use `audiobook <subcommand> ...`.
- If `bin/` is not in your PATH, invoke commands as `bin/audiobook ...`.

## Setup verification

1. Confirm `./input/book.epub` exists. If missing, stop and ask the user.
2. Confirm `./voice/reference.wav` exists. If the file is missing or in a non-WAV format (e.g. `.m4a` from Voice Memos), help the user convert:
   - `afconvert -f WAVE -d LEI16@24000 -c 1 ./voice/input.m4a ./voice/reference.wav` (macOS, built in)
   If a voice preview has not been generated for it:
   - Run: `bin/audiobook voice validate ./voice/reference.wav`
   - Run: `bin/audiobook voice preview ./voice/reference.wav`
   - Ask the user to listen to `./voice/preview.wav` and confirm before continuing.
3. Read `config.toml` and confirm:
   - `[book].title` and `[book].author` are filled in (required for Stage 5). If empty, ask the user.
   - `adapt.mode = "agent"` and note `adapt.concurrency` (default 8).

## Stage 1 — Parse

Run: `bin/audiobook parse ./input/book.epub --out ./work`
Verify `./work/chapters/raw/` is populated and `./work/book_full_text.md` exists.

## Stage 2 — Adapt (your main job as orchestrator)

**Two modes:**
- `[adapt].mode = "agent"` (default) — you orchestrate via subagents, as documented below. Use this when running interactively in Claude Code.
- `[adapt].mode = "api"` — set this and run `bin/audiobook adapt ./work` to drive the entire stage unattended against a local LM Studio (OpenAI-compatible) endpoint. Requires `[adapt.api].model` to be set and LM Studio to be running. The CLI handles concurrency, retries (up to 2 per chapter with validator error feedback), and the whole-book context decision automatically. Use this for headless runs.

The numbered steps below describe the **agent-mode** workflow:

1. Read `./prompts/adapt_system.md` once. This is the system prompt for every subagent.
2. List all chapter files in `./work/chapters/raw/`.
3. Skip any chapter that already has a valid `./work/chapters/adapted/NN_title.json`. Use `bin/audiobook validate-adapted ./work` to check.
4. For the remaining chapters, dispatch subagents in waves of up to `adapt.concurrency` (8 by default).
5. Each subagent dispatch must include:
   - The full system prompt from `./prompts/adapt_system.md`.
   - The exact input file path (`./work/chapters/raw/NN_title.json`).
   - The exact output file path (`./work/chapters/adapted/NN_title.json`).
   - A pointer to `./work/book_full_text.md` for cross-chapter context.
   - Explicit instruction: "Read the input file, apply all rules in the system prompt, write the JSON to the output file path. Do not print the JSON in your final reply — only write it to the file. Reply with: `DONE` plus a one-line summary of what you wrote, or `FAILED` plus the reason."
6. After each wave, run `bin/audiobook validate-adapted ./work` and parse its JSON output.
   - Chapters that pass move to "done."
   - Chapters that fail (malformed JSON, schema mismatch, length anomalies, markdown artifacts) are queued for retry. Include the validator's specific `error_kind` and `detail` in the retry dispatch so the subagent knows what to fix.
7. Each chapter gets at most 2 retries. If still failing, log in `./work/state.json` under `failures` and continue.
8. After all chapters processed, run `bin/audiobook merge-pronunciation ./work`.
9. Report adaptation summary: succeeded / retried / failed counts.

## Stage 3 — Chunk

If adaptation completed without hard failures (or the user explicitly proceeds):
Run: `bin/audiobook chunk ./work`

## Stage 4 — Render

Run: `bin/audiobook render ./work` (host-side, 2–4 hours; expected).
Then: `bin/audiobook validate-render ./work` to confirm no chunks failed quality checks.

## Stage 5 — Assemble

Run: `bin/audiobook assemble ./work --out ./out/book.m4b`

This reads `[book].title`, `[book].author`, and `[book].narrator` from `config.toml`. To override per-run, pass `--title`, `--author`, and/or `--narrator`. To embed cover art, pass `--cover ./input/cover.jpg`.

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
