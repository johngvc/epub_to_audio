"""Stage 4 TTS engine adapter — host-only.

Exposes a single ``TTSCallable`` seam so the renderer is engine-agnostic, and a
``load_engine`` selector that backs it with either Chatterbox (voice cloning
from a reference WAV) or Kokoro (fixed built-in voices). Heavy deps (torch,
chatterbox, kokoro) are imported lazily so the package still imports where they
are absent (e.g. the Docker test image).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

# (text, *, voice_conditioning, **kwargs) -> (float32 mono samples, sample_rate)
TTSCallable = Callable[..., tuple[np.ndarray, int]]


def _load_chatterbox(device: str) -> tuple[Any, TTSCallable]:
    """Import torch + chatterbox lazily and return (model, callable).

    Lazy import: the Docker image does NOT have torch installed, so importing
    this module must succeed without it. This runs only on the host where the
    [render] extra is installed.
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
            "Stage 4 (chatterbox) requires the [render] extra. "
            "Run scripts/host-install.sh on the host."
        ) from exc

    model = ChatterboxTTS.from_pretrained(device=device)

    def _call(text: str, *, voice_conditioning: Any, **kwargs: Any) -> tuple[np.ndarray, int]:
        wav = model.generate(text=text, audio_prompt_path=voice_conditioning, **kwargs)
        # Chatterbox returns a torch.Tensor at 24kHz mono.
        return wav.squeeze().cpu().numpy().astype(np.float32), 24000

    return model, _call


def _kokoro_lang_for_voice(voice: str) -> str:
    """Kokoro voice names are language-prefixed (``a``=American English,
    ``b``=British English, ``j``=Japanese, …). Return the lang_code, defaulting
    to American English."""
    return voice[0] if voice and voice[0].isalpha() else "a"


def _load_kokoro(device: str, *, voice: str) -> TTSCallable:
    """Import kokoro lazily and return a TTSCallable backed by ``KPipeline``.

    ``voice`` fixes the pipeline's language; the per-call ``voice_conditioning``
    selects the actual built-in voice (normally the same value).
    """
    import os

    # Kokoro's iSTFT uses aten::angle, which is unimplemented on Apple's MPS
    # backend, so run on CPU there (the 82M model is fast on CPU anyway). Keep
    # cuda/cpu as requested. The fallback env is a belt-and-suspenders for any
    # other MPS op gaps.
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    kokoro_device = "cpu" if device == "mps" else device

    try:
        from kokoro import KPipeline  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Kokoro engine requires the [kokoro] extra "
            "(uv pip install -e '.[kokoro]'; may also need `brew install espeak-ng`)."
        ) from exc

    lang = _kokoro_lang_for_voice(voice)
    try:
        pipeline = KPipeline(lang_code=lang, device=kokoro_device)
    except TypeError:
        # Older kokoro KPipeline has no `device` kwarg.
        pipeline = KPipeline(lang_code=lang)

    def _call(
        text: str, *, voice_conditioning: Any = None, speed: float = 1.0, **_: Any
    ) -> tuple[np.ndarray, int]:
        selected = voice_conditioning or voice
        parts: list[np.ndarray] = []
        for result in pipeline(text, voice=selected, speed=speed):
            audio = result[2] if isinstance(result, tuple) else getattr(result, "audio", result)
            arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            parts.append(arr.astype(np.float32))
        samples = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        return samples, 24000

    return _call


def load_engine(engine: str, device: str, *, voice: str | None = None) -> TTSCallable:
    """Return the ``TTSCallable`` for the named engine.

    ``voice`` is only used by Kokoro (to pick the pipeline language). Raises
    ``ValueError`` for an unknown engine.
    """
    if engine == "chatterbox":
        _, call = _load_chatterbox(device)
        return call
    if engine == "kokoro":
        return _load_kokoro(device, voice=voice or "af_heart")
    raise ValueError(f"unknown TTS engine: {engine!r} (expected 'chatterbox' or 'kokoro')")
