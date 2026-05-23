# EPUB-to-Audio Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the EPUB-to-Audio pipeline defined in `epub_to_audio_spec.md` as a Docker-first project (TTS + Claude Code on host, all other stages in a single container), agent-mode-only for v1.

**Architecture:** One Python package (`audiobook`) installed in two profiles: Docker image (default deps) and host venv (`[render]` extra adds torch + chatterbox-tts). A `bin/audiobook` wrapper routes subcommands to Docker or to the host venv. Bind mount at `/workspace` makes relative paths identical on host and in container.

**Tech Stack:** Python 3.12, uv, Typer, Pydantic v2, ebooklib, beautifulsoup4 + lxml, pysbd, markdownify, soundfile, numpy, mutagen, ffmpeg, mp4v2 (`mp4chaps`), pytest, ruff, mypy. Image base: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`. Docker driven by colima on macOS (already installed; see `bin/dev`).

**Reference docs in this repo:**
- `epub_to_audio_spec.md` — full source spec
- `docs/superpowers/specs/2026-05-23-epub-to-audio-docker-execution-plan-design.md` — design decisions

---

## File structure (created by this plan)

```
.
├── Dockerfile                                       [Task 1]
├── docker-compose.yml                               [Task 1]
├── .dockerignore                                    [Task 1]
├── .gitignore                                       [Task 1]
├── pyproject.toml                                   [Task 1]
├── config.toml                                      [Task 1]
├── README.md                                        [Task 1]
├── CLAUDE.md                                        [Task 1, revised Task 7]
├── prompts/
│   └── adapt_system.md                              [Task 1 — copied verbatim from spec §6.1]
├── bin/
│   ├── audiobook                                    [Task 1]
│   └── audiobook-test                               [Task 1]
├── scripts/
│   └── host-install.sh                              [Task 11]
├── audiobook/
│   ├── __init__.py                                  [Task 1]
│   ├── cli.py                                       [Task 1, grows per task]
│   ├── models.py                                    [Task 2]
│   ├── config.py                                    [Task 2]
│   ├── state.py                                     [Task 9]
│   ├── parse.py                                     [Task 4]
│   ├── adapt.py                                     [Task 5 — validators + merge only]
│   ├── chunk.py                                     [Task 8]
│   ├── voice.py                                     [Task 10]
│   ├── render.py                                    [Task 11 — host only, lazy imports]
│   ├── assemble.py                                  [Task 12]
│   └── utils/
│       ├── __init__.py                              [Task 1]
│       ├── slugify.py                               [Task 2]
│       └── audio.py                                 [Task 10]
└── tests/
    ├── __init__.py                                  [Task 1]
    ├── conftest.py                                  [Task 1]
    ├── test_smoke.py                                [Task 1]
    ├── test_models.py                               [Task 2]
    ├── test_config.py                               [Task 2]
    ├── test_slugify.py                              [Task 2]
    ├── fixtures/
    │   ├── build_tiny_epub.py                       [Task 3]
    │   ├── tiny.epub                                [Task 3 — generated]
    │   └── adapted/
    │       ├── valid.json                           [Task 5]
    │       ├── truncated.json                       [Task 5]
    │       ├── prose_wrapped.json                   [Task 5]
    │       ├── schema_mismatched.json               [Task 5]
    │       ├── markdown_artifact.json               [Task 5]
    │       ├── too_short.json                       [Task 5]
    │       └── too_long.json                        [Task 5]
    ├── test_parse.py                                [Task 4]
    ├── test_validate_adapted.py                     [Task 5]
    ├── test_merge_pronunciation.py                  [Task 6]
    ├── test_chunk.py                                [Task 8]
    ├── test_state.py                                [Task 9]
    ├── test_voice_validate.py                       [Task 10]
    └── test_assemble.py                             [Task 12]
```

---

## Conventions (apply to every task)

- **Working directory in every command:** repo root. `bin/audiobook` and `docker compose` are run from there.
- **Inside Docker, `WORKDIR=/workspace`**, project root bind-mounted there. Same relative paths work in both environments.
- **TDD order:** write the failing test → run it (must fail) → implement → run it (must pass) → commit.
- **Tests run in Docker:** via `bin/audiobook-test` or `docker compose run --rm audiobook pytest …`.
- **Commits:** small, frequent, one per task minimum. Use Conventional Commit-ish style (`feat:`, `test:`, `chore:`, `docs:`). Always end the commit message with the standard `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.
- **DO NOT modify** `bin/dev` (pre-existing launcher).
- **DO NOT install host-side TTS deps in this plan.** Stage 4 host execution is a manual gate the user runs separately after Task 11 scaffolds it.

---

## Task 1: Docker bootstrap & project skeleton

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`, `pyproject.toml`, `config.toml`, `README.md`, `CLAUDE.md` (initial), `prompts/adapt_system.md`, `bin/audiobook`, `bin/audiobook-test`, `audiobook/__init__.py`, `audiobook/cli.py`, `audiobook/utils/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_smoke.py`

- [ ] **Step 1.1: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
*.egg-info/
build/
dist/

# Project working/output dirs
input/
voice/*.wav
voice/preview.wav
work/
out/
tests/_scratch/

# Keep the tiny fixture
!tests/fixtures/tiny.epub
```

- [ ] **Step 1.2: Create `.dockerignore`**

```
.git
.venv
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
work/
out/
input/
voice/
tests/_scratch/
docs/
```

- [ ] **Step 1.3: Create `pyproject.toml`**

```toml
[project]
name = "audiobook"
version = "0.1.0"
description = "EPUB-to-audiobook pipeline (Claude Sonnet 4.6 + Chatterbox TTS)"
requires-python = ">=3.12"
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
]

[project.optional-dependencies]
render = [
    "torch>=2.3",
    "chatterbox-tts>=0.1.0",
]

[project.scripts]
audiobook = "audiobook.cli:app"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
    "mypy>=1.10",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.mypy]
strict = true
python_version = "3.12"
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["audiobook"]
```

- [ ] **Step 1.4: Create `Dockerfile`**

```dockerfile
# Trusted base: Astral's official uv image, ships uv preinstalled.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# System deps: ffmpeg for assembly, mp4v2-utils for mp4chaps, espeak-ng for any
# fallback phonemization needs (referenced by source-spec §4).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        mp4v2-utils \
        espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Install Python deps first (cacheable layer)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --extra dev || \
    uv sync --no-install-project --extra dev

# The project source is bind-mounted at runtime; install in editable mode
# via a small entry that re-syncs when the source is present.
COPY audiobook ./audiobook
RUN uv pip install --no-deps -e .

ENTRYPOINT []
CMD ["audiobook", "--help"]
```

- [ ] **Step 1.5: Create `docker-compose.yml`**

```yaml
services:
  audiobook:
    build:
      context: .
      dockerfile: Dockerfile
    image: audiobook:dev
    working_dir: /workspace
    volumes:
      - .:/workspace
    environment:
      PYTHONUNBUFFERED: "1"
    tty: true
    stdin_open: true
```

- [ ] **Step 1.6: Create `bin/audiobook` wrapper**

```bash
#!/usr/bin/env bash
# audiobook CLI dispatcher: routes render-stage subcommands to the host venv
# (needs MPS via uv) and everything else to the Docker container.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

sub1="${1:-}"
sub2="${2:-}"

run_host() {
  if [[ ! -x .venv/bin/audiobook ]]; then
    echo "host venv not found. Run scripts/host-install.sh first." >&2
    exit 1
  fi
  exec uv run audiobook "$@"
}

run_docker() {
  exec docker compose run --rm audiobook audiobook "$@"
}

case "$sub1" in
  render)
    run_host "$@"
    ;;
  voice)
    if [[ "$sub2" == "preview" ]]; then
      run_host "$@"
    else
      run_docker "$@"
    fi
    ;;
  *)
    run_docker "$@"
    ;;
esac
```

Make executable: `chmod +x bin/audiobook`.

- [ ] **Step 1.7: Create `bin/audiobook-test` wrapper**

```bash
#!/usr/bin/env bash
# Run pytest inside the Docker image. Pass-through args.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
exec docker compose run --rm audiobook pytest "$@"
```

Make executable: `chmod +x bin/audiobook-test`.

- [ ] **Step 1.8: Create `config.toml`** (verbatim from source-spec §12 with the title/author placeholders kept blank)

```toml
[book]
title = ""
author = ""
narrator = ""
skip_sections = ["copyright", "dedication", "index", "bibliography"]

[adapt]
mode = "agent"
model_label = "claude-sonnet-4-6"
concurrency = 8
split_long_chapters_at_words = 6000
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

- [ ] **Step 1.9: Create `prompts/adapt_system.md`** by copying source-spec §6.1 verbatim (the blockquoted prompt body — strip the leading `>` markers and keep the JSON example intact). The agent dispatching this file MUST NOT paraphrase or summarize the rules. Read `epub_to_audio_spec.md` §6.1 and copy from "You are adapting a chapter of a technical book…" through "…Return only the JSON. No prose before or after."

- [ ] **Step 1.10: Create `CLAUDE.md` (initial)** by copying source-spec §6.2's `CLAUDE.md` template verbatim. It will be revised in Task 7 for the Docker workflow, but start with the spec's text as the baseline.

- [ ] **Step 1.11: Create `README.md`** (short, ~30 lines)

```markdown
# epub_to_audio

Local-first pipeline that converts technical EPUB books into `.m4b` audiobooks using Claude Sonnet 4.6 for content adaptation and Chatterbox TTS for narration.

See `epub_to_audio_spec.md` for the full spec and `docs/superpowers/specs/` for design decisions.

## Setup

