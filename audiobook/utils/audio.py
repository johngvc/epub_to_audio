"""Shared audio utilities (silence padding, format checks). No torch here."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


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


def db_level(samples: np.ndarray) -> float:
    """Return peak dBFS (negative or zero)."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 0:
        return -float("inf")
    return 20 * float(np.log10(peak))
