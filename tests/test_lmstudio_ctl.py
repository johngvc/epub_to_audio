"""Tests for LM Studio model load/unload control + the adapt `ttl` request field."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from audiobook import lmstudio_ctl as L
from audiobook.cli import app
from audiobook.config import AdaptApiConfig, AdaptConfig, AppConfig
from audiobook.models import ChapterRaw

runner = CliRunner()


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# --------------------------------------------------------------------------- #
# availability + loaded detection
# --------------------------------------------------------------------------- #
def test_lms_available() -> None:
    assert L.lms_available(which=lambda _n: "/usr/bin/lms") is True
    assert L.lms_available(which=lambda _n: None) is False


def test_loaded_entries_handles_bad_output() -> None:
    assert L.loaded_entries(lambda _a: _cp("not json")) == []
    assert L.loaded_entries(lambda _a: _cp("[]", returncode=1)) == []
    assert L.loaded_entries(lambda _a: _cp('[{"modelKey":"m"}]')) == [{"modelKey": "m"}]


def test_is_loaded_matches_anywhere_in_entry() -> None:
    def runner_(_a):
        return _cp(json.dumps([{"modelKey": "qwen3.6-35b-a3b-mtp", "identifier": "x"}]))

    assert L.is_loaded("qwen3.6-35b-a3b-mtp", runner_) is True
    assert L.is_loaded("some-other-model", runner_) is False


# --------------------------------------------------------------------------- #
# ensure_loaded
# --------------------------------------------------------------------------- #
def test_ensure_loaded_no_model() -> None:
    assert L.ensure_loaded("", which=lambda _n: "/x") == "no-model"


def test_ensure_loaded_unavailable() -> None:
    assert L.ensure_loaded("m", which=lambda _n: None) == "unavailable"


def test_ensure_loaded_skips_when_already_loaded() -> None:
    calls: list[list[str]] = []

    def runner_(argv):
        calls.append(list(argv))
        return _cp(json.dumps([{"modelKey": "m"}]))

    assert L.ensure_loaded("m", runner=runner_, which=lambda _n: "/x") == "already-loaded"
    assert all(c[:2] != ["lms", "load"] for c in calls)  # never tried to load


def test_ensure_loaded_builds_load_argv() -> None:
    calls: list[list[str]] = []

    def runner_(argv):
        calls.append(list(argv))
        return _cp("[]") if list(argv)[:2] == ["lms", "ps"] else _cp("")

    status = L.ensure_loaded(
        "m", context_length=32768, ttl=300, runner=runner_, which=lambda _n: "/x"
    )
    assert status == "loaded"
    load_call = next(c for c in calls if c[:2] == ["lms", "load"])
    assert load_call == ["lms", "load", "m", "-y", "-c", "32768", "--ttl", "300"]


# --------------------------------------------------------------------------- #
# unload_all
# --------------------------------------------------------------------------- #
def test_unload_all() -> None:
    calls: list[list[str]] = []

    def runner_(argv):
        calls.append(list(argv))
        return _cp("")

    assert L.unload_all(runner=runner_, which=lambda _n: "/x") == "unloaded"
    assert calls[-1] == ["lms", "unload", "--all"]


def test_unload_all_unavailable() -> None:
    assert L.unload_all(which=lambda _n: None) == "unavailable"


# --------------------------------------------------------------------------- #
# adapt request ttl
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        outer = self

        class _Completions:
            def create(_self, **kwargs: Any):
                outer.calls.append(kwargs)
                msg = type("M", (), {"content": json.dumps(
                    {"adapted_text": "ok " * 30, "pronunciation_hints": [], "notes": ""}
                ), "reasoning_content": None})()
                return type("R", (), {
                    "choices": [type("C", (), {"message": msg})()],
                    "usage": type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})(),
                })()

        class _Chat:
            def __init__(_self) -> None:
                _self.completions = _Completions()

        self.chat = _Chat()


def _write_raw(scratch: Path) -> None:
    raw_dir = scratch / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = ChapterRaw(
        index=0, title="intro", source_spine_id="s0", html="<p>" + ("w " * 30) + "</p>",
        word_count_estimate=30, has_code=False, has_math=False, has_tables=False,
    )
    (raw_dir / "00_intro.json").write_text(raw.model_dump_json())


def _cfg(ttl: int) -> AppConfig:
    return AppConfig(adapt=AdaptConfig(mode="api", api=AdaptApiConfig(model="m", ttl_seconds=ttl)))


def test_adapt_sends_ttl_in_extra_body(scratch: Path) -> None:
    from audiobook.adapt_api import run_adapt_api

    _write_raw(scratch)
    client = _FakeClient()
    run_adapt_api(scratch, cfg=_cfg(300), client_factory=lambda _api: client)
    assert client.calls[0]["extra_body"] == {"ttl": 300}


def test_adapt_omits_ttl_when_zero(scratch: Path) -> None:
    from audiobook.adapt_api import run_adapt_api

    _write_raw(scratch)
    client = _FakeClient()
    run_adapt_api(scratch, cfg=_cfg(0), client_factory=lambda _api: client)
    assert client.calls[0]["extra_body"] is None


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_cli_lms_load_passes_config(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_ensure(model: str, *, context_length=None, ttl=None, **_k: Any) -> str:
        captured.update(model=model, ctx=context_length, ttl=ttl)
        return "loaded"

    monkeypatch.setattr(L, "ensure_loaded", fake_ensure)
    cfg = scratch / "config.toml"
    cfg.write_text('[adapt.api]\nmodel = "m"\nttl_seconds = 120\nload_context_length = 8192\n')
    result = runner.invoke(app, ["lms-load", "--config", str(cfg)])
    assert result.exit_code == 0, result.stdout
    assert captured == {"model": "m", "ctx": 8192, "ttl": 120}


def test_cli_lms_unload_invokes_unload_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(L, "unload_all", lambda *_a, **_k: "unloaded")
    result = runner.invoke(app, ["lms-unload"])
    assert result.exit_code == 0, result.stdout
    assert "unloaded" in result.stdout
