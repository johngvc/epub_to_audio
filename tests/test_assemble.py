from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from audiobook.assemble import (
    assemble_book,
    build_ffmetadata,
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


def test_build_ffmetadata_has_chapters() -> None:
    md = build_ffmetadata(
        title="T", author="A",
        chapters=[("Intro", 0.0, 1.5), ("Body", 1.5, 3.5)],
    )
    assert ";FFMETADATA1" in md
    assert "[CHAPTER]" in md
    assert "title=Intro" in md
    assert "title=Body" in md


def test_assemble_produces_playable_m4b(scratch: Path) -> None:
    base = scratch / "audio" / "chunks"
    _write_chunk(base / "00_intro" / "0000.wav", 1.0)
    _write_chunk(base / "01_body" / "0000.wav", 1.0)
    out = scratch / "out.m4b"
    assemble_book(scratch, title="Tiny", author="Auth", out_path=out)
    assert out.exists() and out.stat().st_size > 0
    info = _ffprobe(out)
    assert info["format"]["format_name"].startswith("mov")
    assert len(info.get("chapters", [])) == 2
