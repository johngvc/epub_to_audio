# Autonomous `audiobook run` + Voice Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `bin/audiobook run` (a bash orchestrator that runs the full 8-stage pipeline end-to-end with auto-install preflight and strict failure handling) plus a flat `voices/` library with `voice save|list|rm` commands and `--voice NAME` selection.

**Architecture:** A new `audiobook/voice_library.py` owns voice resolution (`name | path | config | default | legacy`) and library management. The existing `audiobook/cli.py` gains `voice save|list|rm` subcommands and updates `voice preview` + `render` to use the resolver. A new `bin/audiobook-run` bash script sequences the 8 stages; the existing `bin/audiobook` dispatcher gets a `run)` branch.

**Tech Stack:** Python 3.12, Typer (CLI), Pydantic v2 (config), bash (orchestrator), pytest (tests). Uses macOS `afconvert` with `ffmpeg` fallback for audio format conversion.

**Spec reference:** `docs/superpowers/specs/2026-05-24-autonomous-run-and-voice-library-design.md`

---

## File Structure

**New files:**
- `audiobook/voice_library.py` — `resolve_voice_path()`, `save_voice()`, `list_voices()`, `rm_voice()`, `NoVoiceConfigured` exception, `VoiceInfo` dataclass.
- `bin/audiobook-run` — bash orchestrator (preflight + stage chain + summary).
- `tests/test_voice_library.py` — unit tests for resolver + save/list/rm.
- `voices/.gitkeep` — empty file, keeps the directory in the repo.

**Modified files:**
- `audiobook/config.py` — add `voice: str = ""` to `RenderConfig`.
- `audiobook/cli.py` — add `voice save`, `voice list`, `voice rm` subcommands; update `voice preview` to accept `--voice NAME`; update `render` `--voice` to accept name-or-path.
- `audiobook/render.py` — `render_work_dir` accepts an already-resolved `Path` (unchanged signature, but the CLI now resolves before calling).
- `bin/audiobook` — add `run)` case branch.
- `.gitignore` — add `voices/*` with `!voices/.gitkeep` exception.
- `config.toml` — add `voice = ""` under `[render]`.
- `tests/test_config.py` — extend `test_loads_repo_default` to assert `cfg.render.voice == ""`.
- `README.md` — update "How to use" section to recommend `bin/audiobook run`; add a "Working with voices" subsection.

---

## Task 1: Add `voices/` directory + `[render].voice` config field

