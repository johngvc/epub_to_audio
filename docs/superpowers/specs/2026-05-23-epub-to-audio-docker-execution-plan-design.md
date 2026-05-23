# Docker-based execution plan for the EPUB-to-Audio pipeline

**Date:** 2026-05-23
**Source spec:** `epub_to_audio_spec.md` (root of repo)
**Status:** Design — awaiting user approval before implementation planning

This document specifies *how* we will build the pipeline from `epub_to_audio_spec.md`, not the pipeline itself. The source spec defines behavior; this design defines the environment, the host/container split, and the testable build order.

---

## 1. Goals of this design

- Implement the source spec faithfully, honoring its acceptance criteria (§15) and prescribed tech stack (§18).
- Run the pipeline entirely from Docker for stages whose dependencies are containerizable. No host installs beyond what is strictly required.
- Maximize automated test coverage — TDD per stage where the spec permits it (everything except LLM behavior and TTS render, both excluded by §18).
- Make the v1 build target the **agent-mode** path only (§17 build order); chat and API modes deferred.

## 2. Hard constraint and the resulting split

PyTorch's MPS backend (Apple's GPU) is **not accessible from Docker containers on macOS**. Docker Desktop on macOS runs a Linux VM; the Metal API is only reachable from native macOS processes. The source spec's `<8 hours` runtime target for a 500-page book (§15.5) depends on MPS-accelerated Chatterbox TTS.

Therefore the build splits into two execution surfaces:

| Concern | Where | Why |
|---|---|---|
| Stage 1 — Parse | Docker | Pure Python + lxml |
| Stage 2 — Validator + merge utilities | Docker | Pure Python + Pydantic |
| Stage 2 — LLM dispatch (agent mode) | **Host** (Claude Code) | Claude Code drives subagents from its own context |
| Stage 3 — Chunk | Docker | Pure Python + pysbd |
| Stage 4 — Voice validate (format/SNR checks) | Docker | numpy + soundfile only, no model |
| Stage 4 — Voice preview + Render | **Host** | Requires MPS via PyTorch + chatterbox-tts |
| Stage 5 — Assemble | Docker | ffmpeg + mp4v2 + mutagen |
| Tests (unit + integration) | Docker | pytest against tiny.epub fixture |

The only required host installs: `uv`, a Python 3.12 venv with the `[render]` extra, and Claude Code itself. Everything else stays inside the container image.

## 3. Container architecture

**Single container, no sidecars.** The pipeline has no database, queue, or web layer; state lives in `work/state.json` on disk; parallelism inside Docker (when needed) is `ThreadPoolExecutor` within one process.

**Image base:** `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` — Astral's official image. Trusted source, ships `uv` preinstalled (the spec's mandated package manager per §18).

**System packages added in the Dockerfile:**
- `ffmpeg` (stage 5 audio assembly)
- `mp4v2-utils` (provides `mp4chaps` for chapter markers)
- `espeak-ng` (referenced by §4 system deps; harmless if unused)

**`docker-compose.yml`** defines one service `audiobook`:
- Bind-mounts the project root at `/workspace`
- `WORKDIR=/workspace`
- Default command is the Typer entrypoint `audiobook`
- No published ports, no environment beyond `PYTHONUNBUFFERED=1`

The bind mount means host edits to source reflect in the container with no rebuild. Image rebuild is only needed when `pyproject.toml` dependencies change.

## 4. Package layout for the two-environment install

One Python package, two install profiles in `pyproject.toml`:

- **Default (Docker):** `ebooklib`, `beautifulsoup4`, `lxml`, `pydantic`, `typer`, `pysbd`, `markdownify`, `mutagen`, `soundfile`, `numpy`, `rich`. Plus dev deps `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
- **`[render]` extra (host):** adds `torch`, `chatterbox-tts`.

`audiobook render` and `audiobook voice preview` import `chatterbox_tts` lazily inside the command function. Importing the CLI module itself never imports torch, so the Docker install stays small and the help text loads instantly.

If `chatterbox_tts` is missing when those commands run, the CLI raises a clear error: *"This command requires the host install. Run scripts/host-install.sh on macOS."*

## 5. Path strategy: identical relative paths on host and in container

The bind mount at `/workspace` plus `WORKDIR=/workspace` means a relative path like `./work/chapters/raw/00.json` resolves to the same file from:

- Claude Code's Read/Write tools (host)
- Bash commands run on the host
- Anything inside the container

CLAUDE.md (and the user) never need absolute paths and never need path translation. This is the single most important affordance of the design.

## 6. The `audiobook` wrapper script

`bin/audiobook` on the host routes by subcommand:

```sh
case "$1 $2" in
  "render "*)         exec uv run audiobook "$@" ;;
  "voice preview"*)   exec uv run audiobook "$@" ;;
  *)                  exec docker compose run --rm audiobook audiobook "$@" ;;
