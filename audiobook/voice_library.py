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
