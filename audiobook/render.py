"""Stage 4 — TTS rendering. Host-only path. Import torch lazily."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]

from audiobook.models import ChapterChunks
from audiobook.utils.audio import write_wav_with_trailing_silence

TTSCallable = Callable[..., tuple[np.ndarray, int]]

RenderErrorKind = Literal["missing_wav", "unreadable_wav", "zero_duration"]


@dataclass(slots=True)
class RenderOutcome:
    chapter_index: int
    chunk_id: str
    wav_path: Path
    ok: bool
    error_kind: RenderErrorKind | None = None
    detail: str = ""
    duration_s: float | None = None


@dataclass(slots=True)
class RenderReport:
    results: list[RenderOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "results": [
                    {
                        "chapter_index": r.chapter_index,
                        "chunk_id": r.chunk_id,
                        "wav_path": str(r.wav_path),
                        "ok": r.ok,
                        "error_kind": r.error_kind,
                        "detail": r.detail,
                        "duration_s": r.duration_s,
                    }
                    for r in self.results
                ],
            },
            indent=2,
        )


def _check_one_wav(wav_path: Path) -> tuple[bool, RenderErrorKind | None, str, float | None]:
    if not wav_path.exists():
        return False, "missing_wav", "wav file does not exist", None
    try:
        info = sf.info(str(wav_path))
    except Exception as exc:  # soundfile raises RuntimeError for bad files
        return False, "unreadable_wav", f"soundfile: {exc}", None
    duration = info.frames / info.samplerate if info.samplerate else 0.0
    if duration <= 0.0:
        return False, "zero_duration", f"frames={info.frames} sr={info.samplerate}", duration
    return True, None, "", duration


def validate_render_dir(work_dir: Path) -> RenderReport:
    """Check that every chunk listed in chapters/chunks/*.json has a usable WAV
    under audio/chunks/<chapter>/<chunk_id>.wav.
    """
    work_dir = Path(work_dir)
    chunks_dir = work_dir / "chapters" / "chunks"
    audio_root = work_dir / "audio" / "chunks"
    report = RenderReport()
    for chunks_path in sorted(chunks_dir.glob("*.json")):
        cc = ChapterChunks.model_validate_json(chunks_path.read_text())
        chap_audio = audio_root / chunks_path.stem
        for chunk in cc.chunks:
            wav = chap_audio / f"{chunk.id}.wav"
            ok, kind, detail, duration = _check_one_wav(wav)
            report.results.append(
                RenderOutcome(
                    chapter_index=cc.index,
                    chunk_id=chunk.id,
                    wav_path=wav,
                    ok=ok,
                    error_kind=kind,
                    detail=detail,
                    duration_s=duration,
                )
            )
    return report


def render_chapter_chunks(
    cc: ChapterChunks,
    *,
    out_dir: Path,
    tts_callable: TTSCallable,
    voice_conditioning: Any,
    progress: Callable[[str], None] | None = None,
    tts_kwargs: dict[str, Any] | None = None,
) -> None:
    """Render every chunk in ``cc`` to ``out_dir/{chunk_id}.wav``.

    Skips chunks whose WAV already exists. Writes a JSON sidecar per chunk
    so failed chunks can be targeted for re-render. If ``progress`` is given,
    it's called with a short status line per chunk (rendered or skipped).
    """
    import time

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(cc.chunks)
    for i, chunk in enumerate(cc.chunks, 1):
        wav_path = out_dir / f"{chunk.id}.wav"
        if wav_path.exists():
            if progress:
                progress(f"[ch{cc.index:02d} {i}/{total}] {chunk.id} skipped (exists)")
            continue
        t0 = time.monotonic()
        samples, sr = tts_callable(
            chunk.text, voice_conditioning=voice_conditioning, **(tts_kwargs or {})
        )
        write_wav_with_trailing_silence(wav_path, samples, sr, chunk.trailing_silence_ms)
        side = {
            "chunk_id": chunk.id,
            "text": chunk.text,
            "trailing_silence_ms": chunk.trailing_silence_ms,
            "sample_rate": sr,
        }
        (out_dir / f"{chunk.id}.json").write_text(json.dumps(side, indent=2))
        if progress:
            progress(f"[ch{cc.index:02d} {i}/{total}] {chunk.id} rendered in {time.monotonic() - t0:.1f}s")


def _load_chatterbox(device: str) -> tuple[Any, TTSCallable]:
    """Import torch + chatterbox lazily and return (model, callable).

    Lazy import: the Docker image does NOT have torch installed, so importing
    audiobook.render must succeed without it. This function only runs on the
    host where the [render] extra is installed.
    """
    import os
    # Silence chatterbox's per-step sampling progress bars; they spam non-TTY logs.
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    try:
        import torch  # type: ignore[import-not-found]  # noqa: F401
        from chatterbox.tts import ChatterboxTTS  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Stage 4 requires the [render] extra. Run scripts/host-install.sh on the host."
        ) from exc

    model = ChatterboxTTS.from_pretrained(device=device)

    def _call(text: str, *, voice_conditioning: Any, **kwargs: Any) -> tuple[np.ndarray, int]:
        wav = model.generate(text=text, audio_prompt_path=voice_conditioning, **kwargs)
        # Chatterbox returns a torch.Tensor at 24kHz mono.
        return wav.squeeze().cpu().numpy().astype(np.float32), 24000

    return model, _call


def render_work_dir(
    work_dir: Path,
    *,
    device: str,
    workers: int,
    voice_path: Path,
    tts_kwargs: dict[str, Any] | None = None,
) -> None:
    """Top-level entry. Loads Chatterbox once and renders every chapter.

    ``tts_kwargs`` is forwarded to every ``ChatterboxTTS.generate`` call
    (e.g. ``{"exaggeration": 0.8, "cfg_weight": 0.7, "temperature": 0.7}``).
    """
    from concurrent.futures import ThreadPoolExecutor

    work_dir = Path(work_dir)
    chunks_dir = work_dir / "chapters" / "chunks"
    audio_root = work_dir / "audio" / "chunks"
    audio_root.mkdir(parents=True, exist_ok=True)

    _, tts_callable = _load_chatterbox(device)

    def _progress(line: str) -> None:
        print(line, flush=True)

    def _one(chunks_path: Path) -> None:
        cc = ChapterChunks.model_validate_json(chunks_path.read_text())
        out_dir = audio_root / chunks_path.stem
        render_chapter_chunks(
            cc,
            out_dir=out_dir,
            tts_callable=tts_callable,
            voice_conditioning=str(voice_path),
            progress=_progress,
            tts_kwargs=tts_kwargs,
        )

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, sorted(chunks_dir.glob("*.json"))))
