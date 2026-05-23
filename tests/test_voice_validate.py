from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from audiobook.voice import VoiceValidationResult, validate_voice_reference


def _write_wav(p: Path, *, duration_s: float, sr: int, channels: int, amplitude: float) -> None:
    n = int(duration_s * sr)
    if channels == 1:
        data = (amplitude * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype(np.float32)
    else:
        mono = (amplitude * np.sin(2 * np.pi * 220 * np.arange(n) / sr)).astype(np.float32)
        data = np.column_stack([mono] * channels)
    sf.write(p, data, sr, subtype="PCM_16")


def test_clean_reference_passes(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=12, sr=24000, channels=1, amplitude=0.4)
    r = validate_voice_reference(p)
    assert isinstance(r, VoiceValidationResult)
    assert r.ok, r.problems


def test_too_short_flagged(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=3, sr=24000, channels=1, amplitude=0.4)
    r = validate_voice_reference(p)
    assert not r.ok
    assert any("duration" in pr for pr in r.problems)


def test_wrong_sample_rate_warns_not_fails(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=12, sr=48000, channels=2, amplitude=0.4)
    r = validate_voice_reference(p)
    # Resampling/downmix is automatic at use-time → warning, not failure
    assert r.ok or any("resamp" in pr.lower() or "downmix" in pr.lower() for pr in r.problems + r.warnings)


def test_clipping_detected(scratch: Path) -> None:
    p = scratch / "ref.wav"
    _write_wav(p, duration_s=12, sr=24000, channels=1, amplitude=0.999)
    r = validate_voice_reference(p)
    assert any("clip" in pr.lower() for pr in r.problems + r.warnings)
