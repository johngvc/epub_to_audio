"""Stage 4 — TTS rendering. Host-only path. Import torch lazily."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from audiobook.models import ChapterChunks
from audiobook.utils.audio import write_wav_with_trailing_silence

TTSCallable = Callable[..., tuple[np.ndarray, int]]


def render_chapter_chunks(
    cc: ChapterChunks,
    *,
    out_dir: Path,
    tts_callable: TTSCallable,
    voice_conditioning: Any,
) -> None:
    """Render every chunk in ``cc`` to ``out_dir/{chunk_id}.wav``.

    Skips chunks whose WAV already exists. Writes a JSON sidecar per chunk
    so failed chunks can be targeted for re-render.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for chunk in cc.chunks:
        wav_path = out_dir / f"{chunk.id}.wav"
        if wav_path.exists():
            continue
        samples, sr = tts_callable(chunk.text, voice_conditioning=voice_conditioning)
        write_wav_with_trailing_silence(wav_path, samples, sr, chunk.trailing_silence_ms)
        side = {
            "chunk_id": chunk.id,
            "text": chunk.text,
            "trailing_silence_ms": chunk.trailing_silence_ms,
            "sample_rate": sr,
        }
        (out_dir / f"{chunk.id}.json").write_text(json.dumps(side, indent=2))


def _load_chatterbox(device: str) -> tuple[Any, TTSCallable]:
    """Import torch + chatterbox lazily and return (model, callable).

    Lazy import: the Docker image does NOT have torch installed, so importing
    audiobook.render must succeed without it. This function only runs on the
    host where the [render] extra is installed.
    """
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


def render_work_dir(work_dir: Path, *, device: str, workers: int, voice_path: Path) -> None:
    """Top-level entry. Loads Chatterbox once and renders every chapter."""
    from concurrent.futures import ThreadPoolExecutor

    work_dir = Path(work_dir)
    chunks_dir = work_dir / "chapters" / "chunks"
    audio_root = work_dir / "audio" / "chunks"
    audio_root.mkdir(parents=True, exist_ok=True)

    _, tts_callable = _load_chatterbox(device)

    def _one(chunks_path: Path) -> None:
        cc = ChapterChunks.model_validate_json(chunks_path.read_text())
        out_dir = audio_root / chunks_path.stem
        render_chapter_chunks(
            cc, out_dir=out_dir, tts_callable=tts_callable, voice_conditioning=str(voice_path)
        )

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, sorted(chunks_dir.glob("*.json"))))
