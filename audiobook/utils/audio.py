"""Shared audio utilities (silence padding, format checks). No torch here."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), always_2d=True)
    return data.astype(np.float32), sr


def write_wav_with_trailing_silence(
    path: Path, samples: np.ndarray, sample_rate: int, trailing_silence_ms: int
) -> None:
    if trailing_silence_ms > 0:
        n_silence = int(sample_rate * trailing_silence_ms / 1000)
        silence = np.zeros((n_silence,) + samples.shape[1:], dtype=samples.dtype)
        samples = np.concatenate([samples, silence], axis=0)
    sf.write(str(path), samples, sample_rate, subtype="PCM_16")


def compress_silence(
    samples: np.ndarray,
    sample_rate: int,
    *,
    threshold: float = 0.01,
    max_gap_ms: int = 600,
    edge_ms: int = 50,
) -> np.ndarray:
    """Collapse abnormally long silences in TTS output.

    Chatterbox (like most autoregressive TTS) occasionally fails to emit an
    end-of-speech token and pads a chunk with many seconds of near-silence, or
    drops a multi-second gap mid-utterance. This trims leading/trailing silence
    to ``edge_ms`` and shortens any internal silent run longer than
    ``max_gap_ms`` down to ``max_gap_ms``. ``max_gap_ms == 0`` disables.

    Silence is "samples whose absolute amplitude is below ``threshold``".
    Returns the (possibly shorter) samples; the chunk's intended pause is added
    separately by :func:`write_wav_with_trailing_silence`.
    """
    if max_gap_ms <= 0 or samples.size == 0:
        return samples
    mono = np.abs(samples)
    if mono.ndim > 1:
        mono = mono.max(axis=1)
    voiced = np.where(mono > threshold)[0]
    if voiced.size == 0:
        # All silence — keep a short stub so the chunk isn't zero-length.
        stub = min(len(samples), int(sample_rate * edge_ms / 1000))
        return samples[:stub]

    edge = int(sample_rate * edge_ms / 1000)
    max_gap = int(sample_rate * max_gap_ms / 1000)
    keep = np.ones(len(samples), dtype=bool)

    # Trim leading / trailing silence beyond `edge`.
    first, last = int(voiced[0]), int(voiced[-1])
    if first > edge:
        keep[: first - edge] = False
    if len(samples) - 1 - last > edge:
        keep[last + 1 + edge :] = False

    # Shorten internal silent runs longer than `max_gap`.
    big = np.where(np.diff(voiced) - 1 > max_gap)[0]
    for i in big:
        a, b = int(voiced[i]), int(voiced[i + 1])
        keep[a + 1 + max_gap : b] = False

    trimmed: np.ndarray = samples[keep]
    return trimmed


def db_level(samples: np.ndarray) -> float:
    """Return peak dBFS (negative or zero)."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 0:
        return -float("inf")
    return 20 * float(np.log10(peak))
