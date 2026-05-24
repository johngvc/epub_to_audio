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
