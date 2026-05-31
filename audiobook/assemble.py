"""Stage 5 — concatenate chunks, write chapter markers, mux .m4b."""
from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import soundfile as sf  # type: ignore[import-untyped]
from mutagen.mp4 import MP4, MP4Cover

from audiobook.utils.progress import pct_line


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
    progress: Callable[[str], None] | None = None,
    verbose: bool = False,
) -> None:
    work_dir = Path(work_dir)
    chunks_root = work_dir / "audio" / "chunks"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chap_dirs = sorted(p for p in chunks_root.iterdir() if p.is_dir())
    total_chapters = len(chap_dirs)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        chapter_wavs: list[Path] = []
        chapter_titles: list[str] = []
        chapter_lengths: list[float] = []

        for pos, chap_dir in enumerate(chap_dirs, 1):
            wav = tmpdir / f"{chap_dir.name}.wav"
            _concat_chapter_to_wav(chap_dir, wav)
            chapter_wavs.append(wav)
            chapter_titles.append(chap_dir.name)
            info = sf.info(str(wav))
            chapter_lengths.append(info.frames / info.samplerate)
            if verbose and progress:
                progress(pct_line("assemble", pos, total_chapters, chap_dir.name))

        all_list = tmpdir / "all.txt"
        all_list.write_text("\n".join(f"file '{w.resolve()}'" for w in chapter_wavs) + "\n")
        full_wav = tmpdir / "all.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(all_list),
             "-c", "copy", str(full_wav)],
            check=True, capture_output=True,
        )

        cum = 0.0
        chapter_tuples: list[tuple[str, float, float]] = []
        for t, dur in zip(chapter_titles, chapter_lengths, strict=True):
            chapter_tuples.append((t, cum, cum + dur))
            cum += dur
        ffmd = tmpdir / "chapters.txt"
        ffmd.write_text(build_ffmetadata(title=title, author=author, chapters=chapter_tuples))

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

    mp4 = MP4(str(out_path))  # type: ignore[no-untyped-call]
    if mp4.tags is None:
        mp4.add_tags()  # type: ignore[no-untyped-call]
    assert mp4.tags is not None
    mp4.tags["\xa9nam"] = title
    mp4.tags["\xa9ART"] = author
    if narrator:
        mp4.tags["\xa9wrt"] = narrator
    if cover_path and cover_path.exists():
        with open(cover_path, "rb") as f:
            mp4.tags["covr"] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
    mp4.save()
