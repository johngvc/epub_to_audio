from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from audiobook.models import ChapterChunks, Chunk
from audiobook.render import render_chapter_chunks
from audiobook.utils.audio import compress_silence


def _tone(n: int, sr: int = 24000) -> np.ndarray:
    return (0.2 * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype(np.float32)


def _trailing_ms(y: np.ndarray, sr: int = 24000, thr: float = 0.01) -> float:
    idx = np.where(np.abs(y) > thr)[0]
    return (len(y) - 1 - idx[-1]) / sr * 1000 if len(idx) else len(y) / sr * 1000


def _max_internal_gap_ms(y: np.ndarray, sr: int = 24000, thr: float = 0.01) -> float:
    idx = np.where(np.abs(y) > thr)[0]
    return (np.diff(idx).max() / sr * 1000) if len(idx) > 1 else 0.0


def test_compress_silence_trims_trailing_and_internal() -> None:
    sr = 24000
    sig = np.concatenate([
        _tone(sr // 5), np.zeros(sr * 5, dtype=np.float32),   # 5s internal gap
        _tone(sr // 5), np.zeros(sr * 5, dtype=np.float32),   # 5s trailing
    ])
    out = compress_silence(sig, sr, max_gap_ms=600, edge_ms=50)
    assert len(out) < len(sig)
    assert _trailing_ms(out, sr) < 200          # 5s trailing trimmed
    assert _max_internal_gap_ms(out, sr) < 900  # 5s internal capped to ~600ms


def test_compress_silence_noop_when_disabled() -> None:
    sig = np.concatenate([_tone(2400), np.zeros(72000, dtype=np.float32)])
    out = compress_silence(sig, 24000, max_gap_ms=0)
    assert np.array_equal(out, sig)


def test_compress_silence_leaves_clean_audio_unchanged() -> None:
    sig = _tone(24000)  # 1s continuous tone, no long silences
    out = compress_silence(sig, 24000)
    assert len(out) == len(sig)


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

    def fake_render(work_dir, *, engine="chatterbox", device, workers, voice_conditioning,
                    tts_kwargs=None, verbose=False, max_silence_ms=600):
        called["engine"] = engine
        called["voice_conditioning"] = voice_conditioning
        called["work_dir"] = work_dir
        called["tts_kwargs"] = tts_kwargs

    monkeypatch.setattr(cli_mod, "render_work_dir", fake_render)
    monkeypatch.chdir(tmp_path)

    from typer.testing import CliRunner

    from audiobook.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["render", "./work", "--voice", "alice"])
    assert result.exit_code == 0, result.stdout
    assert called["engine"] == "chatterbox"
    assert called["voice_conditioning"] == str(voice)  # resolved WAV path
    assert set(called["tts_kwargs"].keys()) == {"exaggeration", "cfg_weight", "temperature"}


def test_cli_render_kokoro_uses_voice_name(tmp_path, monkeypatch):
    """`render --engine kokoro --voice bm_george` passes the voice NAME (not a
    WAV path) and Kokoro tts_kwargs, skipping the WAV voice library."""
    (tmp_path / "config.toml").write_text("")
    (tmp_path / "work").mkdir()

    called = {}
    import audiobook.cli as cli_mod

    def fake_render(work_dir, *, engine="chatterbox", device, workers, voice_conditioning,
                    tts_kwargs=None, verbose=False, max_silence_ms=600):
        called["engine"] = engine
        called["voice_conditioning"] = voice_conditioning
        called["tts_kwargs"] = tts_kwargs

    monkeypatch.setattr(cli_mod, "render_work_dir", fake_render)
    monkeypatch.chdir(tmp_path)

    from typer.testing import CliRunner

    from audiobook.cli import app

    result = CliRunner().invoke(app, ["render", "./work", "--engine", "kokoro", "--voice", "bm_george"])
    assert result.exit_code == 0, result.stdout
    assert called["engine"] == "kokoro"
    assert called["voice_conditioning"] == "bm_george"
    assert "speed" in called["tts_kwargs"]


def test_render_chapter_chunks_forwards_tts_kwargs(scratch: Path) -> None:
    """Confirms exaggeration/cfg_weight/temperature reach the TTS callable.

    Regression: these were declared in config but the call site dropped them,
    so tuning had no effect on output until the kwarg plumbing was added.
    """
    seen: list[dict[str, Any]] = []

    def capturing_tts(text: str, *, voice_conditioning: Any, **kw: Any) -> tuple[np.ndarray, int]:
        seen.append(kw)
        return (0.1 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000)).astype(np.float32), 24000

    cc = ChapterChunks(
        index=0, title="X",
        chunks=[Chunk(id="0000", text="hi", trailing_silence_ms=0)],
    )
    render_chapter_chunks(
        cc,
        out_dir=scratch / "ch",
        tts_callable=capturing_tts,
        voice_conditioning=None,
        tts_kwargs={"exaggeration": 0.8, "cfg_weight": 0.7, "temperature": 0.6},
    )
    assert seen == [{"exaggeration": 0.8, "cfg_weight": 0.7, "temperature": 0.6}]
