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
