"""Voice reference validation. No TTS dependency here — runs in Docker."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass(slots=True)
class VoiceValidationResult:
    path: Path
    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, object] = field(default_factory=dict)


_MIN_DURATION_S = 5.0
_RECOMMENDED_MIN_S = 10.0
_RECOMMENDED_MAX_S = 20.0
_TARGET_SR = 24000


def validate_voice_reference(path: Path) -> VoiceValidationResult:
    path = Path(path)
    res = VoiceValidationResult(path=path, ok=False)
    if not path.exists():
        res.problems.append(f"file does not exist: {path}")
        return res
    try:
        info = sf.info(str(path))
        data, sr = sf.read(str(path), always_2d=True)
    except Exception as exc:  # noqa: BLE001
        res.problems.append(f"failed to read audio: {exc}")
        return res

    duration = info.frames / info.samplerate
    res.info = {
        "duration_s": duration,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "subtype": info.subtype,
        "frames": info.frames,
    }

    if duration < _MIN_DURATION_S:
        res.problems.append(f"duration {duration:.1f}s below minimum {_MIN_DURATION_S}s")
    elif duration < _RECOMMENDED_MIN_S or duration > _RECOMMENDED_MAX_S:
        res.warnings.append(
            f"duration {duration:.1f}s outside recommended {_RECOMMENDED_MIN_S}-{_RECOMMENDED_MAX_S}s"
        )

    if info.samplerate != _TARGET_SR:
        res.warnings.append(
            f"sample rate {info.samplerate} != {_TARGET_SR}; will resample on use"
        )
    if info.channels > 1:
        res.warnings.append(f"{info.channels}-channel file; will downmix to mono on use")

    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak >= 0.99:
        res.warnings.append("audio is clipping (peak >= -0.1 dBFS); consider re-recording at lower gain")
    rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) if data.size else 0.0
    if rms < 1e-3:
        res.problems.append("recording is essentially silent")

    res.ok = not res.problems
    return res