esac
```

A sibling `bin/audiobook-test` runs `docker compose run --rm audiobook pytest "$@"`.

Result: CLAUDE.md and the user issue `audiobook parse ...`, `audiobook render ...` uniformly. The wrapper decides where it runs. The source spec's §6.5 CLI surface is preserved verbatim.

## 7. Claude Code workflows

Three distinct loops, all driven through the same wrapper.

### 7.1 Orchestrator runtime (CLAUDE.md)

User opens the project in Claude Code → "process the book". Claude Code:

1. `audiobook parse ./input/book.epub --out ./work` (Docker)
2. Reads `./work/chapters/raw/*.json` listing via Read tool (host filesystem)
3. Dispatches one subagent per chapter via its Task tool — these are *Claude Code subagents*, not containers. Each subagent reads the raw chapter JSON, applies the system prompt from `./prompts/adapt_system.md`, writes the adapted JSON to `./work/chapters/adapted/NN.json`, and replies `DONE` or `FAILED`.
4. `audiobook validate-adapted ./work` (Docker) after each wave. Parses machine-readable per-chapter output. Failed chapters are re-dispatched with the validator's specific error inline.
5. `audiobook merge-pronunciation ./work` (Docker)
6. `audiobook chunk ./work` (Docker)
7. `audiobook render ./work` (**host**, via wrapper → uv → MPS)
8. `audiobook validate-render ./work` (Docker — audio format checks, no model)
9. `audiobook assemble ./work --out ./out/book.m4b` (Docker)

### 7.2 Prompt-quality iteration (§17 step 5, highest-leverage)

Edit `prompts/adapt_system.md`, delete the offending `work/chapters/adapted/*.json`, ask Claude Code to re-adapt. Resumability (§6.6) means only the deleted chapters are re-dispatched. No code changes. No TTS yet — this loop runs before stage 4 to avoid wasting render time on bad text.

### 7.3 Code refinement

- Source edits: host filesystem, picked up by container on next invocation (bind mount). No rebuild.
- Dependency changes: `docker compose build` rebuilds the image.
- Tests: `bin/audiobook-test` (Docker). TDD cycle is fast and isolated.
- Render-stage development: `uv run pytest -k render` on the host (small surface — voice-validate logic and any host-side plumbing).

## 8. CLAUDE.md changes from the source spec

The §6.2 template needs three adaptations:

1. **Commands prefixed via the wrapper.** All `audiobook ...` calls go through `bin/audiobook` (or just `audiobook` if the user adds `./bin` to PATH).
2. **Path convention noted.** A short preamble explaining that all paths are relative to the project root and resolve identically inside and outside the container.
3. **Stage 4 host execution called out.** A note that `audiobook render` runs on the host via uv (handled by the wrapper transparently, but worth flagging so future Claude Code instances understand the split).

The substantive orchestration logic (validation gates, retry policy, failure thresholds, concurrency limits) stays identical to §6.2.

## 9. Build order (testable increments)

This mirrors source-spec §17, with Docker setup as Step 0 and an explicit test gate per step. Each step ends green before the next begins.

### Step 0 — Docker bootstrap & project skeleton
- `Dockerfile`, `docker-compose.yml`
- `pyproject.toml` with core deps + `[render]` extra
- Empty `audiobook` package: `cli.py` with Typer app stub, `models.py` with Pydantic skeleton
- `bin/audiobook`, `bin/audiobook-test` wrappers
- `prompts/adapt_system.md` copied verbatim from source spec §6.1
- Initial `CLAUDE.md` (revised in Step 4)
- `config.toml` from source spec §12
- `tests/test_smoke.py`: `audiobook --help` exits 0
- **Gate:** `bin/audiobook --help` works; `bin/audiobook-test` passes; `docker compose build` succeeds.

### Step 1 — Tiny EPUB fixture
- A small Python builder script (`tests/fixtures/build_tiny_epub.py`) that produces `tests/fixtures/tiny.epub` with 3 chapters covering prose + one code block + one equation + one table + one figure. Committed binary.
- **Gate:** ebooklib opens it in a test without errors.

### Step 2 — Stage 1 Parse (TDD)
- Tests first: `ChapterRaw` schema; tiny.epub produces N raw JSONs; `book_full_text.md` exists with code blocks replaced; long-chapter splitting at `<h2>`; skip-section rules.
- Implement `audiobook/parse.py` until green.
- **Gate:** `audiobook parse tests/fixtures/tiny.epub --out tests/_scratch/parse` produces the expected tree.

### Step 3 — Stage 2 validator + merge utilities (TDD)
- Hand-crafted JSON fixtures under `tests/fixtures/adapted/`: valid, truncated, prose-wrapped, schema-mismatched, markdown-artifact, length-anomalous (too short / too long).
- `audiobook validate-adapted` emits machine-readable per-chapter result so the orchestrator can parse it deterministically.
- `audiobook merge-pronunciation` deduplicates and canonicalizes.
- **Gate:** every fixture is classified correctly; merge produces canonical `pronunciation.json`.

### Step 4 — Wire CLAUDE.md to the Docker workflow
- Apply the three adaptations in §8 above to a fresh CLAUDE.md based on source-spec §6.2.
- Document the wrapper, path convention, and host/Docker split in a "How this project runs" section near the top.
- **Manual gate:** open the project in Claude Code with tiny.epub at `input/book.epub`; run through parse + adapt + validate. Confirm subagents dispatch, validator passes on a clean run, retries trigger when a chapter is deliberately corrupted.

### Step 5 — Prompt iteration loop (§17 step 5)
- Listen to (read aloud) each adapted chapter from the tiny EPUB.
- Iterate on `prompts/adapt_system.md` and re-dispatch affected chapters.
- No code changes expected. This is prompt engineering, sequenced before TTS to avoid wasted synthesis.

### Step 6 — Stage 3 Chunk (TDD)
- Tests first: pronunciation find-replace (case-sensitive acronym rule); pysbd segmentation; greedy ≤400-char packing; no orphan-short-sentences; silence annotation (paragraph 400 ms / section 1200 ms).
- Implement `audiobook/chunk.py`.
- **Gate:** tiny adapted JSON → expected chunks JSON.

### Step 7 — Host-side environment for Stage 4
- `scripts/host-install.sh`: `uv venv --python 3.12 && uv pip install -e ".[render]"`.
- `audiobook voice validate`: audio format/SNR/silence checks; pure numpy + soundfile; tests run in Docker.
- `audiobook voice preview` and `audiobook render`: host only; lazy-import `chatterbox_tts`.
- Docker tests cover voice-validate logic, render plumbing (with TTS mocked), `state.json` updates, and resumability (skip chunks whose WAV already exists and passes validation).
- **Manual host gate:** run `audiobook voice preview voice/reference.wav` and listen; then `audiobook render ./work` for one chapter. No automated TTS test — source-spec §18 explicitly excludes it.

### Step 8 — Stage 5 Assemble (TDD where possible)
- Tests first: `ffmetadata` chapter-marker generation from per-chapter durations; mutagen tag writing on a tiny .m4b; cover-art extraction from the EPUB; slugified output filename.
- ffmpeg/`mp4chaps` invocations tested with synthetic 1-second sine-wave fixtures.
- **Gate:** synthetic chunks → valid `.m4b` whose chapters are visible to `ffprobe`.

### Step 9 — Full tiny-EPUB end-to-end
- Claude Code drives the full CLAUDE.md flow against tiny.epub with a real voice reference.
- Output: `out/tiny.m4b` plays in Apple Books with navigable chapter markers.
- This is the v1 acceptance gate (source-spec §17 step 7).

### Step 10 — Real book
Out of scope for the build phase. This is the source spec's §15 acceptance, validated after v1 lands.

## 10. Testing strategy

Following source-spec §18 testing notes:

- **In Docker (CI-ready):** parse, validator, chunker, assembler unit + integration tests against the tiny.epub fixture and hand-crafted JSON/audio fixtures. Audio fixtures are synthetic sine waves — no microphone, no TTS, deterministic.
- **Excluded from automated tests (per §18):** LLM adaptation behavior, TTS render output. These are validated by manual listening (§17 step 5) and the end-to-end §15 acceptance criteria.
- **Lint + types:** `ruff` and `mypy --strict` run in Docker; same image as tests.
- **Pre-commit:** ruff + mypy hooks configured once; both run in the container so contributors don't need a host Python.

## 11. Trusted image sources

- `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` — Astral, the project that publishes `uv` (the spec's mandated package manager). Pinned by digest in the Dockerfile to lock the build.
- All apt packages from Debian Bookworm's default repos.
- No third-party Docker registries.

## 12. Non-goals for this build

Carried forward from source-spec §16 plus design-specific exclusions:

- No chat-mode or API-mode adapt paths in v1 (defer per the user decision recorded during brainstorming).
- No multi-container orchestration (no Postgres, Redis, RabbitMQ, etc. — the pipeline has no need for any).
- No CI pipeline configuration (`.github/workflows/` etc.) in v1 — local Docker tests are the only required gate.
- No host installs beyond `uv` + Python venv + Claude Code.

## 13. Open items deferred to the implementation plan

The writing-plans phase that follows this design will produce the concrete file-by-file plan. Items intentionally left for that phase:

- Exact Pydantic model field definitions (the source spec sketches JSON schemas; the Pydantic translation is straightforward but not yet written out).
- The wrapper script's exact bash dispatch (a few subcommands have nested args; the case statement may need slight expansion).
- Concrete CLAUDE.md text with the §8 adaptations applied.
- Concrete `Dockerfile` line ordering for layer-cache friendliness (deps before source copy, etc.).
