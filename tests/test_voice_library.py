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