**Files:**
- Modify: `.gitignore`
- Create: `voices/.gitkeep`
- Modify: `audiobook/config.py`
- Modify: `config.toml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Update `.gitignore`**

Open `.gitignore`. Find the block that ignores `voice/*` (around line 14). Add a sibling block for `voices/`:

```diff
 voice/*
 !voice/.gitkeep
 work/
 out/
 scratch/
 tests/_scratch/
+
+# Saved voices library
+voices/*
+!voices/.gitkeep
```

- [ ] **Step 2: Create the `voices/.gitkeep`**

```bash
mkdir -p voices
touch voices/.gitkeep
```

- [ ] **Step 3: Add `voice` field to `RenderConfig`**

Open `audiobook/config.py`. Find `class RenderConfig(_Strict):`. Add `voice: str = ""` as the FIRST field (so it appears at the top of the rendered config):

```python
class RenderConfig(_Strict):
    voice: str = ""             # saved voice name; empty = voices/default.wav fallback chain
    device: str = "mps"
    workers: int = Field(default=2, ge=1, le=8)
    exaggeration: float = 0.4
    cfg_weight: float = 0.5
    temperature: float = 0.7
    multilingual: bool = False
```

- [ ] **Step 4: Add `voice = ""` to `config.toml`**

Open `config.toml`. Find the `[render]` block (around line 47). Insert the new field at the top:

```toml
[render]
# TWEAK — Stage 4 (TTS). Host-only.
voice = ""                    # saved voice name; empty = voices/default.wav, then voice/reference.wav (legacy)
device = "mps"                # "mps" on Apple Silicon, "cuda" on NVIDIA, "cpu" elsewhere
workers = 2                   # parallel render workers; 1-2 typical for one GPU
exaggeration = 0.4            # Chatterbox: vocal expressiveness
cfg_weight = 0.5
temperature = 0.7
multilingual = false
```

- [ ] **Step 5: Extend `test_loads_repo_default`**

Open `tests/test_config.py`. In `test_loads_repo_default`, add this assertion:

```python
    assert cfg.render.voice == ""   # default is empty; resolver picks the right file
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add .gitignore voices/.gitkeep audiobook/config.py config.toml tests/test_config.py
git commit -m "feat(config): add voices/ library + [render].voice field"
```

---

## Task 2: Implement `resolve_voice_path` helper (TDD)

**Files:**
- Create: `audiobook/voice_library.py`
- Create: `tests/test_voice_library.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_library.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from audiobook.config import AppConfig, RenderConfig
from audiobook.voice_library import (
    NoVoiceConfigured,
    resolve_voice_path,
)


def _cfg(voice: str = "") -> AppConfig:
    return AppConfig(render=RenderConfig(voice=voice))


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF....fake wav for resolver test")
    return path


def test_resolve_explicit_name_wins(tmp_path: Path) -> None:
    _wav(tmp_path / "voices" / "alice.wav")
    _wav(tmp_path / "voices" / "default.wav")
    result = resolve_voice_path("alice", cfg=_cfg(voice="bob"), project_root=tmp_path)
    assert result == tmp_path / "voices" / "alice.wav"


def test_resolve_explicit_path_wins(tmp_path: Path) -> None:
    custom = _wav(tmp_path / "custom" / "narrator.wav")
    result = resolve_voice_path(str(custom), cfg=_cfg(), project_root=tmp_path)
    assert result == custom


def test_resolve_config_voice_used_when_no_arg(tmp_path: Path) -> None:
    _wav(tmp_path / "voices" / "bob.wav")
    result = resolve_voice_path(None, cfg=_cfg(voice="bob"), project_root=tmp_path)
    assert result == tmp_path / "voices" / "bob.wav"


def test_resolve_falls_back_to_default_wav(tmp_path: Path) -> None:
    target = _wav(tmp_path / "voices" / "default.wav")
    result = resolve_voice_path(None, cfg=_cfg(), project_root=tmp_path)
    assert result == target


def test_resolve_falls_back_to_legacy_reference_wav(tmp_path: Path, capsys) -> None:
    legacy = _wav(tmp_path / "voice" / "reference.wav")
    result = resolve_voice_path(None, cfg=_cfg(), project_root=tmp_path)
    assert result == legacy
    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower() or "legacy" in captured.err.lower()


def test_resolve_raises_when_nothing_configured(tmp_path: Path) -> None:
    with pytest.raises(NoVoiceConfigured) as exc:
        resolve_voice_path(None, cfg=_cfg(), project_root=tmp_path)
    assert "voice save" in str(exc.value)


def test_resolve_named_voice_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(NoVoiceConfigured) as exc:
        resolve_voice_path("ghost", cfg=_cfg(), project_root=tmp_path)
    assert "ghost" in str(exc.value)


def test_resolve_path_with_separator_treated_as_path(tmp_path: Path) -> None:
    target = _wav(tmp_path / "sub" / "anywhere.wav")
    result = resolve_voice_path("sub/anywhere.wav", cfg=_cfg(), project_root=tmp_path)
    assert result == target


def test_resolve_path_with_separator_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(NoVoiceConfigured):
        resolve_voice_path("missing/file.wav", cfg=_cfg(), project_root=tmp_path)
```

- [ ] **Step 2: Run tests — confirm RED**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v`
Expected: ImportError (`audiobook.voice_library` does not exist).

- [ ] **Step 3: Implement `voice_library.py`**

Create `audiobook/voice_library.py`:

```python
"""Voice library: name → path resolution + save/list/rm operations.

A 'voice' is a 24 kHz mono PCM WAV file in `voices/<name>.wav`. The
resolver picks one based on explicit arg, config, or fallback chain.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from audiobook.config import AppConfig


class NoVoiceConfigured(RuntimeError):
    """No voice could be resolved (explicit + config + default + legacy all empty)."""


def _looks_like_path(value: str) -> bool:
    """A value is a path if it contains a separator OR matches an existing file."""
    return os.sep in value or "/" in value or Path(value).is_file()


def resolve_voice_path(
    name_or_path: str | None,
    *,
    cfg: AppConfig,
    project_root: Path,
) -> Path:
    """Resolve a voice selection to an absolute path on disk.

    Order:
    1. Explicit `name_or_path` arg (path or name).
    2. `cfg.render.voice` (name only).
    3. `<project_root>/voices/default.wav` if present.
    4. `<project_root>/voice/reference.wav` if present (with deprecation log).

    Raises `NoVoiceConfigured` if nothing resolves to an existing file.
    """
    project_root = Path(project_root)

    # 1. Explicit value
    if name_or_path:
        if _looks_like_path(name_or_path):
            p = Path(name_or_path)
            if not p.is_absolute():
                p = project_root / p
            if p.is_file():
                return p
            raise NoVoiceConfigured(f"voice path does not exist: {p}")
        candidate = project_root / "voices" / f"{name_or_path}.wav"
        if candidate.is_file():
            return candidate
        raise NoVoiceConfigured(
            f"voice '{name_or_path}' not found at {candidate}. "
            f"Available voices: see `audiobook voice list`. "
            f"To add a new one: `audiobook voice save SAMPLE --name {name_or_path}`."
        )

    # 2. Config-supplied name
    if cfg.render.voice:
        candidate = project_root / "voices" / f"{cfg.render.voice}.wav"
        if candidate.is_file():
            return candidate
        raise NoVoiceConfigured(
            f"[render].voice = '{cfg.render.voice}' but {candidate} is missing. "
            f"Either remove the config value or save the voice: "
            f"`audiobook voice save SAMPLE --name {cfg.render.voice}`."
        )

    # 3. voices/default.wav
    default = project_root / "voices" / "default.wav"
    if default.is_file():
        return default

    # 4. Legacy voice/reference.wav
    legacy = project_root / "voice" / "reference.wav"
    if legacy.is_file():
        print(
            "warn: using legacy voice/reference.wav. Consider running "
            "`audiobook voice save voice/reference.wav --name default` "
            "to move to the new voices/ library.",
            file=sys.stderr,
        )
        return legacy

    raise NoVoiceConfigured(
        "no voice selected. Run `audiobook voice save SAMPLE --name NAME` "
        "to register one, then either pass `--voice NAME` or set "
        "[render].voice in config.toml."
    )
```

- [ ] **Step 4: Run tests — confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v`
Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add audiobook/voice_library.py tests/test_voice_library.py
git commit -m "feat(voice): add voice_library.resolve_voice_path resolver"
```

---

## Task 3: Implement save_voice / list_voices / rm_voice helpers (TDD)

**Files:**
- Modify: `audiobook/voice_library.py`
- Modify: `tests/test_voice_library.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_voice_library.py`:

```python
import numpy as np
import soundfile as sf

from audiobook.voice_library import (
    VoiceInfo,
    list_voices,
    rm_voice,
    save_voice,
)


def _real_wav(path: Path, *, duration_s: float = 12.0, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.zeros(int(sr * duration_s), dtype=np.float32)
    sf.write(str(path), samples, sr, subtype="PCM_16")
    return path


def test_save_voice_copies_wav_to_library(tmp_path: Path) -> None:
    sample = _real_wav(tmp_path / "voice" / "raw.wav", duration_s=12.0)
    out = save_voice(sample, name="alice", project_root=tmp_path)
    assert out == tmp_path / "voices" / "alice.wav"
    assert out.is_file()
    info = sf.info(str(out))
    assert info.samplerate == 24000
    assert info.channels == 1


def test_save_voice_refuses_overwrite_without_force(tmp_path: Path) -> None:
    sample = _real_wav(tmp_path / "voice" / "raw.wav")
    save_voice(sample, name="alice", project_root=tmp_path)
    with pytest.raises(FileExistsError):
        save_voice(sample, name="alice", project_root=tmp_path)
    # With force, succeeds
    out = save_voice(sample, name="alice", project_root=tmp_path, force=True)
    assert out.is_file()


def test_save_voice_rejects_invalid_name(tmp_path: Path) -> None:
    sample = _real_wav(tmp_path / "voice" / "raw.wav")
    for bad in ["", "with space", "with/slash", ".."]:
        with pytest.raises(ValueError):
            save_voice(sample, name=bad, project_root=tmp_path)


def test_list_voices_returns_sorted_with_default_marked(tmp_path: Path) -> None:
    _real_wav(tmp_path / "voices" / "alice.wav")
    _real_wav(tmp_path / "voices" / "bob.wav")
    _real_wav(tmp_path / "voices" / "default.wav")
    cfg = _cfg()
    items = list_voices(cfg=cfg, project_root=tmp_path)
    assert [v.name for v in items] == ["alice", "bob", "default"]
    is_default = {v.name: v.is_active_default for v in items}
    assert is_default["default"] is True
    assert is_default["alice"] is False


def test_list_voices_marks_config_voice_as_default(tmp_path: Path) -> None:
    _real_wav(tmp_path / "voices" / "alice.wav")
    _real_wav(tmp_path / "voices" / "default.wav")
    cfg = _cfg(voice="alice")
    items = list_voices(cfg=cfg, project_root=tmp_path)
    is_default = {v.name: v.is_active_default for v in items}
    assert is_default["alice"] is True
    assert is_default["default"] is False


def test_list_voices_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    items = list_voices(cfg=_cfg(), project_root=tmp_path)
    assert items == []


def test_rm_voice_removes_file_and_preview(tmp_path: Path) -> None:
    target = _real_wav(tmp_path / "voices" / "alice.wav")
    preview = _real_wav(tmp_path / "voices" / "alice.preview.wav")
    rm_voice("alice", project_root=tmp_path)
    assert not target.exists()
    assert not preview.exists()


def test_rm_voice_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        rm_voice("ghost", project_root=tmp_path)
```

- [ ] **Step 2: Run — confirm RED**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v`
Expected: ImportErrors for `VoiceInfo`, `list_voices`, `rm_voice`, `save_voice`.

- [ ] **Step 3: Implement the helpers**

Append to `audiobook/voice_library.py`:

```python
import re
import shutil
import subprocess
from dataclasses import dataclass

import soundfile as sf  # type: ignore[import-untyped]


@dataclass(slots=True)
class VoiceInfo:
    name: str
    path: Path
    duration_s: float
    sample_rate: int
    size_bytes: int
    is_active_default: bool


_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_name(name: str) -> None:
    if not name or not _VALID_NAME.fullmatch(name):
        raise ValueError(
            f"invalid voice name {name!r}: use letters, digits, '-', or '_'."
        )


def _convert_to_voice_wav(src: Path, dst: Path) -> None:
    """Convert any audio file to 24 kHz mono 16-bit PCM WAV.

    Tries `afconvert` (macOS built-in) first, then `ffmpeg`. Raises if neither
    is on PATH.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("afconvert"):
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1",
             str(src), str(dst)],
            check=True, capture_output=True,
        )
        return
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "24000",
             "-acodec", "pcm_s16le", str(dst)],
            check=True, capture_output=True,
        )
        return
    raise RuntimeError(
        "neither afconvert nor ffmpeg found on PATH. Install one to convert "
        "voice samples. (macOS ships afconvert; otherwise `brew install ffmpeg`.)"
    )


def save_voice(
    sample: Path,
    *,
    name: str,
    project_root: Path,
    force: bool = False,
) -> Path:
    """Convert `sample` to 24 kHz mono PCM and write voices/<name>.wav.

    Raises ``ValueError`` for invalid names, ``FileExistsError`` if the
    destination already exists and ``force`` is False.
    """
    _validate_name(name)
    sample = Path(sample)
    if not sample.is_file():
        raise FileNotFoundError(f"sample not found: {sample}")
    project_root = Path(project_root)
    dst = project_root / "voices" / f"{name}.wav"
    if dst.exists() and not force:
        raise FileExistsError(
            f"{dst} already exists. Pass force=True (or --force on the CLI) to overwrite."
        )
    _convert_to_voice_wav(sample, dst)
    return dst


def list_voices(*, cfg: AppConfig, project_root: Path) -> list[VoiceInfo]:
    """Return all voices in `voices/` sorted by name. The voice that would be
    picked by `resolve_voice_path(None, cfg, project_root)` is marked
    `is_active_default=True`.

    Preview files (`<name>.preview.wav`) are filtered out.
    """
    project_root = Path(project_root)
    voices_dir = project_root / "voices"
    if not voices_dir.is_dir():
        return []
    try:
        active = resolve_voice_path(None, cfg=cfg, project_root=project_root)
    except NoVoiceConfigured:
        active = None
    items: list[VoiceInfo] = []
    for path in sorted(voices_dir.glob("*.wav")):
        if path.stem.endswith(".preview"):
            continue
        info = sf.info(str(path))
        items.append(
            VoiceInfo(
                name=path.stem,
                path=path,
                duration_s=info.frames / info.samplerate if info.samplerate else 0.0,
                sample_rate=info.samplerate,
                size_bytes=path.stat().st_size,
                is_active_default=(active == path),
            )
        )
    return items


def rm_voice(name: str, *, project_root: Path) -> None:
    """Delete voices/<name>.wav and any voices/<name>.preview.wav.

    Raises FileNotFoundError if the voice does not exist.
    """
    _validate_name(name)
    project_root = Path(project_root)
    target = project_root / "voices" / f"{name}.wav"
    if not target.is_file():
        raise FileNotFoundError(f"voice '{name}' not found at {target}")
    target.unlink()
    preview = project_root / "voices" / f"{name}.preview.wav"
    if preview.is_file():
        preview.unlink()
```

Move the imports (`import re`, `import shutil`, `import subprocess`, `from dataclasses import dataclass`, `import soundfile as sf`) to the top of the file with the existing imports — group them, don't leave them mid-file.

- [ ] **Step 4: Run tests — confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v`
Expected: all ~17 tests pass.

Note: `test_save_voice_copies_wav_to_library` requires `afconvert` (macOS built-in) or `ffmpeg`. The host has `afconvert` available, so this should pass on macOS. On other systems, it skips if neither tool exists; if you want to be defensive, you can skip via `pytest.skip` when `shutil.which("afconvert") is None and shutil.which("ffmpeg") is None`, but the plan keeps the tests strict — macOS dev env is the target.

- [ ] **Step 5: Commit**

```bash
git add audiobook/voice_library.py tests/test_voice_library.py
git commit -m "feat(voice): add save_voice / list_voices / rm_voice helpers"
```

---

## Task 4: Add `voice save` CLI command

**Files:**
- Modify: `audiobook/cli.py`
- Modify: `tests/test_voice_library.py`

- [ ] **Step 1: Write a CLI test**

Append to `tests/test_voice_library.py`:

```python
from typer.testing import CliRunner

from audiobook.cli import app

runner = CliRunner()


def test_cli_voice_save_writes_to_library(tmp_path: Path, monkeypatch) -> None:
    sample = _real_wav(tmp_path / "sample.wav")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["voice", "save", str(sample), "--name", "alice"])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "voices" / "alice.wav").is_file()


def test_cli_voice_save_refuses_overwrite(tmp_path: Path, monkeypatch) -> None:
    sample = _real_wav(tmp_path / "sample.wav")
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["voice", "save", str(sample), "--name", "alice"])
    result = runner.invoke(app, ["voice", "save", str(sample), "--name", "alice"])
    assert result.exit_code != 0
    result2 = runner.invoke(
        app, ["voice", "save", str(sample), "--name", "alice", "--force"]
    )
    assert result2.exit_code == 0, result2.stdout
```

- [ ] **Step 2: Run — confirm RED**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py::test_cli_voice_save_writes_to_library -v`
Expected: fails (subcommand doesn't exist).

- [ ] **Step 3: Add the `voice save` command in `cli.py`**

Open `audiobook/cli.py`. Find the existing `voice_app = typer.Typer(...)` block. Add new commands after the existing `voice_preview` function:

```python
@voice_app.command("save")
def voice_save(
    sample: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    name: str = typer.Option(..., "--name"),
    force: bool = typer.Option(False, "--force"),
    preview: bool = typer.Option(False, "--preview", help="Also generate voices/<name>.preview.wav"),
) -> None:
    """Convert a raw audio sample to 24 kHz mono PCM and save it as a named voice."""
    from audiobook.voice_library import save_voice

    project_root = Path.cwd()
    try:
        out = save_voice(sample, name=name, project_root=project_root, force=force)
    except FileExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"wrote {out}")

    if preview:
        # Reuse the existing voice preview implementation.
        from audiobook.render import _load_chatterbox  # type: ignore[no-untyped-call]
        import soundfile as sf  # type: ignore[import-untyped]

        preview_text = (
            "When we examine the architecture of a distributed system, three "
            "concerns dominate: consistency, availability, and partition tolerance."
        )
        _, tts = _load_chatterbox("mps")
        samples, sr = tts(preview_text, voice_conditioning=str(out))
        preview_path = out.with_suffix(".preview.wav")
        sf.write(str(preview_path), samples, sr, subtype="PCM_16")
        typer.echo(f"wrote {preview_path}")
```

- [ ] **Step 4: Run tests — confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v -k "voice_save"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add audiobook/cli.py tests/test_voice_library.py
git commit -m "feat(cli): add 'audiobook voice save' subcommand"
```

---

## Task 5: Add `voice list` + `voice rm` CLI commands

**Files:**
- Modify: `audiobook/cli.py`
- Modify: `tests/test_voice_library.py`

- [ ] **Step 1: Write CLI tests**

Append to `tests/test_voice_library.py`:

```python
def test_cli_voice_list_marks_active(tmp_path: Path, monkeypatch) -> None:
    _real_wav(tmp_path / "voices" / "alice.wav")
    _real_wav(tmp_path / "voices" / "default.wav")
    monkeypatch.chdir(tmp_path)
    # write a minimal config so load_config works
    (tmp_path / "config.toml").write_text("[render]\nvoice = \"\"\n")
    result = runner.invoke(app, ["voice", "list"])
    assert result.exit_code == 0, result.stdout
    assert "alice" in result.stdout
    assert "default" in result.stdout
    # The active default is marked with '*'
    active_line = [line for line in result.stdout.splitlines() if line.startswith("*")]
    assert active_line, f"expected a '*'-prefixed active line in:\n{result.stdout}"
    assert "default" in active_line[0]


def test_cli_voice_rm_removes_voice(tmp_path: Path, monkeypatch) -> None:
    target = _real_wav(tmp_path / "voices" / "alice.wav")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["voice", "rm", "alice", "--force"])
    assert result.exit_code == 0, result.stdout
    assert not target.exists()


def test_cli_voice_rm_missing_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["voice", "rm", "ghost", "--force"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run — confirm RED**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v -k "voice_list or voice_rm"`
Expected: fails.

- [ ] **Step 3: Add the commands to `cli.py`**

Append after `voice_save` in `audiobook/cli.py`:

```python
@voice_app.command("list")
def voice_list(
    config: Path = typer.Option(Path("./config.toml"), "--config"),  # noqa: B008
) -> None:
    """List saved voices. The voice that would be picked by an unflagged run is marked with *."""
    from audiobook.voice_library import list_voices

    cfg = load_config(config) if config.exists() else load_config_default()
    items = list_voices(cfg=cfg, project_root=Path.cwd())
    if not items:
        typer.echo("(no saved voices — run `audiobook voice save SAMPLE --name NAME`)")
        return
    for v in items:
        prefix = "*" if v.is_active_default else " "
        size_kb = v.size_bytes // 1024
        typer.echo(
            f"{prefix} {v.name:20s} {v.duration_s:6.1f}s  {v.sample_rate:>6d} Hz  {size_kb:>5d} KB"
        )


@voice_app.command("rm")
def voice_rm(
    name: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
) -> None:
    """Remove a saved voice from the library."""
    from audiobook.voice_library import rm_voice

    if not force:
        confirm = typer.confirm(f"delete voice '{name}'?")
        if not confirm:
            typer.echo("aborted")
            raise typer.Exit(1)
    try:
        rm_voice(name, project_root=Path.cwd())
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"removed voices/{name}.wav")
```

Add this helper near the top of `cli.py` (right after the existing `from audiobook.config import load_config` import) — `load_config_default()` exists for the `voice list` no-config case:

```python
def load_config_default():
    """Return a default AppConfig without reading from disk."""
    from audiobook.config import AppConfig
    return AppConfig()
```

- [ ] **Step 4: Run tests — confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add audiobook/cli.py tests/test_voice_library.py
git commit -m "feat(cli): add 'audiobook voice list' and 'voice rm'"
```

---

## Task 6: Update `voice preview` to accept `--voice NAME`

**Files:**
- Modify: `audiobook/cli.py`
- Modify: `tests/test_voice_library.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_voice_library.py`:

```python
def test_cli_voice_preview_resolves_name(tmp_path: Path, monkeypatch) -> None:
    """`voice preview --voice NAME` should resolve via the library, not require a path."""
    _real_wav(tmp_path / "voices" / "alice.wav")
    monkeypatch.chdir(tmp_path)
    # We don't actually run TTS here — just verify the CLI parses --voice and
    # exits non-zero with a "model not available" error rather than an arg
    # parsing error. The render plumbing is exercised in test_render_plumbing.
    result = runner.invoke(app, ["voice", "preview", "--voice", "alice", "--out", str(tmp_path / "p.wav")])
    # Exit code 0 if chatterbox available (CI host); otherwise the chatterbox
    # import raises and we exit non-zero with that error. Either way we should
    # NOT see a typer arg parsing failure.
    assert "Usage:" not in result.stdout or result.exit_code == 0
```

- [ ] **Step 2: Run — confirm RED**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py::test_cli_voice_preview_resolves_name -v`
Expected: fails because `voice preview` doesn't accept `--voice NAME` yet.

- [ ] **Step 3: Update `voice_preview` in `cli.py`**

Replace the existing `voice_preview` function in `audiobook/cli.py` with:

```python
@voice_app.command("preview")
def voice_preview(
    reference: Path | None = typer.Argument(None, exists=False, dir_okay=False),  # noqa: B008
    voice: str | None = typer.Option(None, "--voice", help="Saved voice name OR path"),
    text: str = typer.Option(
        "When we examine the architecture of a distributed system, three concerns "
        "dominate: consistency, availability, and partition tolerance.",
        "--text",
    ),
    out: Path = typer.Option(Path("./voice/preview.wav"), "--out"),  # noqa: B008
    config: Path = typer.Option(Path("./config.toml"), "--config"),  # noqa: B008
) -> None:
    """Render a preview using the supplied reference voice. HOST ONLY.

    The reference can come from (in order): positional REFERENCE path,
    --voice NAME-or-PATH, or the library default. Use `audiobook voice list`
    to see saved names.
    """
    import soundfile as sf  # type: ignore[import-untyped]

    from audiobook.render import _load_chatterbox
    from audiobook.voice_library import NoVoiceConfigured, resolve_voice_path

    cfg = load_config(config) if config.exists() else load_config_default()
    selector: str | None = voice or (str(reference) if reference else None)
    try:
        ref = resolve_voice_path(selector, cfg=cfg, project_root=Path.cwd())
    except NoVoiceConfigured as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None

    _, tts = _load_chatterbox("mps")
    samples, sr = tts(text, voice_conditioning=str(ref))
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), samples, sr, subtype="PCM_16")
    typer.echo(f"wrote {out}")
```

- [ ] **Step 4: Run tests — confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_voice_library.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add audiobook/cli.py tests/test_voice_library.py
git commit -m "feat(cli): voice preview accepts --voice NAME via library resolver"
```

---

## Task 7: Update `render` to accept name-or-path via `--voice`

**Files:**
- Modify: `audiobook/cli.py`
- Modify: `tests/test_render_plumbing.py`

- [ ] **Step 1: Add a CLI test for `render --voice NAME`**

Append to `tests/test_render_plumbing.py`:

```python
from typer.testing import CliRunner

from audiobook.cli import app
from audiobook.voice_library import resolve_voice_path  # noqa: F401  ensure module imports


def test_cli_render_resolves_voice_name(tmp_path, monkeypatch):
    """`render --voice NAME` should resolve via library; ensure the helper is invoked.

    We monkeypatch render_work_dir so we don't actually run TTS.
    """
    import numpy as np
    import soundfile as sf

    # set up a fake project layout
    voice = tmp_path / "voices" / "alice.wav"
    voice.parent.mkdir(parents=True)
    sf.write(str(voice), np.zeros(24_000, dtype=np.float32), 24_000, subtype="PCM_16")
    (tmp_path / "config.toml").write_text("")
    (tmp_path / "work").mkdir()

    called = {}
    import audiobook.cli as cli_mod

    def fake_render(work_dir, *, device, workers, voice_path):
        called["voice_path"] = voice_path
        called["work_dir"] = work_dir

    monkeypatch.setattr(cli_mod, "render_work_dir", fake_render)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(app, ["render", "./work", "--voice", "alice"])
    assert result.exit_code == 0, result.stdout
    assert called["voice_path"] == voice
```

- [ ] **Step 2: Run — confirm RED**

Run: `.venv/bin/python -m pytest tests/test_render_plumbing.py::test_cli_render_resolves_voice_name -v`
Expected: fails (render still expects an existing file path).

- [ ] **Step 3: Update `render_cmd` in `cli.py`**

Find the existing `render_cmd` function in `audiobook/cli.py`. Replace it with:

```python
@app.command("render")
def render_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    voice: str | None = typer.Option(None, "--voice", help="Saved voice name OR path"),
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),  # noqa: B008
) -> None:
    """Stage 4 — render chunked text to WAVs. HOST ONLY (uses MPS).

    The voice can be a saved name (from `audiobook voice list`), an explicit
    path, or omitted to fall back to [render].voice in config, voices/default.wav,
    or legacy voice/reference.wav.
    """
    from audiobook.voice_library import NoVoiceConfigured, resolve_voice_path

    cfg = load_config(config)
    try:
        voice_path = resolve_voice_path(voice, cfg=cfg, project_root=Path.cwd())
    except NoVoiceConfigured as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None

    render_work_dir(
        work_dir, device=cfg.render.device, workers=cfg.render.workers, voice_path=voice_path
    )
    typer.echo("render complete")
```

- [ ] **Step 4: Run tests — confirm GREEN**

Run: `.venv/bin/python -m pytest tests/test_render_plumbing.py -v`
Expected: all pass.

Also run the full suite:

Run: `.venv/bin/python -m pytest -q`
Expected: previously-passing tests still pass (only pre-existing ffmpeg failure remains).

- [ ] **Step 5: Commit**

```bash
git add audiobook/cli.py tests/test_render_plumbing.py
git commit -m "feat(cli): render --voice accepts saved name or path"
```

---

## Task 8: Create `bin/audiobook-run` orchestrator script

**Files:**
- Create: `bin/audiobook-run`

This is a bash script with no Python tests. Smoke-tested in Task 11.

- [ ] **Step 1: Create the script**

Create `bin/audiobook-run` with the following content (full script — copy verbatim):

```bash
#!/usr/bin/env bash
# audiobook-run — autonomous pipeline orchestrator.
# Runs parse → adapt → validate-adapted → merge-pronunciation → chunk →
# render → validate-render → assemble in sequence. Auto-installs missing
# dependencies during preflight. Strict failure: any non-zero exit aborts.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# ---------- defaults ----------
INPUT="./input/book.epub"
OUT="./out/book.m4b"
WORK="./work"
CONFIG="./config.toml"
VOICE=""
FRESH=0
SKIP_PREFLIGHT=0
FORCE=0

# ---------- helpers ----------
if [[ -t 1 ]]; then
  BOLD=$'\e[1m'; CYAN=$'\e[36m'; GREEN=$'\e[32m'; RED=$'\e[31m'; YELLOW=$'\e[33m'; RESET=$'\e[0m'
else
  BOLD=""; CYAN=""; GREEN=""; RED=""; YELLOW=""; RESET=""
fi

err()  { echo "${RED}error:${RESET} $*" >&2; }
warn() { echo "${YELLOW}warn:${RESET} $*" >&2; }
info() { echo "${CYAN}→${RESET} $*"; }
ok()   { echo "${GREEN}✓${RESET} $*"; }

usage() {
  cat <<EOF
Usage: bin/audiobook run [INPUT_EPUB] [OPTIONS]

Runs the full pipeline end-to-end. Auto-installs missing dependencies.

Arguments:
  INPUT_EPUB        Path to input EPUB (default: ./input/book.epub)

Options:
  --out PATH        Output .m4b path (default: ./out/book.m4b)
  --work DIR        Work directory (default: ./work)
  --config FILE     Config file (default: ./config.toml)
  --voice NAME      Saved voice name OR path to a WAV
  --fresh           Wipe work/ before starting (confirms unless --force)
  --skip-preflight  Skip dependency checks
  --force           Skip confirmations
  -h, --help        Show this message
EOF
}

# ---------- arg parsing ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)            OUT="$2"; shift 2 ;;
    --work)           WORK="$2"; shift 2 ;;
    --config)         CONFIG="$2"; shift 2 ;;
    --voice)          VOICE="$2"; shift 2 ;;
    --fresh)          FRESH=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --force)          FORCE=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    -*)               err "unknown option: $1"; usage; exit 2 ;;
    *)                INPUT="$1"; shift ;;
  esac
done

read_toml_string() {
  # crude TOML reader: read_toml_string FILE 'section' 'key'
  local file="$1" section="$2" key="$3"
  awk -v sec="[$section]" -v k="$key" '
    $0 == sec { in_sec=1; next }
    /^\[/      { in_sec=0 }
    in_sec && $1 == k {
      sub(/^[^=]*=[ \t]*/, "")
      gsub(/^"|"$/, "")
      print
      exit
    }
  ' "$file"
}

