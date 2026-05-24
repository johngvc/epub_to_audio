from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]

from audiobook.models import ChapterChunks, Chunk
from audiobook.render import validate_render_dir


def _make_chunks_file(scratch: Path, index: int, slug: str, chunk_ids: list[str]) -> Path:
    chunks_dir = scratch / "chapters" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    cc = ChapterChunks(
        index=index,
        title=slug,
        chunks=[Chunk(id=cid, text="hello world", trailing_silence_ms=0) for cid in chunk_ids],
    )
    p = chunks_dir / f"{index:02d}_{slug}.json"
    p.write_text(cc.model_dump_json())
    return p


def _write_wav(path: Path, *, duration_s: float = 0.5, sr: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.zeros(int(sr * duration_s), dtype=np.float32)
    sf.write(str(path), samples, sr, subtype="PCM_16")


def test_all_wavs_present_and_valid(scratch: Path) -> None:
    _make_chunks_file(scratch, 0, "intro", ["0000", "0001"])
    audio_dir = scratch / "audio" / "chunks" / "00_intro"
    _write_wav(audio_dir / "0000.wav")
    _write_wav(audio_dir / "0001.wav")

    report = validate_render_dir(scratch)
    assert report.ok, report.to_json()
    assert len(report.results) == 2


def test_missing_wav_flagged(scratch: Path) -> None:
    _make_chunks_file(scratch, 0, "intro", ["0000", "0001"])
    audio_dir = scratch / "audio" / "chunks" / "00_intro"
    _write_wav(audio_dir / "0000.wav")
    # 0001.wav intentionally not written

    report = validate_render_dir(scratch)
    assert not report.ok
    by_id = {r.chunk_id: r for r in report.results}
    assert by_id["0000"].ok
    assert by_id["0001"].error_kind == "missing_wav"


def test_zero_byte_wav_flagged(scratch: Path) -> None:
    _make_chunks_file(scratch, 0, "intro", ["0000"])
    audio_dir = scratch / "audio" / "chunks" / "00_intro"
    audio_dir.mkdir(parents=True)
    (audio_dir / "0000.wav").write_bytes(b"")

    report = validate_render_dir(scratch)
    assert not report.ok
    assert report.results[0].error_kind == "unreadable_wav"


def test_zero_duration_flagged(scratch: Path) -> None:
    _make_chunks_file(scratch, 0, "intro", ["0000"])
    audio_dir = scratch / "audio" / "chunks" / "00_intro"
    _write_wav(audio_dir / "0000.wav", duration_s=0.0)

    report = validate_render_dir(scratch)
    assert not report.ok
    assert report.results[0].error_kind == "zero_duration"


def test_aggregates_across_chapters(scratch: Path) -> None:
    _make_chunks_file(scratch, 0, "intro", ["0000"])
    _make_chunks_file(scratch, 1, "body", ["0000"])
    _write_wav(scratch / "audio" / "chunks" / "00_intro" / "0000.wav")
    # 01_body has no audio dir at all
    report = validate_render_dir(scratch)
    assert not report.ok
    by_chapter = {(r.chapter_index, r.chunk_id): r for r in report.results}
    assert by_chapter[(0, "0000")].ok
    assert by_chapter[(1, "0000")].error_kind == "missing_wav"
