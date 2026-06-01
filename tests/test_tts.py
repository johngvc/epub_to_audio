"""Tests for the TTS engine selector (audiobook.tts)."""
from __future__ import annotations

import pytest

from audiobook import tts


def test_kokoro_lang_for_voice() -> None:
    assert tts._kokoro_lang_for_voice("bm_george") == "b"
    assert tts._kokoro_lang_for_voice("af_heart") == "a"
    assert tts._kokoro_lang_for_voice("jf_alpha") == "j"
    assert tts._kokoro_lang_for_voice("") == "a"          # empty -> American default
    assert tts._kokoro_lang_for_voice("123") == "a"       # non-alpha -> default


def test_load_engine_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown TTS engine"):
        tts.load_engine("festival", "cpu")


def test_load_engine_dispatches_chatterbox(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(tts, "_load_chatterbox", lambda device: ("model", sentinel))
    assert tts.load_engine("chatterbox", "mps") is sentinel


def test_load_engine_dispatches_kokoro_with_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_kokoro(device, *, voice):
        captured["device"] = device
        captured["voice"] = voice
        return "kokoro-callable"

    monkeypatch.setattr(tts, "_load_kokoro", fake_kokoro)
    out = tts.load_engine("kokoro", "mps", voice="bm_george")
    assert out == "kokoro-callable"
    assert captured == {"device": "mps", "voice": "bm_george"}


def test_load_engine_kokoro_defaults_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    monkeypatch.setattr(tts, "_load_kokoro", lambda device, *, voice: captured.update(voice=voice))
    tts.load_engine("kokoro", "cpu")
    assert captured["voice"] == "af_heart"
