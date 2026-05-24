"""Voice library: name → path resolution + save/list/rm operations.

A 'voice' is a 24 kHz mono PCM WAV file in `voices/<name>.wav`. The
resolver picks one based on explicit arg, config, or fallback chain.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf  # type: ignore[import-untyped]

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
