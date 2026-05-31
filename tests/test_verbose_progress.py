"""Tests for the --verbose / -v progress output across stages."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from audiobook.adapt_api import run_adapt_api
from audiobook.chunk import chunk_work_dir
from audiobook.cli import app
from audiobook.config import AdaptApiConfig, AdaptConfig, AppConfig
from audiobook.models import ChapterAdapted, ChapterChunks, ChapterRaw, Chunk
from audiobook.parse_pdf import parse_pdf
from audiobook.render import render_chapter_chunks
from audiobook.utils.progress import pct_line

runner = CliRunner()


# --------------------------------------------------------------------------- #
# pct_line
# --------------------------------------------------------------------------- #
def test_pct_line_basic() -> None:
    assert pct_line("render", 1, 4) == "[render] 1/4 (25%)"


def test_pct_line_with_detail() -> None:
    assert pct_line("render", 1, 4, "0001.wav") == "[render] 1/4 (25%) 0001.wav"


def test_pct_line_rounds() -> None:
    assert pct_line("adapt", 1, 3) == "[adapt] 1/3 (33%)"
    assert pct_line("adapt", 2, 3) == "[adapt] 2/3 (67%)"


def test_pct_line_full() -> None:
    assert pct_line("chunk", 2, 2) == "[chunk] 2/2 (100%)"


def test_pct_line_guards_zero_total() -> None:
    assert pct_line("parse", 0, 0) == "[parse] 0/0 (0%)"


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #
def test_parse_pdf_verbose_emits_pct_lines(repo_root: Path, scratch: Path) -> None:
    lines: list[str] = []
    parse_pdf(
        repo_root / "tests" / "fixtures" / "tiny.pdf",
        scratch,
        progress=lines.append,
        verbose=True,
    )
    parse_lines = [ln for ln in lines if ln.startswith("[parse]")]
    assert len(parse_lines) == 2
    assert parse_lines[-1] == "[parse] 2/2 (100%) Chapter Two"


def test_parse_pdf_quiet_emits_no_pct_lines(repo_root: Path, scratch: Path) -> None:
    lines: list[str] = []
    parse_pdf(
        repo_root / "tests" / "fixtures" / "tiny.pdf",
        scratch,
        progress=lines.append,
        verbose=False,
    )
    assert [ln for ln in lines if ln.startswith("[parse]")] == []


# --------------------------------------------------------------------------- #
# chunk
# --------------------------------------------------------------------------- #
def _write_chapter_pair(work: Path, index: int, title: str, body: str) -> None:
    raw_dir = work / "chapters" / "raw"
    adapted_dir = work / "chapters" / "adapted"
    raw_dir.mkdir(parents=True, exist_ok=True)
    adapted_dir.mkdir(parents=True, exist_ok=True)
    name = f"{index:02d}_{title}.json"
    raw = ChapterRaw(
        index=index, title=title, source_spine_id=f"s{index}", html=f"<p>{body}</p>",
        word_count_estimate=len(body.split()), has_code=False, has_math=False, has_tables=False,
    )
    (raw_dir / name).write_text(raw.model_dump_json())
    adapted = ChapterAdapted(adapted_text=body, pronunciation_hints=[], notes="")
    (adapted_dir / name).write_text(adapted.model_dump_json())


def test_chunk_verbose_emits_pct_lines(scratch: Path) -> None:
    body = "This is a sentence. " * 20
    _write_chapter_pair(scratch, 0, "intro", body)
    _write_chapter_pair(scratch, 1, "body", body)
    lines: list[str] = []
    chunk_work_dir(
        scratch, max_chars=200, paragraph_silence_ms=400, section_silence_ms=1200,
        progress=lines.append, verbose=True,
    )
    chunk_lines = [ln for ln in lines if ln.startswith("[chunk]")]
    assert len(chunk_lines) == 2
    assert chunk_lines[0].startswith("[chunk] 1/2 (50%)")
    assert chunk_lines[1].startswith("[chunk] 2/2 (100%)")


def test_chunk_quiet_emits_no_pct_lines(scratch: Path) -> None:
    body = "This is a sentence. " * 20
    _write_chapter_pair(scratch, 0, "intro", body)
    lines: list[str] = []
    chunk_work_dir(
        scratch, max_chars=200, paragraph_silence_ms=400, section_silence_ms=1200,
        progress=lines.append, verbose=False,
    )
    assert lines == []


# --------------------------------------------------------------------------- #
# adapt
# --------------------------------------------------------------------------- #
def _make_cfg(**api_overrides: Any) -> AppConfig:
    api = AdaptApiConfig(**{"model": "test-model", **api_overrides})
    return AppConfig(adapt=AdaptConfig(mode="api", api=api))


class _FakeResponse:
    def __init__(self, content: str) -> None:
        message = type("M", (), {"content": content, "reasoning_content": None})()
        self.choices = [type("C", (), {"message": message})()]
        self.usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 50})()


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        outer = self

        class _Completions:
            def create(_self, **kwargs: Any) -> _FakeResponse:
                return outer._responses.pop(0)

        class _Chat:
            def __init__(_self) -> None:
                _self.completions = _Completions()

        self.chat = _Chat()


def _write_raw(work: Path, index: int, title: str, body: str) -> None:
    raw_dir = work / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = ChapterRaw(
        index=index, title=title, source_spine_id=f"s{index}", html=f"<p>{body}</p>",
        word_count_estimate=len(body.split()), has_code=False, has_math=False, has_tables=False,
    )
    (raw_dir / f"{index:02d}_{title}.json").write_text(raw.model_dump_json())


def test_adapt_verbose_emits_pct_line_with_tokens(scratch: Path) -> None:
    body = " ".join(["word"] * 50)
    _write_raw(scratch, 0, "intro", body)
    valid = json.dumps({"adapted_text": body, "pronunciation_hints": [], "notes": ""})
    client = _FakeClient([_FakeResponse(valid)])
    lines: list[str] = []
    run_adapt_api(
        scratch, cfg=_make_cfg(), client_factory=lambda _api: client,
        progress=lines.append, verbose=True,
    )
    adapt_lines = [ln for ln in lines if ln.startswith("[adapt]")]
    assert adapt_lines == ["[adapt] 1/1 (100%) 00_intro ok in=100 out=50 tok"]


def test_adapt_quiet_emits_no_pct_lines(scratch: Path) -> None:
    body = " ".join(["word"] * 50)
    _write_raw(scratch, 0, "intro", body)
    valid = json.dumps({"adapted_text": body, "pronunciation_hints": [], "notes": ""})
    client = _FakeClient([_FakeResponse(valid)])
    lines: list[str] = []
    run_adapt_api(
        scratch, cfg=_make_cfg(), client_factory=lambda _api: client,
        progress=lines.append, verbose=False,
    )
    assert [ln for ln in lines if ln.startswith("[adapt]")] == []


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def _fake_tts(text: str, *, voice_conditioning: Any, **_: Any) -> tuple[np.ndarray, int]:
    sr = 24000
    return (0.1 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype(np.float32), sr


def test_render_on_chunk_fires_per_chunk(scratch: Path) -> None:
    cc = ChapterChunks(
        index=0, title="Intro",
        chunks=[
            Chunk(id="0000", text="hello", trailing_silence_ms=0),
            Chunk(id="0001", text="world", trailing_silence_ms=0),
        ],
    )
    seen: list[str] = []
    render_chapter_chunks(
        cc, out_dir=scratch / "ch", tts_callable=_fake_tts,
        voice_conditioning=None, on_chunk=seen.append,
    )
    assert seen == ["0000", "0001"]


def test_render_on_chunk_fires_on_skip(scratch: Path) -> None:
    import soundfile as sf

    cc = ChapterChunks(
        index=0, title="X", chunks=[Chunk(id="0000", text="hi", trailing_silence_ms=0)],
    )
    out_dir = scratch / "ch"
    out_dir.mkdir(parents=True)
    sf.write(str(out_dir / "0000.wav"), np.zeros(2400, dtype=np.float32), 24000, subtype="PCM_16")
    seen: list[str] = []
    render_chapter_chunks(
        cc, out_dir=out_dir, tts_callable=_fake_tts,
        voice_conditioning=None, on_chunk=seen.append,
    )
    assert seen == ["0000"]  # fired even though the chunk was skipped


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #
def test_cli_chunk_passes_verbose(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (scratch / "chapters" / "adapted").mkdir(parents=True)
    (scratch / "config.toml").write_text("")
    captured: dict[str, Any] = {}

    import audiobook.cli as cli_mod

    def fake_chunk(work_dir: Path, **kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli_mod, "_chunk_dir", fake_chunk)
    result = runner.invoke(
        app, ["chunk", str(scratch), "--config", str(scratch / "config.toml"), "-v"]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["verbose"] is True
    assert captured["progress"] is not None


def test_cli_chunk_defaults_to_quiet(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (scratch / "chapters" / "adapted").mkdir(parents=True)
    (scratch / "config.toml").write_text("")
    captured: dict[str, Any] = {}

    import audiobook.cli as cli_mod

    def fake_chunk(work_dir: Path, **kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli_mod, "_chunk_dir", fake_chunk)
    result = runner.invoke(
        app, ["chunk", str(scratch), "--config", str(scratch / "config.toml")]
    )
    assert result.exit_code == 0, result.stdout
    assert captured["verbose"] is False
    assert captured["progress"] is None