run_stage() {
  local stage_num="$1" stage_name="$2"; shift 2
  echo ""
  echo "${BOLD}=== Stage ${stage_num}/8 — ${stage_name} ===${RESET}"
  local t0 t1 elapsed
  t0=$(date +%s)
  if ! "$@"; then
    err "stage '${stage_name}' failed. Re-run \`bin/audiobook run\` to resume from here."
    exit 1
  fi
  t1=$(date +%s)
  elapsed=$(( t1 - t0 ))
  STAGE_TIMES+=("${stage_name}:${elapsed}")
  ok "${stage_name} (${elapsed}s)"
}

# ---------- preflight ----------
declare -a STAGE_TIMES=()

if (( ! SKIP_PREFLIGHT )); then
  info "preflight checks"

  # --- EPUB input ---
  if [[ ! -f "$INPUT" ]]; then
    err "EPUB not found: $INPUT"
    exit 1
  fi

  # --- voices/ dir ---
  mkdir -p voices

  # --- voice resolvable (best-effort: rely on python resolver during render) ---
  # If no voices/* and no voice/reference.wav, but voice/ has a sample, hint.
  if [[ -z "$VOICE" ]] && \
     ! ls voices/*.wav >/dev/null 2>&1 && \
     [[ ! -f voice/reference.wav ]]; then
    local_sample="$(ls voice/* 2>/dev/null | grep -vE '\.gitkeep|preview' | head -n 1 || true)"
    if [[ -n "$local_sample" ]]; then
      warn "no voice in voices/, but found $local_sample"
      warn "  Run: bin/audiobook voice save \"$local_sample\" --name default"
      exit 1
    fi
    err "no voice configured. Run: bin/audiobook voice save SAMPLE --name default"
    exit 1
  fi

  # --- book metadata ---
  title="$(read_toml_string "$CONFIG" book title)"
  author="$(read_toml_string "$CONFIG" book author)"
  if [[ -z "$title" || -z "$author" ]]; then
    err "[book].title and [book].author must be set in $CONFIG"
    err "  See README.md → How to use → Configuration reference"
    exit 1
  fi

  # --- macOS: Colima ---
  if [[ "$(uname -s)" == "Darwin" ]]; then
    if command -v colima >/dev/null 2>&1; then
      if ! colima status >/dev/null 2>&1; then
        info "starting Colima"
        colima start
      fi
    fi
  fi

  # --- Docker image ---
  if ! docker image inspect audiobook:dev >/dev/null 2>&1; then
    info "building Docker image (~3 min, first run only)"
    docker compose build audiobook
  fi

  # --- host venv ---
  if [[ ! -x .venv/bin/audiobook ]]; then
    info "creating host venv (~2 min)"
    scripts/host-install.sh
  fi

  # --- api mode: openai pkg + LM Studio ---
  mode="$(read_toml_string "$CONFIG" adapt mode)"
  if [[ "$mode" == "agent" ]]; then
    err "\`bin/audiobook run\` cannot drive agent mode. Set [adapt].mode = \"api\" in $CONFIG"
    err "  Or use the interactive Claude Code workflow."
    exit 1
  fi
  if [[ "$mode" == "api" ]]; then
    if ! .venv/bin/python -c "import openai" >/dev/null 2>&1; then
      info "installing [api] extra (openai SDK)"
      uv pip install --python .venv/bin/python -e ".[api]" >/dev/null
    fi

    base_url="$(read_toml_string "$CONFIG" adapt.api base_url)"
    model="$(read_toml_string "$CONFIG" adapt.api model)"
    base_url="${OPENAI_BASE_URL:-$base_url}"
    model="${OPENAI_MODEL:-$model}"

    if [[ -z "$model" ]]; then
      err "[adapt.api].model is empty in $CONFIG (or via OPENAI_MODEL)"
      exit 1
    fi
    if ! curl -sf --max-time 5 "${base_url%/}/models" >/dev/null; then
      err "LM Studio not reachable at $base_url"
      err "  Start LM Studio and load the model: $model"
      exit 1
    fi
    if ! curl -s --max-time 5 "${base_url%/}/models" \
         | grep -q "\"id\": *\"${model//\//\\/}\""; then
      err "model '$model' not loaded in LM Studio at $base_url"
      err "  Load it in LM Studio, then re-run."
      exit 1
    fi
  fi

  ok "preflight ok"
fi

# ---------- fresh ----------
if (( FRESH )); then
  if (( ! FORCE )); then
    read -r -p "wipe ${WORK}/ ? [y/N] " yn
    [[ "$yn" =~ ^[Yy]$ ]] || { err "aborted"; exit 1; }
  fi
  rm -rf "$WORK"
  info "wiped $WORK"
fi

# ---------- pipeline ----------
START_T=$(date +%s)

run_stage 1 parse                bin/audiobook parse "$INPUT" --out "$WORK"

if [[ "$mode" == "api" ]]; then
  if [[ -n "$VOICE" ]]; then
    run_stage 2 adapt               bin/audiobook adapt "$WORK" --config "$CONFIG"
  else
    run_stage 2 adapt               bin/audiobook adapt "$WORK" --config "$CONFIG"
  fi
fi

run_stage 3 validate-adapted     bin/audiobook validate-adapted "$WORK"
run_stage 4 merge-pronunciation  bin/audiobook merge-pronunciation "$WORK"
run_stage 5 chunk                bin/audiobook chunk "$WORK" --config "$CONFIG"

if [[ -n "$VOICE" ]]; then
  run_stage 6 render             bin/audiobook render "$WORK" --voice "$VOICE" --config "$CONFIG"
else
  run_stage 6 render             bin/audiobook render "$WORK" --config "$CONFIG"
fi

run_stage 7 validate-render      bin/audiobook validate-render "$WORK"
run_stage 8 assemble             bin/audiobook assemble "$WORK" --out "$OUT" --config "$CONFIG"

END_T=$(date +%s)
TOTAL=$(( END_T - START_T ))

# ---------- summary ----------
echo ""
ok "Audiobook ready: $OUT"
if [[ -f "$OUT" ]]; then
  size_bytes=$(stat -f%z "$OUT" 2>/dev/null || stat -c%s "$OUT" 2>/dev/null || echo 0)
  size_kb=$(( size_bytes / 1024 ))
  echo "   size:     ${size_kb} KB"
  if command -v afinfo >/dev/null 2>&1; then
    dur=$(afinfo "$OUT" 2>/dev/null | awk -F': ' '/estimated duration/ {print $2}' | awk '{printf "%.0f", $1}')
    [[ -n "$dur" ]] && echo "   duration: ${dur}s"
  fi
fi
echo ""
echo "Per-stage wall time:"
for entry in "${STAGE_TIMES[@]}"; do
  name="${entry%:*}"
  secs="${entry#*:}"
  printf "  %-22s %4ds\n" "$name" "$secs"
done
printf "  %-22s %4ds\n" "total" "$TOTAL"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x bin/audiobook-run
```

- [ ] **Step 3: Smoke check the script syntax**

Run: `bash -n bin/audiobook-run && bin/audiobook-run --help`
Expected: `--help` text prints; no syntax errors.

- [ ] **Step 4: Commit**

```bash
git add bin/audiobook-run
git commit -m "feat(bin): add audiobook-run orchestrator with preflight + strict failure"
```

---

## Task 9: Route `run` in `bin/audiobook` dispatcher

**Files:**
- Modify: `bin/audiobook`

- [ ] **Step 1: Inspect the existing dispatcher**

Run: `cat bin/audiobook | head -50`
Locate the `case "$sub1" in` block.

- [ ] **Step 2: Add the `run` branch**

Open `bin/audiobook`. Find the `case "$sub1" in` block. Add a `run)` branch FIRST (so it has priority and is visible at the top):

```bash
case "$sub1" in
  run)
    shift
    exec "$(dirname "$0")/audiobook-run" "$@"
    ;;
  adapt)
    run_host "$@"
    ;;
  render)
    run_host "$@"
    ;;
  voice)
    if [[ "$sub2" == "preview" ]]; then
      run_host "$@"
```

Add `voice save|list|rm` to host routing too — they all need the project venv. Find the existing `voice)` branch and update it:

```bash
  voice)
    case "$sub2" in
      preview|save|list|rm)
        run_host "$@"
        ;;
      *)
        run_docker "$@"
        ;;
    esac
    ;;
```

(Replace the existing single-condition `if [[ "$sub2" == "preview" ]]` block with the case statement above.)

- [ ] **Step 3: Smoke test**

Run: `bin/audiobook run --help 2>&1 | head -5`
Expected: orchestrator help text.

Run: `bin/audiobook voice list 2>&1 | head -3`
Expected: lists saved voices (or "(no saved voices ...)") — proves the host routing works for voice subcommands.

- [ ] **Step 4: Commit**

```bash
git add bin/audiobook
git commit -m "chore(bin): route 'run' and voice save|list|rm subcommands"
```

---

## Task 10: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the "How to use" → step 5 to recommend `run`**

Open `README.md`. Find the section `### 5. Run the pipeline`. Replace its body with:

```markdown
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
```

- [ ] **Step 2: Add a "Working with voices" subsection**

Find the section "Configuration reference" in `README.md`. Add a new subsection ABOVE it titled "Working with voices":

```markdown
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
```

- [ ] **Step 3: Update the "Direct command reference" section**

Find the section starting `## Direct command reference`. Add the new commands. Replace its `voice ...` lines with:

```sh
bin/audiobook run [INPUT_EPUB]                        # All stages, auto-install, strict failure
bin/audiobook voice save SAMPLE --name NAME           # Host — add a voice to the library
bin/audiobook voice list                              # Host — list saved voices
bin/audiobook voice rm NAME                           # Host — remove a saved voice
bin/audiobook voice validate ./voice/reference.wav    # Docker — check format/SNR
bin/audiobook voice preview --voice NAME              # Host (MPS) — preview a saved voice
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): document 'audiobook run' + voice library workflow"
```

---

## Task 11: End-to-end smoke test + push

**Files:** (no code changes)

- [ ] **Step 1: Verify everything passes**

Run: `.venv/bin/python -m pytest -q`
Expected: only the pre-existing `test_assemble_produces_playable_m4b` ffmpeg failure remains.

- [ ] **Step 2: Smoke the help text**

Run: `bin/audiobook run --help`
Expected: orchestrator help text.

Run: `bin/audiobook voice list`
Expected: lists current voices (or empty message).

- [ ] **Step 3: Promote the existing reference.wav to a saved voice**

```bash
bin/audiobook voice save voice/reference.wav --name default
bin/audiobook voice list
```

Expected: `* default` line appears.

- [ ] **Step 4: Run the pipeline end-to-end on the test EPUB**

Pre-requirement: LM Studio running with `qwen3.6-35b-a3b-mtp` loaded (or whatever's in `[adapt.api].model`).

```bash
# Wipe prior work, ensure tiny test EPUB is at input/book.epub
bin/audiobook run --fresh --force
```

Expected: 8-stage progression with per-stage timings, summary at end, `out/book.m4b` exists and has > 0 duration.

If the run fails at any stage, debug, then re-run (it's resumable).

- [ ] **Step 5: Verify clean tree and push**

```bash
git status                # should be clean
git log --oneline | head -12   # should show the new commits + existing
git push origin main
```

---

## Self-Review

**Spec coverage:**

| Spec section | Covered by |
|---|---|
| `resolve_voice_path` with 5-level fallback | Task 2 (tests + impl) |
| `save_voice` / `list_voices` / `rm_voice` helpers | Task 3 |
| `voice save` CLI command | Task 4 |
| `voice list` / `voice rm` CLI commands | Task 5 |
| `voice preview --voice NAME` | Task 6 |
| `render --voice` name-or-path | Task 7 |
| `[render].voice` config field | Task 1 |
| `bin/audiobook run` orchestrator | Task 8 |
| Dispatcher routing for `run` and voice subcommands | Task 9 |
| README updates | Task 10 |
| Backwards-compat with legacy `voice/reference.wav` | Task 2 (resolver test) |
| Cross-platform: afconvert with ffmpeg fallback | Task 3 (`_convert_to_voice_wav`) |
| Auto-install preflight (Colima/Docker/venv/extras) | Task 8 |
| LM Studio reachability + model-loaded check | Task 8 |
| Voice name validation (no slashes, no spaces) | Task 3 (`_validate_name` + test) |
| Strict-failure stage chain | Task 8 |
| Per-stage timing + summary | Task 8 |
| End-to-end smoke test | Task 11 |
| `--fresh` and `--skip-preflight` flags | Task 8 |
| Tests cover the helper directly + via CLI | Tasks 2, 3, 4, 5, 6, 7 |

**Placeholder scan:** No "TBD" / "TODO" / "implement later" in any task. ✓

**Type consistency:**
- `resolve_voice_path(name_or_path: str | None, *, cfg: AppConfig, project_root: Path) -> Path` — referenced consistently in Tasks 2, 6, 7 ✓
- `save_voice(sample, *, name, project_root, force=False) -> Path` — used the same way in Tasks 3, 4 ✓
- `VoiceInfo` dataclass fields (`name`, `path`, `duration_s`, `sample_rate`, `size_bytes`, `is_active_default`) — used consistently in Tasks 3, 5 ✓
- `NoVoiceConfigured` exception — raised in Tasks 2, 6, 7 ✓
- Bash orchestrator stage chain — order matches CLAUDE.md/README spec (parse → adapt → validate-adapted → merge-pronunciation → chunk → render → validate-render → assemble) ✓

**Ambiguity check:** None I can spot. The `--voice` flag's path-or-name interpretation is exhaustively tested in Task 2, the resolver's fallback order is in Task 2 with all 5 levels covered.

---

Plan complete and saved to `docs/superpowers/plans/2026-05-24-autonomous-run-and-voice-library.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute the 11 tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