1. Install [colima](https://github.com/abiosoft/colima) and Docker CLI (already required by `bin/dev`).
2. Build the image: `docker compose build`
3. (Host-side, only when ready for Stage 4 TTS) `scripts/host-install.sh`

## Usage

Everything goes through one wrapper:

```sh
bin/audiobook parse ./input/book.epub --out ./work   # Docker
bin/audiobook render ./work                          # host (MPS)
```

Run tests: `bin/audiobook-test`.
```

(Use a different fence to avoid nesting issues — replace inner triple-backticks with `~~~` if needed; see Task 1's actual file).

- [ ] **Step 1.12: Create the empty package skeleton**

```python
# audiobook/__init__.py
__version__ = "0.1.0"
```

```python
# audiobook/utils/__init__.py
```

- [ ] **Step 1.13: Create `audiobook/cli.py` (Typer stub)**

```python
"""Audiobook pipeline CLI entry point."""
from __future__ import annotations

import typer

app = typer.Typer(
    name="audiobook",
    help="EPUB-to-audiobook pipeline.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Root callback (placeholder; subcommands attached in later tasks)."""


if __name__ == "__main__":
    app()
```

- [ ] **Step 1.14: Create `tests/__init__.py` (empty) and `tests/conftest.py`**

```python
# tests/conftest.py
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    return tmp_path
```

- [ ] **Step 1.15: Write the smoke test** in `tests/test_smoke.py`

```python
from __future__ import annotations

import subprocess
import sys


def test_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "audiobook.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "audiobook" in result.stdout.lower()
```

- [ ] **Step 1.16: Build the image and run the smoke test**

Run: `docker compose build`
Expected: build succeeds.

Run: `bin/audiobook-test tests/test_smoke.py -v`
Expected: PASS.

Run: `bin/audiobook --help`
Expected: Typer help text prints (via Docker).

- [ ] **Step 1.17: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore .gitignore \
        pyproject.toml config.toml README.md CLAUDE.md \
        prompts/adapt_system.md bin/audiobook bin/audiobook-test \
        audiobook/ tests/
git commit -m "$(cat <<'EOF'
chore: docker bootstrap and project skeleton

Adds the single-container image (uv + ffmpeg + mp4v2 + espeak-ng),
the audiobook/audiobook-test wrappers, an empty Typer CLI, and the
smoke test. Source-spec system prompt copied verbatim to prompts/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Pydantic models, config loader, slugify utility

**Files:**
- Create: `audiobook/models.py`, `audiobook/config.py`, `audiobook/utils/slugify.py`, `tests/test_models.py`, `tests/test_config.py`, `tests/test_slugify.py`

- [ ] **Step 2.1: Write the failing test for `slugify`**

```python
# tests/test_slugify.py
from audiobook.utils.slugify import slugify


def test_basic_lowercase() -> None:
    assert slugify("Chapter 5: Concurrency Primitives") == "chapter-5-concurrency-primitives"


def test_strips_unicode_and_punctuation() -> None:
    assert slugify("Café — résumé!") == "cafe-resume"


def test_collapses_separators() -> None:
    assert slugify("  Hello   World  ") == "hello-world"


def test_empty_yields_untitled() -> None:
    assert slugify("") == "untitled"
```

- [ ] **Step 2.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_slugify.py -v`
Expected: FAIL (`audiobook.utils.slugify` does not exist).

- [ ] **Step 2.3: Implement `audiobook/utils/slugify.py`**

```python
"""ASCII slug helper used by parse and assemble."""
from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """Return a lowercase ASCII slug suitable for filenames.

    Empty / whitespace-only inputs yield ``"untitled"`` so downstream paths
    never collide on an empty filename.
    """
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned or "untitled"
```

- [ ] **Step 2.4: Run, expect pass**

Run: `bin/audiobook-test tests/test_slugify.py -v`
Expected: PASS.

- [ ] **Step 2.5: Write the failing test for the models**

```python
# tests/test_models.py
import pytest
from pydantic import ValidationError

from audiobook.models import (
    ChapterRaw,
    ChapterAdapted,
    PronunciationHint,
    Chunk,
    ChapterChunks,
)


def test_chapter_raw_minimal() -> None:
    raw = ChapterRaw(
        index=0,
        title="Intro",
        source_spine_id="ch01.xhtml",
        html="<p>Hello</p>",
        word_count_estimate=2,
        has_code=False,
        has_math=False,
        has_tables=False,
    )
    assert raw.index == 0


def test_pronunciation_hint_fields() -> None:
    h = PronunciationHint(term="kubectl", spoken_as="cube control", reason="CLI tool")
    assert h.term == "kubectl"


def test_chapter_adapted_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChapterAdapted(
            adapted_text="hi",
            pronunciation_hints=[],
            notes="",
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_chapter_adapted_empty_text_rejected() -> None:
    with pytest.raises(ValidationError):
        ChapterAdapted(adapted_text="", pronunciation_hints=[], notes="")


def test_chunk_max_chars_enforced() -> None:
    with pytest.raises(ValidationError):
        Chunk(id="0000", text="x" * 401, trailing_silence_ms=0)


def test_chapter_chunks_ids_unique() -> None:
    with pytest.raises(ValidationError):
        ChapterChunks(
            index=0,
            title="t",
            chunks=[
                Chunk(id="0000", text="a", trailing_silence_ms=0),
                Chunk(id="0000", text="b", trailing_silence_ms=0),
            ],
        )
```

- [ ] **Step 2.6: Run, expect fail**

Run: `bin/audiobook-test tests/test_models.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 2.7: Implement `audiobook/models.py`**

```python
"""Pydantic v2 models for every on-disk artifact in the pipeline."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    """Common base: forbid extras, validate on assignment."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=False)


class ChapterRaw(_Strict):
    index: int = Field(ge=0)
    title: str = Field(min_length=1)
    source_spine_id: str
    html: str
    word_count_estimate: int = Field(ge=0)
    has_code: bool
    has_math: bool
    has_tables: bool
    part: int | None = Field(default=None, ge=1)  # set when a long chapter is split at <h2>
    part_of: int | None = Field(default=None, ge=1)


class PronunciationHint(_Strict):
    term: str = Field(min_length=1)
    spoken_as: str = Field(min_length=1)
    reason: str = ""


class ChapterAdapted(_Strict):
    adapted_text: str = Field(min_length=1)
    pronunciation_hints: list[PronunciationHint] = Field(default_factory=list)
    notes: str = ""


class Chunk(_Strict):
    id: str = Field(pattern=r"^\d{4}$")
    text: str = Field(min_length=1, max_length=400)
    trailing_silence_ms: int = Field(ge=0, le=10_000)


class ChapterChunks(_Strict):
    index: int = Field(ge=0)
    title: str = Field(min_length=1)
    chunks: list[Chunk]

    @model_validator(mode="after")
    def _unique_chunk_ids(self) -> "ChapterChunks":
        ids = [c.id for c in self.chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("chunk ids must be unique within a chapter")
        return self


class BookMetadata(_Strict):
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    narrator: str = ""
    publisher: str = ""
    year: int | None = None
    genre: str = ""
    description: str = ""
```

- [ ] **Step 2.8: Run, expect pass**

Run: `bin/audiobook-test tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 2.9: Write the failing test for config loader**

```python
# tests/test_config.py
from pathlib import Path

from audiobook.config import AppConfig, load_config


def test_loads_repo_default(repo_root: Path) -> None:
    cfg = load_config(repo_root / "config.toml")
    assert isinstance(cfg, AppConfig)
    assert cfg.adapt.mode == "agent"
    assert cfg.adapt.concurrency == 8
    assert cfg.chunk.max_chars == 400
    assert cfg.render.workers == 2


def test_rejects_unknown_section(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[bogus]\nx = 1\n")
    import pytest
    with pytest.raises(ValueError):
        load_config(bad)
```

- [ ] **Step 2.10: Run, expect fail**

Run: `bin/audiobook-test tests/test_config.py -v`
Expected: FAIL (`audiobook.config` missing).

- [ ] **Step 2.11: Implement `audiobook/config.py`**

```python
"""TOML config loader with strict Pydantic validation."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BookConfig(_Strict):
    title: str = ""
    author: str = ""
    narrator: str = ""
    skip_sections: list[str] = Field(
        default_factory=lambda: ["copyright", "dedication", "index", "bibliography"]
    )


class AdaptConfig(_Strict):
    mode: Literal["agent", "chat", "api"] = "agent"
    model_label: str = "claude-sonnet-4-6"
    concurrency: int = Field(default=8, ge=1, le=32)
    split_long_chapters_at_words: int = Field(default=6000, ge=500)
    max_tokens_per_call: int = 8192
    budget_usd: float = 15.0
    prompt_cache: bool = True


class ChunkConfig(_Strict):
    max_chars: int = Field(default=400, ge=50, le=600)
    paragraph_silence_ms: int = Field(default=400, ge=0, le=5000)
    section_silence_ms: int = Field(default=1200, ge=0, le=10_000)


class RenderConfig(_Strict):
    device: str = "mps"
    workers: int = Field(default=2, ge=1, le=8)
    exaggeration: float = 0.4
    cfg_weight: float = 0.5
    temperature: float = 0.7
    multilingual: bool = False


class AssembleConfig(_Strict):
    audio_bitrate_kbps: int = 64
    sample_rate_hz: int = 24000


class AppConfig(_Strict):
    book: BookConfig = Field(default_factory=BookConfig)
    adapt: AdaptConfig = Field(default_factory=AdaptConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    assemble: AssembleConfig = Field(default_factory=AssembleConfig)


def load_config(path: Path) -> AppConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:  # pydantic re-raises as ValidationError
        raise ValueError(f"invalid config {path}: {exc}") from exc
```

- [ ] **Step 2.12: Run, expect pass**

Run: `bin/audiobook-test tests/test_config.py tests/test_models.py tests/test_slugify.py -v`
Expected: ALL PASS.

- [ ] **Step 2.13: Commit**

```bash
git add audiobook/models.py audiobook/config.py audiobook/utils/slugify.py \
        tests/test_models.py tests/test_config.py tests/test_slugify.py
git commit -m "$(cat <<'EOF'
feat: pydantic models, config loader, slug utility

Adds the strict Pydantic v2 schemas for every on-disk artifact
(ChapterRaw, ChapterAdapted, Chunk, ChapterChunks, BookMetadata),
a tomllib + Pydantic config loader for config.toml, and slugify().

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Tiny EPUB fixture

**Files:**
- Create: `tests/fixtures/build_tiny_epub.py`, `tests/fixtures/tiny.epub` (generated binary, committed)

- [ ] **Step 3.1: Write the failing test for the fixture's shape**

Append to `tests/test_parse.py` — or create a tiny placeholder check now in `tests/fixtures/test_fixture.py`:

```python
# tests/fixtures/__init__.py  -- empty
```

```python
# tests/test_fixture.py
from pathlib import Path

import ebooklib  # type: ignore[import-untyped]
from ebooklib import epub


def test_tiny_epub_exists_and_opens(repo_root: Path) -> None:
    path = repo_root / "tests" / "fixtures" / "tiny.epub"
    assert path.exists(), "Run tests/fixtures/build_tiny_epub.py to generate it."
    book = epub.read_epub(str(path))
    docs = [it for it in book.get_items() if it.get_type() == ebooklib.ITEM_DOCUMENT]
    titles = [d.get_name() for d in docs]
    # 3 content chapters + nav
    assert len(docs) >= 3
    assert any("code" in t or "chap" in t.lower() for t in titles)
```

- [ ] **Step 3.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_fixture.py -v`
Expected: FAIL (fixture file missing).

- [ ] **Step 3.3: Write `tests/fixtures/build_tiny_epub.py`**

```python
"""Build tests/fixtures/tiny.epub — three short chapters that exercise prose,
code, equations, a table, and a figure. Run inside Docker:

    docker compose run --rm audiobook python tests/fixtures/build_tiny_epub.py
"""
from __future__ import annotations

from pathlib import Path

from ebooklib import epub  # type: ignore[import-untyped]


def _chapter(idx: int, file_name: str, title: str, body: str) -> epub.EpubHtml:
    c = epub.EpubHtml(title=title, file_name=file_name, lang="en")
    c.content = (
        f"<html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1>{body}</body></html>"
    )
    return c


def build(out: Path) -> None:
    book = epub.EpubBook()
    book.set_identifier("tiny-fixture-001")
    book.set_title("Tiny Technical Book")
    book.set_language("en")
    book.add_author("Test Author")

    c1 = _chapter(
        1,
        "ch01_intro.xhtml",
        "Chapter 1: Introduction",
        "<p>This short book demonstrates the parser. It contains prose, code, "
        "equations, a table, and a figure.</p>"
        "<p>The author writes in a measured, deliberate tone.</p>",
    )
    c2 = _chapter(
        2,
        "ch02_code.xhtml",
        "Chapter 2: A Code Example",
        "<p>The following Python function illustrates recursion.</p>"
        "<pre><code class=\"python\">def fact(n):\n    return 1 if n == 0 else n * fact(n-1)</code></pre>"
        "<p>The equation <span class=\"math\">n! = n * (n-1)!</span> captures the same idea.</p>",
    )
    c3 = _chapter(
        3,
        "ch03_table.xhtml",
        "Chapter 3: A Table and a Figure",
        "<p>The table below compares two approaches.</p>"
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<figure><img src=\"placeholder.png\" alt=\"A placeholder figure\" />"
        "<figcaption>Figure 1: placeholder.</figcaption></figure>"
        "<p>The figure above is referenced from the prose, so it should be described.</p>",
    )

    for c in (c1, c2, c3):
        book.add_item(c)

    book.toc = (
        epub.Link("ch01_intro.xhtml", "Chapter 1: Introduction", "ch1"),
        epub.Link("ch02_code.xhtml", "Chapter 2: A Code Example", "ch2"),
        epub.Link("ch03_table.xhtml", "Chapter 3: A Table and a Figure", "ch3"),
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", c1, c2, c3]

    out.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out), book)
    print(f"wrote {out}")


if __name__ == "__main__":
    build(Path(__file__).resolve().parent / "tiny.epub")
```

- [ ] **Step 3.4: Generate the fixture**

Run: `docker compose run --rm audiobook python tests/fixtures/build_tiny_epub.py`
Expected: prints `wrote /workspace/tests/fixtures/tiny.epub`.

- [ ] **Step 3.5: Run, expect pass**

Run: `bin/audiobook-test tests/test_fixture.py -v`
Expected: PASS.

- [ ] **Step 3.6: Commit**

```bash
git add tests/fixtures/__init__.py tests/fixtures/build_tiny_epub.py \
        tests/fixtures/tiny.epub tests/test_fixture.py
git commit -m "$(cat <<'EOF'
test: add tiny.epub fixture with prose, code, table, figure

Three-chapter EPUB covering the structural elements the parser
must handle. Generated by tests/fixtures/build_tiny_epub.py and
committed as a binary so tests are deterministic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Stage 1 — Parse

**Files:**
- Create: `audiobook/parse.py`, `tests/test_parse.py`
- Modify: `audiobook/cli.py` (add `parse` subcommand)

- [ ] **Step 4.1: Write the failing tests**

```python
# tests/test_parse.py
from __future__ import annotations

import json
from pathlib import Path

from audiobook.models import ChapterRaw
from audiobook.parse import parse_epub


def test_parse_produces_chapter_files(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "tiny.epub"
    chapters = parse_epub(src, scratch)

    raw_dir = scratch / "chapters" / "raw"
    files = sorted(raw_dir.glob("*.json"))
    assert len(files) == 3
    assert len(chapters) == 3
    for f in files:
        ChapterRaw.model_validate_json(f.read_text())


def test_parse_emits_book_full_text(repo_root: Path, scratch: Path) -> None:
    parse_epub(repo_root / "tests" / "fixtures" / "tiny.epub", scratch)
    md = (scratch / "book_full_text.md").read_text()
    assert "[code block]" in md  # code stripped to a marker
    assert "Tiny Technical Book" in md or "Introduction" in md


def test_parse_detects_features(repo_root: Path, scratch: Path) -> None:
    parse_epub(repo_root / "tests" / "fixtures" / "tiny.epub", scratch)
    raw_dir = scratch / "chapters" / "raw"
    files = sorted(raw_dir.glob("*.json"))
    by_index = {ChapterRaw.model_validate_json(f.read_text()).index: ChapterRaw.model_validate_json(f.read_text()) for f in files}
    assert by_index[1].has_code is True
    assert by_index[2].has_tables is True


def test_parse_is_idempotent(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "tiny.epub"
    parse_epub(src, scratch)
    first = sorted((scratch / "chapters" / "raw").glob("*.json"))
    first_bytes = [p.read_bytes() for p in first]
    parse_epub(src, scratch)
    second = sorted((scratch / "chapters" / "raw").glob("*.json"))
    assert [p.read_bytes() for p in second] == first_bytes
```

- [ ] **Step 4.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_parse.py -v`
Expected: FAIL.

- [ ] **Step 4.3: Implement `audiobook/parse.py`**

```python
"""Stage 1 — EPUB parser. Reads an EPUB, emits per-chapter JSON and a
plaintext-ish book_full_text.md used by adaptation subagents."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import ebooklib  # type: ignore[import-untyped]
from bs4 import BeautifulSoup, Tag
from ebooklib import epub  # type: ignore[import-untyped]
from markdownify import markdownify  # type: ignore[import-untyped]

from audiobook.models import ChapterRaw
from audiobook.utils.slugify import slugify

_SKIP_PATTERNS = (
    re.compile(r"copyright", re.I),
    re.compile(r"acknowledg", re.I),
    re.compile(r"dedication", re.I),
    re.compile(r"^index$", re.I),
    re.compile(r"bibliograph", re.I),
)

_FEATURE_PRESERVED_TAGS = ("pre", "code", "table", "figure", "img")


@dataclass(slots=True)
class _ParsedChapter:
    index: int
    title: str
    source_spine_id: str
    html: str


def _likely_skip(title: str, text: str) -> bool:
    if any(p.search(title) for p in _SKIP_PATTERNS):
        return True
    # very short pages are boilerplate (copyright, dedication, etc.)
    return len(text.split()) < 40


def _resolve_titles(book: epub.EpubBook) -> dict[str, str]:
    """Map spine item href -> human title from nav doc or NCX."""
    titles: dict[str, str] = {}

    def walk(items: list) -> None:  # type: ignore[type-arg]
        for entry in items:
            if isinstance(entry, tuple):
                section, children = entry
                if hasattr(section, "href") and section.href:
                    titles[section.href.split("#")[0]] = section.title
                walk(children)
            elif hasattr(entry, "href") and entry.href:
                titles[entry.href.split("#")[0]] = entry.title

    if book.toc:
        walk(list(book.toc))
    return titles


def _chapter_text(soup: BeautifulSoup) -> str:
    return soup.get_text(" ", strip=True)


def _detect_features(soup: BeautifulSoup) -> tuple[bool, bool, bool]:
    has_code = soup.find(["pre", "code"]) is not None
    has_math = bool(soup.find("math")) or bool(soup.find(class_="math"))
    has_tables = soup.find("table") is not None
    return has_code, has_math, has_tables


def _strip_for_full_text(html: str) -> str:
    """Convert chapter HTML to lightly-cleaned markdown for book_full_text.md."""
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


def parse_epub(epub_path: Path, out_dir: Path) -> list[ChapterRaw]:
    """Parse an EPUB into per-chapter JSON files plus book_full_text.md.

    Returns the list of ChapterRaw written.
    """
    epub_path = Path(epub_path)
    out_dir = Path(out_dir)
    raw_dir = out_dir / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    book = epub.read_epub(str(epub_path))
    nav_titles = _resolve_titles(book)

    chapters: list[ChapterRaw] = []
    full_text_sections: list[str] = []
    index = 0

    for spine_id, _linear in book.spine:
        item = book.get_item_with_id(spine_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        href = item.get_name()
        soup = BeautifulSoup(item.get_content(), "lxml")
        for tag in soup.find_all(["script", "style", "nav"]):
            if isinstance(tag, Tag):
                tag.decompose()

        title = nav_titles.get(href)
        if not title:
            h = soup.find(["h1", "h2"])
            title = h.get_text(strip=True) if h else item.get_name()

        text = _chapter_text(soup)
        if _likely_skip(title, text):
            continue

        has_code, has_math, has_tables = _detect_features(soup)
        # Preserve the structural HTML for the LLM stage (we want it to see
        # <pre>/<code>/<table>/<figure> verbatim).
        chapter = ChapterRaw(
            index=index,
            title=title,
            source_spine_id=href,
            html=str(soup),
            word_count_estimate=len(text.split()),
            has_code=has_code,
            has_math=has_math,
            has_tables=has_tables,
        )
        out_path = raw_dir / f"{index:02d}_{slugify(title)}.json"
        out_path.write_text(chapter.model_dump_json(indent=2) + "\n")
        chapters.append(chapter)

        full_text_sections.append(f"# {title}\n\n{_strip_for_full_text(str(soup))}")
        index += 1

    (out_dir / "book_full_text.md").write_text("\n\n".join(full_text_sections) + "\n")
    return chapters
```

- [ ] **Step 4.4: Run, expect pass**

Run: `bin/audiobook-test tests/test_parse.py -v`
Expected: ALL PASS. If `_FEATURE_PRESERVED_TAGS` triggers a "unused" warning, leave it — referenced by docstring; or remove the constant.

- [ ] **Step 4.5: Wire the `parse` subcommand into the CLI**

Append to `audiobook/cli.py`:

```python
from pathlib import Path
from audiobook.parse import parse_epub as _parse_epub


@app.command("parse")
def parse(
    epub_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    out: Path = typer.Option(Path("./work"), "--out", help="Output work directory."),
) -> None:
    """Stage 1 — parse an EPUB into per-chapter JSON + book_full_text.md."""
    chapters = _parse_epub(epub_path, out)
    typer.echo(f"parsed {len(chapters)} chapters -> {out}")
```

- [ ] **Step 4.6: Verify the CLI manually**

Run: `bin/audiobook parse tests/fixtures/tiny.epub --out tests/_scratch/parse`
Expected: prints `parsed 3 chapters -> tests/_scratch/parse`; files exist under `tests/_scratch/parse/chapters/raw/`.

- [ ] **Step 4.7: Commit**

```bash
git add audiobook/parse.py audiobook/cli.py tests/test_parse.py
git commit -m "$(cat <<'EOF'
feat: stage 1 — EPUB parser

Implements parse_epub() with spine walking, nav/toc title resolution,
skip-section heuristics, feature detection (code/math/tables), and
book_full_text.md emission. Exposed via `audiobook parse`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Stage 2 — Validator for adapted JSON

**Files:**
- Create: `audiobook/adapt.py`, `tests/test_validate_adapted.py`, `tests/fixtures/adapted/*.json`
- Modify: `audiobook/cli.py`

- [ ] **Step 5.1: Create the JSON fixtures under `tests/fixtures/adapted/`**

`valid.json`:
```json
{"adapted_text": "This is a perfectly fine adapted chapter. It reads naturally and contains no forbidden artifacts.", "pronunciation_hints": [{"term": "kubectl", "spoken_as": "cube control", "reason": "CLI tool"}], "notes": ""}
```

`truncated.json` (raw text, not valid JSON):
```
{"adapted_text": "This response was cut off mid-string and never closed
```

`prose_wrapped.json`:
```
Sure, here is the JSON you requested:

{"adapted_text": "Body text.", "pronunciation_hints": [], "notes": ""}
```

`schema_mismatched.json`:
```json
{"adapted_text": "Text but missing required field name", "hints": [], "notes": ""}
```

`markdown_artifact.json`:
```json
{"adapted_text": "Here is some prose. <pre>then a code block</pre> and more prose.", "pronunciation_hints": [], "notes": ""}
```

`too_short.json` (will be checked against a source of ~100 words → adapted under 30%):
```json
{"adapted_text": "Tiny.", "pronunciation_hints": [], "notes": ""}
```

`too_long.json`:
```json
{"adapted_text": "PLACEHOLDER", "pronunciation_hints": [], "notes": ""}
```

For `too_long.json`, generate the body programmatically in the test rather than by hand (the placeholder is replaced at test setup).

- [ ] **Step 5.2: Write the failing test**

```python
# tests/test_validate_adapted.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from audiobook.adapt import (
    ValidationOutcome,
    validate_adapted_file,
    validate_adapted_dir,
)
from audiobook.models import ChapterRaw

FIXTURES = Path(__file__).parent / "fixtures" / "adapted"


def _make_raw(scratch: Path, index: int, title: str, word_count: int) -> Path:
    raw_dir = scratch / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    body = " ".join(["word"] * word_count)
    c = ChapterRaw(
        index=index,
        title=title,
        source_spine_id=f"ch{index:02d}.xhtml",
        html=f"<p>{body}</p>",
        word_count_estimate=word_count,
        has_code=False,
        has_math=False,
        has_tables=False,
    )
    f = raw_dir / f"{index:02d}_{title}.json"
    f.write_text(c.model_dump_json())
    return f


def test_valid_passes(scratch: Path) -> None:
    raw = _make_raw(scratch, 0, "intro", 100)
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    (adapted_dir / "00_intro.json").write_text((FIXTURES / "valid.json").read_text())
    outcome = validate_adapted_file(raw, adapted_dir / "00_intro.json")
    assert outcome.ok, outcome


@pytest.mark.parametrize(
    "fixture,expected_kind",
    [
        ("truncated.json", "json_parse_error"),
        ("prose_wrapped.json", "json_parse_error"),
        ("schema_mismatched.json", "schema_error"),
        ("markdown_artifact.json", "markdown_artifact"),
    ],
)
def test_known_bad_fixtures_rejected(
    scratch: Path, fixture: str, expected_kind: str
) -> None:
    raw = _make_raw(scratch, 0, "x", 100)
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    (adapted_dir / "00_x.json").write_text((FIXTURES / fixture).read_text())
    outcome = validate_adapted_file(raw, adapted_dir / "00_x.json")
    assert not outcome.ok
    assert outcome.error_kind == expected_kind, outcome


def test_too_short_flagged(scratch: Path) -> None:
    raw = _make_raw(scratch, 0, "x", 200)  # source 200 words; adapted "Tiny." → <30%
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    (adapted_dir / "00_x.json").write_text((FIXTURES / "too_short.json").read_text())
    outcome = validate_adapted_file(raw, adapted_dir / "00_x.json")
    assert not outcome.ok
    assert outcome.error_kind == "length_anomaly"


def test_too_long_flagged(scratch: Path) -> None:
    raw = _make_raw(scratch, 0, "x", 100)  # source 100 → adapted 200+ is >110%
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    body = " ".join(["wordy"] * 250)
    payload = {"adapted_text": body, "pronunciation_hints": [], "notes": ""}
    (adapted_dir / "00_x.json").write_text(json.dumps(payload))
    outcome = validate_adapted_file(raw, adapted_dir / "00_x.json")
    assert not outcome.ok
    assert outcome.error_kind == "length_anomaly"


def test_validate_dir_reports_each_chapter(scratch: Path) -> None:
    _make_raw(scratch, 0, "good", 100)
    _make_raw(scratch, 1, "bad", 100)
    adapted = scratch / "chapters" / "adapted"
    adapted.mkdir(parents=True)
    (adapted / "00_good.json").write_text((FIXTURES / "valid.json").read_text())
    (adapted / "01_bad.json").write_text((FIXTURES / "markdown_artifact.json").read_text())

    report = validate_adapted_dir(scratch)
    by_idx = {r.chapter_index: r for r in report.results}
    assert by_idx[0].ok
    assert not by_idx[1].ok
    assert by_idx[1].error_kind == "markdown_artifact"
```

- [ ] **Step 5.3: Run, expect fail**

Run: `bin/audiobook-test tests/test_validate_adapted.py -v`
Expected: FAIL.

- [ ] **Step 5.4: Implement `audiobook/adapt.py`**

```python
"""Stage 2 helpers — validators and merge utilities. Agent mode uses these
via the CLI; this module deliberately contains no LLM transport code."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from audiobook.models import ChapterAdapted, ChapterRaw, PronunciationHint

ErrorKind = Literal[
    "missing_file",
    "json_parse_error",
    "schema_error",
    "markdown_artifact",
    "length_anomaly",
    "empty_text",
]

_MARKDOWN_ARTIFACT_PATTERNS = (
    re.compile(r"<pre[\s>]"),
    re.compile(r"```"),
    re.compile(r"<table[\s>]"),
    re.compile(r"\$\$"),
    re.compile(r"<h1[\s>]", re.I),
)

LENGTH_RATIO_MIN = 0.30
LENGTH_RATIO_MAX = 1.10


@dataclass(slots=True)
class ValidationOutcome:
    chapter_index: int
    raw_path: Path
    adapted_path: Path
    ok: bool
    error_kind: ErrorKind | None = None
    detail: str = ""
    length_ratio: float | None = None


@dataclass(slots=True)
class ValidationReport:
    results: list[ValidationOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "results": [
                    {
                        "chapter_index": r.chapter_index,
                        "raw_path": str(r.raw_path),
                        "adapted_path": str(r.adapted_path),
                        "ok": r.ok,
                        "error_kind": r.error_kind,
                        "detail": r.detail,
                        "length_ratio": r.length_ratio,
                    }
                    for r in self.results
                ],
            },
            indent=2,
        )


def _markdown_artifact(text: str) -> str | None:
    for pat in _MARKDOWN_ARTIFACT_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def validate_adapted_file(raw_path: Path, adapted_path: Path) -> ValidationOutcome:
    raw = ChapterRaw.model_validate_json(Path(raw_path).read_text())
    outcome = ValidationOutcome(
        chapter_index=raw.index, raw_path=Path(raw_path), adapted_path=Path(adapted_path), ok=False
    )
    if not Path(adapted_path).exists():
        outcome.error_kind = "missing_file"
        outcome.detail = "adapted file does not exist"
        return outcome
    text = Path(adapted_path).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        outcome.error_kind = "json_parse_error"
        outcome.detail = f"line {exc.lineno} col {exc.colno}: {exc.msg}"
        return outcome
    try:
        adapted = ChapterAdapted.model_validate(data)
    except ValidationError as exc:
        outcome.error_kind = "schema_error"
        outcome.detail = str(exc).splitlines()[0]
        return outcome

    artifact = _markdown_artifact(adapted.adapted_text)
    if artifact:
        outcome.error_kind = "markdown_artifact"
        outcome.detail = f"matched: {artifact!r}"
        return outcome

    src_words = max(raw.word_count_estimate, 1)
    adp_words = len(adapted.adapted_text.split())
    ratio = adp_words / src_words
    outcome.length_ratio = ratio
    if ratio < LENGTH_RATIO_MIN or ratio > LENGTH_RATIO_MAX:
        outcome.error_kind = "length_anomaly"
        outcome.detail = f"ratio={ratio:.2f} outside [{LENGTH_RATIO_MIN}, {LENGTH_RATIO_MAX}]"
        return outcome

    outcome.ok = True
    return outcome


def validate_adapted_dir(work_dir: Path) -> ValidationReport:
    work_dir = Path(work_dir)
    raw_dir = work_dir / "chapters" / "raw"
    adapted_dir = work_dir / "chapters" / "adapted"
    report = ValidationReport()
    for raw_path in sorted(raw_dir.glob("*.json")):
        # adapted file mirrors the raw filename
        adapted_path = adapted_dir / raw_path.name
        report.results.append(validate_adapted_file(raw_path, adapted_path))
    return report
```

- [ ] **Step 5.5: Run, expect pass**

Run: `bin/audiobook-test tests/test_validate_adapted.py -v`
Expected: ALL PASS.

- [ ] **Step 5.6: Wire the `validate-adapted` subcommand**

Append to `audiobook/cli.py`:

```python
import sys
from audiobook.adapt import validate_adapted_dir as _validate_dir


@app.command("validate-adapted")
def validate_adapted(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),
) -> None:
    """Validate every chapters/adapted/*.json file. Emits JSON report on stdout."""
    report = _validate_dir(work_dir)
    typer.echo(report.to_json())
    sys.exit(0 if report.ok else 1)
```

- [ ] **Step 5.7: Verify the CLI manually**

Run: `mkdir -p tests/_scratch/v/chapters/{raw,adapted} && cp tests/fixtures/adapted/valid.json tests/_scratch/v/chapters/adapted/00_intro.json` then create a matching raw stub and run `bin/audiobook validate-adapted tests/_scratch/v`. Expect the JSON report on stdout.

- [ ] **Step 5.8: Commit**

```bash
git add audiobook/adapt.py audiobook/cli.py tests/test_validate_adapted.py \
        tests/fixtures/adapted/
git commit -m "$(cat <<'EOF'
feat: stage 2 validator for adapted chapter JSON

Adds validate_adapted_file / validate_adapted_dir with deterministic
error classification (json_parse_error, schema_error, markdown_artifact,
length_anomaly, missing_file). Emits machine-readable JSON for the
orchestrator to consume in agent mode.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: merge-pronunciation utility

**Files:**
- Create: `tests/test_merge_pronunciation.py`
- Modify: `audiobook/adapt.py`, `audiobook/cli.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_merge_pronunciation.py
from __future__ import annotations

import json
from pathlib import Path

from audiobook.adapt import merge_pronunciation


def _write_adapted(dir_: Path, name: str, hints: list[dict[str, str]]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(json.dumps({
        "adapted_text": "body",
        "pronunciation_hints": hints,
        "notes": "",
    }))


def test_dedupe_and_merge(scratch: Path) -> None:
    adapted = scratch / "chapters" / "adapted"
    _write_adapted(adapted, "00_a.json", [
        {"term": "kubectl", "spoken_as": "cube control", "reason": "CLI"},
        {"term": "SQL", "spoken_as": "sequel", "reason": "acronym"},
    ])
    _write_adapted(adapted, "01_b.json", [
        {"term": "kubectl", "spoken_as": "cube control", "reason": "CLI"},
        {"term": "k8s", "spoken_as": "kates", "reason": "acronym"},
    ])
    out = merge_pronunciation(scratch)
    assert out == scratch / "pronunciation.json"
    payload = json.loads(out.read_text())
    terms = {h["term"]: h["spoken_as"] for h in payload}
    assert terms == {"kubectl": "cube control", "SQL": "sequel", "k8s": "kates"}


def test_conflicting_spelling_keeps_first_and_notes_conflict(scratch: Path) -> None:
    adapted = scratch / "chapters" / "adapted"
    _write_adapted(adapted, "00_a.json", [{"term": "API", "spoken_as": "A P I", "reason": ""}])
    _write_adapted(adapted, "01_b.json", [{"term": "API", "spoken_as": "appy", "reason": ""}])
    out = merge_pronunciation(scratch)
    payload = json.loads(out.read_text())
    api = next(h for h in payload if h["term"] == "API")
    assert api["spoken_as"] == "A P I"
    assert "conflict" in api["reason"].lower()
```

- [ ] **Step 6.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_merge_pronunciation.py -v`
Expected: FAIL.

- [ ] **Step 6.3: Implement `merge_pronunciation` in `audiobook/adapt.py`**

Append to `audiobook/adapt.py`:

```python
def merge_pronunciation(work_dir: Path) -> Path:
    """Combine all chapters' pronunciation_hints into work/pronunciation.json.

    Deduplication policy: first occurrence wins for `spoken_as`; conflicting
    later spellings are recorded in the `reason` field so the user can review.
    """
    work_dir = Path(work_dir)
    adapted_dir = work_dir / "chapters" / "adapted"
    merged: dict[str, PronunciationHint] = {}
    conflicts: dict[str, set[str]] = {}

    for f in sorted(adapted_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            chapter = ChapterAdapted.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            continue
        for h in chapter.pronunciation_hints:
            if h.term not in merged:
                merged[h.term] = h
            elif merged[h.term].spoken_as != h.spoken_as:
                conflicts.setdefault(h.term, set()).add(h.spoken_as)

    out_list = []
    for term, hint in merged.items():
        reason = hint.reason
        if term in conflicts:
            alt = ", ".join(sorted(conflicts[term]))
            reason = (reason + " " if reason else "") + f"[conflict with: {alt}]"
        out_list.append({"term": hint.term, "spoken_as": hint.spoken_as, "reason": reason})

    out = work_dir / "pronunciation.json"
    out.write_text(json.dumps(out_list, indent=2) + "\n")
    return out
```

- [ ] **Step 6.4: Run, expect pass**

Run: `bin/audiobook-test tests/test_merge_pronunciation.py -v`
Expected: PASS.

- [ ] **Step 6.5: Wire CLI subcommand**

Append to `audiobook/cli.py`:

```python
from audiobook.adapt import merge_pronunciation as _merge_pron


@app.command("merge-pronunciation")
def merge_pron(work_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Merge per-chapter pronunciation hints into work/pronunciation.json."""
    out = _merge_pron(work_dir)
    typer.echo(f"wrote {out}")
```

- [ ] **Step 6.6: Commit**

```bash
git add audiobook/adapt.py audiobook/cli.py tests/test_merge_pronunciation.py
git commit -m "$(cat <<'EOF'
feat: merge-pronunciation deduplicates per-chapter hints

First occurrence of a term wins; conflicting spoken_as values from
later chapters are flagged in the `reason` field for human review.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update CLAUDE.md for the Docker workflow

**Files:**
- Modify: `CLAUDE.md`

No code, no tests — pure documentation work. The result is the runtime contract Claude Code follows in agent mode.

- [ ] **Step 7.1: Replace `CLAUDE.md` with the Docker-aware version**

Use the template below (it preserves the spec §6.2 orchestration logic and adds the three adaptations from the design doc §8):

```markdown
# Audiobook Pipeline — Orchestrator Instructions

You are orchestrating the conversion of an EPUB book into an audiobook. Follow this workflow exactly.

## How this project runs

This project uses one Docker container for stages 1, 2, 3, and 5, plus the host environment for stage 4 (TTS) and the orchestration you are performing right now. A wrapper script (`bin/audiobook`) routes each subcommand to the right place automatically — you never need to type `docker compose` or `uv run` directly.

- All paths in commands are **relative to the project root**, and resolve identically inside and outside the container thanks to a bind mount at `/workspace`.
- Stage 4 (`audiobook render`, `audiobook voice preview`) runs on the host via `uv` so it can use Apple Silicon's MPS GPU. Every other `audiobook ...` call runs in Docker. The wrapper handles this distinction; just use `audiobook <subcommand> ...`.
- If `bin/` is not in your PATH, invoke commands as `bin/audiobook ...`.

## Setup verification

1. Confirm `./input/book.epub` exists. If missing, stop and ask the user.
2. Confirm `./voice/reference.wav` exists. If a voice preview has not been generated for it:
   - Run: `bin/audiobook voice validate ./voice/reference.wav`
   - Run: `bin/audiobook voice preview ./voice/reference.wav`
   - Ask the user to listen to `./voice/preview.wav` and confirm before continuing.
3. Read `config.toml` to confirm `adapt.mode = "agent"` and `adapt.concurrency` (default 8).

## Stage 1 — Parse

Run: `bin/audiobook parse ./input/book.epub --out ./work`
Verify `./work/chapters/raw/` is populated and `./work/book_full_text.md` exists.

## Stage 2 — Adapt (your main job as orchestrator)

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

- [ ] **Step 7.2: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: rewrite CLAUDE.md for the Docker + host hybrid workflow

Same orchestration logic as spec §6.2; commands now go through the
bin/audiobook wrapper. Adds a "How this project runs" preamble that
explains the host vs Docker split so future Claude Code instances
understand why `render` is host-only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Stage 3 — Chunk

**Files:**
- Create: `audiobook/chunk.py`, `tests/test_chunk.py`
- Modify: `audiobook/cli.py`

- [ ] **Step 8.1: Write the failing tests**

```python
# tests/test_chunk.py
from __future__ import annotations

import json
from pathlib import Path

from audiobook.chunk import (
    apply_pronunciation,
    chunk_chapter,
    pack_sentences,
)
from audiobook.models import ChapterAdapted, ChapterChunks, PronunciationHint


def test_apply_pronunciation_acronym_case_sensitive() -> None:
    hints = [
        PronunciationHint(term="SQL", spoken_as="sequel", reason=""),
        PronunciationHint(term="kubectl", spoken_as="cube control", reason=""),
    ]
    text = "We deploy SQL queries via kubectl. (Note: 'sql' as a word is not replaced.)"
    out = apply_pronunciation(text, hints)
    assert "sequel" in out
    assert "cube control" in out
    assert "sql" in out  # lowercase preserved


def test_pack_sentences_under_max_chars() -> None:
    sentences = [
        "Short one.",
        "This is a slightly longer sentence that fits.",
        "Another short.",
        "X.",
    ]
    chunks = pack_sentences(sentences, max_chars=80, min_orphan_chars=20)
    assert all(len(c) <= 80 for c in chunks)
    # X. should be merged into the previous chunk (short orphan rule)
    assert not any(c == "X." for c in chunks)


def test_chunk_chapter_writes_expected_structure(scratch: Path) -> None:
    adapted = ChapterAdapted(
        adapted_text=(
            "Paragraph one sentence one. Paragraph one sentence two.\n\n"
            "Paragraph two opens here. It contains two sentences.\n\n"
            "Final paragraph."
        ),
        pronunciation_hints=[],
        notes="",
    )
    cc = chunk_chapter(
        index=0,
        title="Intro",
        adapted=adapted,
        pronunciation=[],
        max_chars=400,
        paragraph_silence_ms=400,
        section_silence_ms=1200,
    )
    assert isinstance(cc, ChapterChunks)
    assert len(cc.chunks) >= 3
    # Trailing silence after the last chunk in each paragraph should be 400 ms.
    para_breaks = [c for c in cc.chunks if c.trailing_silence_ms == 400]
    assert len(para_breaks) >= 2  # two paragraph boundaries
```

- [ ] **Step 8.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_chunk.py -v`
Expected: FAIL.

- [ ] **Step 8.3: Implement `audiobook/chunk.py`**

```python
"""Stage 3 — sentence segmentation, pronunciation pass, greedy packing."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pysbd  # type: ignore[import-untyped]

from audiobook.models import ChapterAdapted, ChapterChunks, Chunk, PronunciationHint

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)


def apply_pronunciation(text: str, hints: list[PronunciationHint]) -> str:
    """Whole-word find-replace. Case-sensitive for terms that are all-uppercase
    (acronyms) or mixed-case (CLI tool names); case-insensitive otherwise."""
    result = text
    for h in hints:
        case_sensitive = h.term != h.term.lower()
        pattern = r"\b" + re.escape(h.term) + r"\b"
        flags = 0 if case_sensitive else re.IGNORECASE
        result = re.sub(pattern, h.spoken_as, result, flags=flags)
    return result


def pack_sentences(sentences: list[str], max_chars: int, min_orphan_chars: int) -> list[str]:
    """Greedy-pack sentences into chunks ≤ max_chars without splitting sentences.

    Sentences shorter than ``min_orphan_chars`` are merged with the next chunk
    so a tiny "X." doesn't end up alone (Chatterbox handles short utterances
    poorly).
    """
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    pending_short: str | None = None

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append(" ".join(buf).strip())
            buf = []
            buf_len = 0

    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        if pending_short is not None:
            s = pending_short + " " + s
            pending_short = None
        if len(s) < min_orphan_chars:
            pending_short = s
            continue
        # +1 for the joining space
        if buf_len + len(s) + (1 if buf else 0) > max_chars and buf:
            flush()
        buf.append(s)
        buf_len += len(s) + (1 if len(buf) > 1 else 0)

    if pending_short is not None:
        # No more sentences to merge with — append to last chunk if possible.
        if chunks and len(chunks[-1]) + 1 + len(pending_short) <= max_chars:
            chunks[-1] = chunks[-1] + " " + pending_short
        elif buf:
            buf.append(pending_short)
        else:
            chunks.append(pending_short)
        pending_short = None
    flush()
    return chunks


def chunk_chapter(
    *,
    index: int,
    title: str,
    adapted: ChapterAdapted,
    pronunciation: list[PronunciationHint],
    max_chars: int,
    paragraph_silence_ms: int,
    section_silence_ms: int,
) -> ChapterChunks:
    text = apply_pronunciation(adapted.adapted_text, pronunciation + adapted.pronunciation_hints)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    all_chunks: list[Chunk] = []
    chunk_id = 0
    for p_i, paragraph in enumerate(paragraphs):
        section_break = paragraph.strip() == "---"
        if section_break:
            # Encode as a zero-length text? No — instead bump the previous chunk's
            # trailing silence to the section value.
            if all_chunks:
                all_chunks[-1] = Chunk(
                    id=all_chunks[-1].id,
                    text=all_chunks[-1].text,
                    trailing_silence_ms=section_silence_ms,
                )
            continue
        sentences = _SEGMENTER.segment(paragraph)
        packed = pack_sentences(list(sentences), max_chars=max_chars, min_orphan_chars=20)
        for i, ptext in enumerate(packed):
            is_last_in_paragraph = i == len(packed) - 1
            trailing = paragraph_silence_ms if is_last_in_paragraph and p_i < len(paragraphs) - 1 else 0
            all_chunks.append(Chunk(id=f"{chunk_id:04d}", text=ptext, trailing_silence_ms=trailing))
            chunk_id += 1

    return ChapterChunks(index=index, title=title, chunks=all_chunks)


def chunk_work_dir(work_dir: Path, *, max_chars: int, paragraph_silence_ms: int, section_silence_ms: int) -> int:
    """Chunk every adapted file in work_dir. Skips chapters whose chunks already exist."""
    work_dir = Path(work_dir)
    adapted_dir = work_dir / "chapters" / "adapted"
    chunks_dir = work_dir / "chapters" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = work_dir / "chapters" / "raw"

    # Load pronunciation.json if present
    pron_path = work_dir / "pronunciation.json"
    pron: list[PronunciationHint] = []
    if pron_path.exists():
        for item in json.loads(pron_path.read_text()):
            pron.append(PronunciationHint(**item))

    count = 0
    for adapted_path in sorted(adapted_dir.glob("*.json")):
        out_path = chunks_dir / adapted_path.name
        if out_path.exists():
            continue
        adapted = ChapterAdapted.model_validate_json(adapted_path.read_text())
        # Index + title come from the corresponding raw file
        raw_path = raw_dir / adapted_path.name
        if not raw_path.exists():
            continue
        raw_data = json.loads(raw_path.read_text())
        chunks = chunk_chapter(
            index=raw_data["index"],
            title=raw_data["title"],
            adapted=adapted,
            pronunciation=pron,
            max_chars=max_chars,
            paragraph_silence_ms=paragraph_silence_ms,
            section_silence_ms=section_silence_ms,
        )
        out_path.write_text(chunks.model_dump_json(indent=2) + "\n")
        count += 1
    return count
```

- [ ] **Step 8.4: Run, expect pass**

Run: `bin/audiobook-test tests/test_chunk.py -v`
Expected: PASS.

- [ ] **Step 8.5: Wire CLI subcommand**

Append to `audiobook/cli.py`:

```python
from audiobook.chunk import chunk_work_dir as _chunk_dir
from audiobook.config import load_config


@app.command("chunk")
def chunk_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),
) -> None:
    """Stage 3 — chunk adapted chapters into TTS-sized pieces."""
    cfg = load_config(config)
    n = _chunk_dir(
        work_dir,
        max_chars=cfg.chunk.max_chars,
        paragraph_silence_ms=cfg.chunk.paragraph_silence_ms,
        section_silence_ms=cfg.chunk.section_silence_ms,
    )
    typer.echo(f"chunked {n} chapters")
```

- [ ] **Step 8.6: Commit**

```bash
git add audiobook/chunk.py audiobook/cli.py tests/test_chunk.py
git commit -m "$(cat <<'EOF'
feat: stage 3 — sentence segmentation and greedy chunk packing

Pysbd-based sentence segmentation, pronunciation pass with case-sensitive
acronym matching, greedy ≤max_chars packer that merges short-orphan
sentences forward, and silence-annotation per paragraph/section.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: state.json management

**Files:**
- Create: `audiobook/state.py`, `tests/test_state.py`
- Modify: `audiobook/cli.py`

- [ ] **Step 9.1: Write the failing test**

```python
# tests/test_state.py
from __future__ import annotations

import json
from pathlib import Path

from audiobook.state import State, load_state, save_state


def test_initialize_and_roundtrip(scratch: Path) -> None:
    s = State(epub_sha256="deadbeef", adapt_mode="agent")
    save_state(scratch, s)
    loaded = load_state(scratch)
    assert loaded.epub_sha256 == "deadbeef"
    assert loaded.adapt_mode == "agent"


def test_partial_update_persists(scratch: Path) -> None:
    s = State(epub_sha256="d", adapt_mode="agent")
    s.stages_completed["parse"] = True
    s.stages_completed["adapt"] = {"00": "done", "01": "failed"}
    save_state(scratch, s)
    raw = json.loads((scratch / "state.json").read_text())
    assert raw["stages_completed"]["parse"] is True
    assert raw["stages_completed"]["adapt"]["01"] == "failed"
```

- [ ] **Step 9.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_state.py -v`
Expected: FAIL.

- [ ] **Step 9.3: Implement `audiobook/state.py`**

```python
"""work/state.json management. Schema mirrors source-spec §13."""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CostLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    usd_total: float = 0.0
    note: str = "agent mode — no per-token billing; counts against Claude Code subscription limits"


class FailureEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    chapter: str
    reason: str


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epub_sha256: str
    started_at: str = Field(default_factory=lambda: _dt.datetime.now(_dt.UTC).isoformat())
    adapt_mode: str = "agent"
    stages_completed: dict[str, Any] = Field(
        default_factory=lambda: {
            "parse": False,
            "adapt": {},
            "chunk": [],
            "render": {},
            "assemble": False,
        }
    )
    cost: CostLedger = Field(default_factory=CostLedger)
    voice_reference_sha256: str = ""
    voice_preview_done: bool = False
    failures: list[FailureEntry] = Field(default_factory=list)


def state_path(work_dir: Path) -> Path:
    return Path(work_dir) / "state.json"


def save_state(work_dir: Path, state: State) -> None:
    p = state_path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(state.model_dump_json(indent=2) + "\n")


def load_state(work_dir: Path) -> State:
    return State.model_validate_json(state_path(work_dir).read_text())
```

- [ ] **Step 9.4: Run, expect pass**

Run: `bin/audiobook-test tests/test_state.py -v`
Expected: PASS.

- [ ] **Step 9.5: Add a `status` CLI subcommand**

Append to `audiobook/cli.py`:

```python
from audiobook.state import load_state


@app.command("status")
def status(work_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Print a human-readable summary of work/state.json."""
    try:
        state = load_state(work_dir)
    except FileNotFoundError:
        typer.echo("no state.json yet — nothing has run")
        raise typer.Exit(0)
    typer.echo(state.model_dump_json(indent=2))
```

- [ ] **Step 9.6: Commit**

```bash
git add audiobook/state.py audiobook/cli.py tests/test_state.py
git commit -m "$(cat <<'EOF'
feat: state.json schema and load/save helpers

Pydantic-modeled state file matching source-spec §13. Adds the
`audiobook status` CLI subcommand for human-readable summaries.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Voice validate + audio utilities (Docker-runnable)

**Files:**
- Create: `audiobook/voice.py`, `audiobook/utils/audio.py`, `tests/test_voice_validate.py`
- Modify: `audiobook/cli.py`

- [ ] **Step 10.1: Write the failing test**

```python
# tests/test_voice_validate.py
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from audiobook.voice import VoiceValidationResult, validate_voice_reference


def _write_wav(p: Path, *, duration_s: float, sr: int, channels: int, amplitude: float) -> None:
    n = int(duration_s * sr)
    if channels == 1:
        data = (amplitude * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype(np.float32)
    else:
        mono = (amplitude * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype(np.float32)
        data = np.column_stack([mono] * channels)
    sf.write(p, data, sr, subtype="PCM_16")


def test_clean_reference_passes(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=12, sr=24000, channels=1, amplitude=0.4)
    r = validate_voice_reference(p)
    assert isinstance(r, VoiceValidationResult)
    assert r.ok, r.problems


def test_too_short_flagged(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=3, sr=24000, channels=1, amplitude=0.4)
    r = validate_voice_reference(p)
    assert not r.ok
    assert any("duration" in pr for pr in r.problems)


def test_wrong_sample_rate_warns_not_fails(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=12, sr=48000, channels=2, amplitude=0.4)
    r = validate_voice_reference(p)
    # Resampling/downmix is automatic at use-time → warning, not failure
    assert r.ok or any("resamp" in pr.lower() or "downmix" in pr.lower() for pr in r.problems + r.warnings)


def test_clipping_detected(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=12, sr=24000, channels=1, amplitude=0.999)
    r = validate_voice_reference(p)
    assert any("clip" in pr.lower() for pr in r.problems + r.warnings)
```

- [ ] **Step 10.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_voice_validate.py -v`
Expected: FAIL.

- [ ] **Step 10.3: Implement `audiobook/utils/audio.py`**

```python
"""Shared audio utilities (silence padding, format checks). No torch here."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), always_2d=True)
    return data.astype(np.float32), sr


def write_wav_with_trailing_silence(
    path: Path, samples: np.ndarray, sample_rate: int, trailing_silence_ms: int
) -> None:
    if trailing_silence_ms > 0:
        n_silence = int(sample_rate * trailing_silence_ms / 1000)
        silence = np.zeros((n_silence,) + samples.shape[1:], dtype=samples.dtype)
        samples = np.concatenate([samples, silence], axis=0)
    sf.write(str(path), samples, sample_rate, subtype="PCM_16")


def db_level(samples: np.ndarray) -> float:
    """Return peak dBFS (negative or zero)."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 0:
        return -float("inf")
    return 20 * float(np.log10(peak))
```

- [ ] **Step 10.4: Implement `audiobook/voice.py`**

```python
"""Voice reference validation. No TTS dependency here — runs in Docker."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass(slots=True)
class VoiceValidationResult:
    path: Path
    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, object] = field(default_factory=dict)


_MIN_DURATION_S = 5.0
_RECOMMENDED_MIN_S = 10.0
_RECOMMENDED_MAX_S = 20.0
_TARGET_SR = 24000


def validate_voice_reference(path: Path) -> VoiceValidationResult:
    path = Path(path)
    res = VoiceValidationResult(path=path, ok=False)
    if not path.exists():
        res.problems.append(f"file does not exist: {path}")
        return res
    try:
        info = sf.info(str(path))
        data, sr = sf.read(str(path), always_2d=True)
    except Exception as exc:  # noqa: BLE001
        res.problems.append(f"failed to read audio: {exc}")
        return res

    duration = info.frames / info.samplerate
    res.info = {
        "duration_s": duration,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "subtype": info.subtype,
        "frames": info.frames,
    }

    if duration < _MIN_DURATION_S:
        res.problems.append(f"duration {duration:.1f}s below minimum {_MIN_DURATION_S}s")
    elif duration < _RECOMMENDED_MIN_S or duration > _RECOMMENDED_MAX_S:
        res.warnings.append(
            f"duration {duration:.1f}s outside recommended {_RECOMMENDED_MIN_S}-{_RECOMMENDED_MAX_S}s"
        )

    if info.samplerate != _TARGET_SR:
        res.warnings.append(
            f"sample rate {info.samplerate} != {_TARGET_SR}; will resample on use"
        )
    if info.channels > 1:
        res.warnings.append(f"{info.channels}-channel file; will downmix to mono on use")

    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak >= 0.99:
        res.warnings.append("audio is clipping (peak ≥ -0.1 dBFS); consider re-recording at lower gain")
    rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) if data.size else 0.0
    if rms < 1e-3:
        res.problems.append("recording is essentially silent")

    res.ok = not res.problems
    return res
```

- [ ] **Step 10.5: Run, expect pass**

Run: `bin/audiobook-test tests/test_voice_validate.py -v`
Expected: PASS.

- [ ] **Step 10.6: Wire `voice validate` CLI**

Append to `audiobook/cli.py`:

```python
from audiobook.voice import validate_voice_reference

voice_app = typer.Typer(name="voice", help="Voice reference utilities.")
app.add_typer(voice_app)


@voice_app.command("validate")
def voice_validate(path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    r = validate_voice_reference(path)
    typer.echo(f"path: {r.path}")
    for k, v in r.info.items():
        typer.echo(f"  {k}: {v}")
    for p in r.problems:
        typer.echo(f"PROBLEM: {p}")
    for w in r.warnings:
        typer.echo(f"warning: {w}")
    if not r.ok:
        raise typer.Exit(1)
    typer.echo("ok")
```

- [ ] **Step 10.7: Commit**

```bash
git add audiobook/voice.py audiobook/utils/audio.py audiobook/cli.py \
        tests/test_voice_validate.py
git commit -m "$(cat <<'EOF'
feat: voice reference validation (Docker-runnable)

Format/duration/clipping/SNR checks that don't require any TTS model.
Resample + downmix are warnings (auto-handled at use-time), not
failures. Silence and missing-file are hard failures.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Stage 4 — Render plumbing & host install script

**Files:**
- Create: `audiobook/render.py`, `scripts/host-install.sh`
- Modify: `audiobook/cli.py`

This task scaffolds the host-only path. No automated TTS test per source-spec §18. Tests cover everything *around* the TTS call (resumability, sidecar writes, mocked render).

- [ ] **Step 11.1: Write failing tests for render plumbing (Docker-runnable, TTS mocked)**

```python
# tests/test_render_plumbing.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from audiobook.models import ChapterChunks, Chunk
from audiobook.render import render_chapter_chunks


def _fake_tts(text: str, *, voice_conditioning: Any, **_: Any) -> tuple[np.ndarray, int]:
    """Return 1s of sine at 24kHz regardless of text — used to test plumbing."""
    sr = 24000
    n = sr
    return (0.1 * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype(np.float32), sr


def test_render_writes_per_chunk_wavs(scratch: Path) -> None:
    cc = ChapterChunks(
        index=0,
        title="Intro",
        chunks=[
            Chunk(id="0000", text="hello", trailing_silence_ms=0),
            Chunk(id="0001", text="world", trailing_silence_ms=400),
        ],
    )
    out_dir = scratch / "audio" / "chunks" / "00_intro"
    render_chapter_chunks(cc, out_dir=out_dir, tts_callable=_fake_tts, voice_conditioning=None)
    files = sorted(out_dir.glob("*.wav"))
    assert [f.name for f in files] == ["0000.wav", "0001.wav"]
    # Second chunk has 400ms trailing silence appended (1s+0.4s ≈ 1.4s)
    data, sr = sf.read(str(files[1]))
    assert sr == 24000
    assert 1.3 * sr < len(data) < 1.6 * sr


def test_render_skips_existing(scratch: Path) -> None:
    cc = ChapterChunks(
        index=0, title="X",
        chunks=[Chunk(id="0000", text="hi", trailing_silence_ms=0)],
    )
    out_dir = scratch / "ch"
    out_dir.mkdir(parents=True)
    existing = out_dir / "0000.wav"
    sf.write(str(existing), np.zeros(2400, dtype=np.float32), 24000, subtype="PCM_16")
    mtime = existing.stat().st_mtime

    calls = {"n": 0}

    def boom(*_a: Any, **_k: Any) -> tuple[np.ndarray, int]:
        calls["n"] += 1
        return np.zeros(2400, dtype=np.float32), 24000

    render_chapter_chunks(cc, out_dir=out_dir, tts_callable=boom, voice_conditioning=None)
    assert calls["n"] == 0  # skipped existing
    assert existing.stat().st_mtime == mtime  # untouched


def test_render_writes_sidecar(scratch: Path) -> None:
    cc = ChapterChunks(
        index=0, title="X",
        chunks=[Chunk(id="0000", text="hi", trailing_silence_ms=0)],
    )
    out_dir = scratch / "ch"
    render_chapter_chunks(cc, out_dir=out_dir, tts_callable=_fake_tts, voice_conditioning=None)
    side = json.loads((out_dir / "0000.json").read_text())
    assert side["text"] == "hi"
    assert side["chunk_id"] == "0000"
```

- [ ] **Step 11.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_render_plumbing.py -v`
Expected: FAIL.

- [ ] **Step 11.3: Implement `audiobook/render.py`**

Note: this file imports `chatterbox_tts` lazily *inside* the helper that loads the model. The plumbing functions (which the tests exercise) do not import torch at all.

```python
"""Stage 4 — TTS rendering. Host-only path. Import torch lazily."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from audiobook.models import ChapterChunks
from audiobook.utils.audio import write_wav_with_trailing_silence

TTSCallable = Callable[..., tuple[np.ndarray, int]]


def render_chapter_chunks(
    cc: ChapterChunks,
    *,
    out_dir: Path,
    tts_callable: TTSCallable,
    voice_conditioning: Any,
) -> None:
    """Render every chunk in ``cc`` to ``out_dir/{chunk_id}.wav``.

    Skips chunks whose WAV already exists. Writes a JSON sidecar per chunk
    so failed chunks can be targeted for re-render.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for chunk in cc.chunks:
        wav_path = out_dir / f"{chunk.id}.wav"
        if wav_path.exists():
            continue
        samples, sr = tts_callable(chunk.text, voice_conditioning=voice_conditioning)
        write_wav_with_trailing_silence(wav_path, samples, sr, chunk.trailing_silence_ms)
        side = {
            "chunk_id": chunk.id,
            "text": chunk.text,
            "trailing_silence_ms": chunk.trailing_silence_ms,
            "sample_rate": sr,
        }
        (out_dir / f"{chunk.id}.json").write_text(json.dumps(side, indent=2))


def _load_chatterbox(device: str) -> tuple[Any, TTSCallable]:
    """Import torch + chatterbox lazily and return (model, callable)."""
    try:
        import torch  # noqa: F401
        from chatterbox.tts import ChatterboxTTS  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "Stage 4 requires the [render] extra. Run scripts/host-install.sh on the host."
        ) from exc

    model = ChatterboxTTS.from_pretrained(device=device)

    def _call(text: str, *, voice_conditioning: Any, **kwargs: Any) -> tuple[np.ndarray, int]:
        wav = model.generate(text=text, audio_prompt_path=voice_conditioning, **kwargs)
        # Chatterbox returns a torch.Tensor at 24kHz mono.
        return wav.squeeze().cpu().numpy().astype(np.float32), 24000

    return model, _call


def render_work_dir(work_dir: Path, *, device: str, workers: int, voice_path: Path) -> None:
    """Top-level entry. Loads Chatterbox once and renders every chapter."""
    from concurrent.futures import ThreadPoolExecutor

    work_dir = Path(work_dir)
    chunks_dir = work_dir / "chapters" / "chunks"
    audio_root = work_dir / "audio" / "chunks"
    audio_root.mkdir(parents=True, exist_ok=True)

    _, tts_callable = _load_chatterbox(device)

    def _one(chunks_path: Path) -> None:
        cc = ChapterChunks.model_validate_json(chunks_path.read_text())
        out_dir = audio_root / chunks_path.stem
        render_chapter_chunks(
            cc, out_dir=out_dir, tts_callable=tts_callable, voice_conditioning=str(voice_path)
        )

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, sorted(chunks_dir.glob("*.json"))))
```

- [ ] **Step 11.4: Run, expect pass**

Run: `bin/audiobook-test tests/test_render_plumbing.py -v`
Expected: PASS.

- [ ] **Step 11.5: Add CLI subcommands**

Append to `audiobook/cli.py`:

```python
from audiobook.render import render_work_dir


@app.command("render")
def render_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    voice: Path = typer.Option(Path("./voice/reference.wav"), exists=True, dir_okay=False),
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),
) -> None:
    """Stage 4 — render chunked text to WAVs. HOST ONLY (uses MPS)."""
    cfg = load_config(config)
    render_work_dir(work_dir, device=cfg.render.device, workers=cfg.render.workers, voice_path=voice)
    typer.echo("render complete")


@voice_app.command("preview")
def voice_preview(
    reference: Path = typer.Argument(..., exists=True, dir_okay=False),
    text: str = typer.Option(
        "When we examine the architecture of a distributed system, three concerns dominate: "
        "consistency, availability, and partition tolerance.",
        "--text",
    ),
    out: Path = typer.Option(Path("./voice/preview.wav"), "--out"),
) -> None:
    """Render a 30-second preview using the supplied reference voice. HOST ONLY."""
    from audiobook.render import _load_chatterbox  # local import
    _, tts = _load_chatterbox("mps")
    samples, sr = tts(text, voice_conditioning=str(reference))
    import soundfile as sf
    sf.write(str(out), samples, sr, subtype="PCM_16")
    typer.echo(f"wrote {out}")
```

- [ ] **Step 11.6: Create `scripts/host-install.sh`**

```bash
#!/usr/bin/env bash
# Sets up the host-side venv for stage 4 (render / voice preview).
# Run from the project root.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on PATH. Install via: brew install uv" >&2
  exit 1
fi

uv venv --python 3.12 .venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e ".[render]"

echo
echo "Host venv ready at .venv. Smoke check:"
echo "  uv run audiobook --help"
```

Make executable: `chmod +x scripts/host-install.sh`.

- [ ] **Step 11.7: Commit**

```bash
git add audiobook/render.py audiobook/cli.py scripts/host-install.sh \
        tests/test_render_plumbing.py
git commit -m "$(cat <<'EOF'
feat: stage 4 render plumbing + host install script

Chunk→WAV writer that skips existing outputs and produces a JSON
sidecar per chunk. chatterbox_tts is imported lazily inside the
model loader so Docker tests stay torch-free. scripts/host-install.sh
provisions the host venv with the [render] extra.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Stage 5 — Assemble (.m4b)

**Files:**
- Create: `audiobook/assemble.py`, `tests/test_assemble.py`
- Modify: `audiobook/cli.py`

- [ ] **Step 12.1: Write the failing tests**

```python
# tests/test_assemble.py
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from audiobook.assemble import (
    build_ffmetadata,
    assemble_book,
    chapter_durations,
)


def _write_chunk(path: Path, seconds: float, sr: int = 24000) -> None:
    n = int(seconds * sr)
    samples = (0.05 * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sr, subtype="PCM_16")


def _ffprobe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", "-show_chapters", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def test_chapter_durations_sum_per_chapter(scratch: Path) -> None:
    base = scratch / "audio" / "chunks"
    _write_chunk(base / "00_intro" / "0000.wav", 1.0)
    _write_chunk(base / "00_intro" / "0001.wav", 0.5)
    _write_chunk(base / "01_body" / "0000.wav", 2.0)
    durs = chapter_durations(scratch)
    assert abs(durs["00_intro"] - 1.5) < 0.05
    assert abs(durs["01_body"] - 2.0) < 0.05


def test_build_ffmetadata_has_chapters(scratch: Path) -> None:
    md = build_ffmetadata(
        title="T", author="A",
        chapters=[("Intro", 0.0, 1.5), ("Body", 1.5, 3.5)],
    )
    assert ";FFMETADATA1" in md
    assert "[CHAPTER]" in md
    assert "title=Intro" in md
    assert "title=Body" in md


def test_assemble_produces_playable_m4b(scratch: Path) -> None:
    # Build minimal work tree
    base = scratch / "audio" / "chunks"
    _write_chunk(base / "00_intro" / "0000.wav", 1.0)
    _write_chunk(base / "01_body" / "0000.wav", 1.0)
    out = scratch / "out.m4b"
    assemble_book(scratch, title="Tiny", author="Auth", out_path=out)
    assert out.exists() and out.stat().st_size > 0
    info = _ffprobe(out)
    assert info["format"]["format_name"].startswith("mov")
    assert len(info.get("chapters", [])) == 2
```

- [ ] **Step 12.2: Run, expect fail**

Run: `bin/audiobook-test tests/test_assemble.py -v`
Expected: FAIL.

- [ ] **Step 12.3: Implement `audiobook/assemble.py`**

```python
"""Stage 5 — concatenate chunks, write chapter markers, mux .m4b."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import soundfile as sf
from mutagen.mp4 import MP4, MP4Cover


def chapter_durations(work_dir: Path) -> dict[str, float]:
    """Return {chapter_dir_name: total_seconds} across all chunks per chapter."""
    work_dir = Path(work_dir)
    chunks_root = work_dir / "audio" / "chunks"
    out: dict[str, float] = {}
    for chap_dir in sorted(p for p in chunks_root.iterdir() if p.is_dir()):
        total = 0.0
        for wav in sorted(chap_dir.glob("*.wav")):
            info = sf.info(str(wav))
            total += info.frames / info.samplerate
        out[chap_dir.name] = total
    return out


def build_ffmetadata(*, title: str, author: str, chapters: list[tuple[str, float, float]]) -> str:
    """Return ffmetadata text. chapters = [(title, start_s, end_s), ...]."""
    lines = [
        ";FFMETADATA1",
        f"title={title}",
        f"artist={author}",
    ]
    for ch_title, start, end in chapters:
        lines += [
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(start * 1000)}",
            f"END={int(end * 1000)}",
            f"title={ch_title}",
        ]
    return "\n".join(lines) + "\n"


def _concat_chapter_to_wav(chap_dir: Path, dst: Path) -> None:
    """Concat all chunks in chap_dir into a single WAV using ffmpeg concat demuxer."""
    listfile = dst.with_suffix(".txt")
    files = sorted(chap_dir.glob("*.wav"))
    listfile.write_text("\n".join(f"file '{f.resolve()}'" for f in files) + "\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c", "copy", str(dst)],
        check=True, capture_output=True,
    )
    listfile.unlink(missing_ok=True)


def assemble_book(
    work_dir: Path,
    *,
    title: str,
    author: str,
    narrator: str = "",
    out_path: Path,
    bitrate_kbps: int = 64,
    cover_path: Path | None = None,
) -> None:
    work_dir = Path(work_dir)
    chunks_root = work_dir / "audio" / "chunks"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chap_dirs = sorted(p for p in chunks_root.iterdir() if p.is_dir())

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        chapter_wavs: list[Path] = []
        chapter_titles: list[str] = []
        chapter_lengths: list[float] = []

        for chap_dir in chap_dirs:
            wav = tmpdir / f"{chap_dir.name}.wav"
            _concat_chapter_to_wav(chap_dir, wav)
            chapter_wavs.append(wav)
            chapter_titles.append(chap_dir.name)
            chapter_lengths.append(sf.info(str(wav)).frames / sf.info(str(wav)).samplerate)

        # Concatenate all chapter wavs into one
        all_list = tmpdir / "all.txt"
        all_list.write_text("\n".join(f"file '{w.resolve()}'" for w in chapter_wavs) + "\n")
        full_wav = tmpdir / "all.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(all_list),
             "-c", "copy", str(full_wav)],
            check=True, capture_output=True,
        )

        # Build ffmetadata with chapter markers
        cum = 0.0
        chapter_tuples: list[tuple[str, float, float]] = []
        for t, dur in zip(chapter_titles, chapter_lengths, strict=True):
            chapter_tuples.append((t, cum, cum + dur))
            cum += dur
        ffmd = tmpdir / "chapters.txt"
        ffmd.write_text(build_ffmetadata(title=title, author=author, chapters=chapter_tuples))

        # Encode to AAC m4b with chapter metadata
        subprocess.run(
            ["ffmpeg", "-y",
             "-i", str(full_wav),
             "-i", str(ffmd),
             "-map_metadata", "1",
             "-c:a", "aac", "-b:a", f"{bitrate_kbps}k", "-ac", "1",
             "-f", "mp4",
             str(out_path)],
            check=True, capture_output=True,
        )

    # Embed extra tags + cover via mutagen
    mp4 = MP4(str(out_path))
    mp4.tags["\xa9nam"] = title
    mp4.tags["\xa9ART"] = author
    if narrator:
        mp4.tags["\xa9wrt"] = narrator
    if cover_path and cover_path.exists():
        with open(cover_path, "rb") as f:
            mp4.tags["covr"] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
    mp4.save()
```

- [ ] **Step 12.4: Run, expect pass**

Run: `bin/audiobook-test tests/test_assemble.py -v`
Expected: PASS.

- [ ] **Step 12.5: Wire CLI**

Append to `audiobook/cli.py`:

```python
from audiobook.assemble import assemble_book as _assemble


@app.command("assemble")
def assemble_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    title: str = typer.Option(..., "--title"),
    author: str = typer.Option(..., "--author"),
    narrator: str = typer.Option("", "--narrator"),
    out: Path = typer.Option(..., "--out"),
    cover: Path | None = typer.Option(None, "--cover", exists=True, dir_okay=False),
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),
) -> None:
    """Stage 5 — assemble final .m4b with chapter markers and tags."""
    cfg = load_config(config)
    _assemble(
        work_dir,
        title=title,
        author=author,
        narrator=narrator,
        out_path=out,
        bitrate_kbps=cfg.assemble.audio_bitrate_kbps,
        cover_path=cover,
    )
    typer.echo(f"wrote {out}")
```

- [ ] **Step 12.6: Commit**

```bash
git add audiobook/assemble.py audiobook/cli.py tests/test_assemble.py
git commit -m "$(cat <<'EOF'
feat: stage 5 — assemble .m4b with chapter markers and tags

Two-stage ffmpeg pipeline: per-chapter WAV concat → all-chapters WAV
→ AAC mux with ffmetadata-driven chapter markers. mutagen writes
title/author/narrator/cover tags. Verified via ffprobe in tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Final sweep — full test run + lint + type check

**Files:** none (verification only)

- [ ] **Step 13.1: Run the full test suite in Docker**

Run: `bin/audiobook-test -v`
Expected: ALL PASS.

- [ ] **Step 13.2: Lint**

Run: `docker compose run --rm audiobook ruff check audiobook tests`
Expected: no errors. If any, fix inline (most likely import order or unused vars).

- [ ] **Step 13.3: Type check**

Run: `docker compose run --rm audiobook mypy --strict audiobook`
Expected: no errors. If any, fix the offending function signatures.

- [ ] **Step 13.4: Commit any lint/type fixes**

```bash
git add -u
git commit -m "$(cat <<'EOF'
chore: ruff and mypy sweep

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)" || echo "nothing to commit"
```

- [ ] **Step 13.5: Verify the help output lists every expected subcommand**

Run: `bin/audiobook --help`
Expected: parse, chunk, render, validate-adapted, merge-pronunciation, assemble, status, voice (group).

---

## Self-review (run before declaring done)

**Source-spec coverage check:**

| Spec § | Requirement | Implemented in |
|---|---|---|
| §5  | Stage 1 parse | Task 4 |
| §6.1 | adapt_system.md verbatim | Task 1 |
| §6.2 | Orchestrator CLAUDE.md | Tasks 1, 7 |
| §6.4 | API-mode adapter | **Deferred to v2** (design §12) |
| §6.3 | Chat-mode adapter | **Deferred to v2** (design §12) |
| §7   | Stage 3 chunk | Task 8 |
| §8   | Stage 4 render | Task 11 (plumbing; manual gate for full TTS) |
| §8 voice setup | Voice validate/preview | Tasks 10, 11 |
| §9   | Stage 5 assemble | Task 12 |
| §11  | Project layout | Task 1 |
| §12  | config.toml | Task 1, loader Task 2 |
| §13  | state.json | Task 9 |
| §14  | Error handling at validator + render | Tasks 5, 11 |
| §15.3 | Resumability (skip done) | Tasks 4, 8, 11 |
| §17  | Build order | This entire plan |
| §18  | Tech stack choices | Task 1 pyproject.toml |

**Manual gates (not in this plan, executed by user after Task 13):**
- Source-spec §17 step 5 — prompt-quality iteration loop
- Source-spec §17 step 7 — full tiny-EPUB end-to-end in Claude Code with a real voice
- Source-spec §17 step 8 — real 500-page book
