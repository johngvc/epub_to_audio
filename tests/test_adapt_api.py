from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from audiobook.adapt_api import AdaptRunSummary, run_adapt_api
from audiobook.cli import app
from audiobook.config import AppConfig, AdaptConfig, AdaptApiConfig
from audiobook.models import ChapterRaw

runner = CliRunner()


def _make_cfg(**api_overrides) -> AppConfig:
    api = AdaptApiConfig(**{"model": "test-model", **api_overrides})
    return AppConfig(adapt=AdaptConfig(mode="api", api=api))


def test_empty_work_dir_returns_empty_summary(tmp_path: Path) -> None:
    (tmp_path / "chapters" / "raw").mkdir(parents=True)
    summary = run_adapt_api(tmp_path, cfg=_make_cfg(), client_factory=lambda cfg: None)
    assert isinstance(summary, AdaptRunSummary)
    assert summary.succeeded == []
    assert summary.retried == []
    assert summary.failed == []
    assert summary.total_input_tokens == 0
    assert summary.total_output_tokens == 0
    assert summary.included_book_context is False


def _write_raw(scratch: Path, index: int, title: str, body: str) -> Path:
    raw_dir = scratch / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = ChapterRaw(
        index=index,
        title=title,
        source_spine_id=f"ch{index:02d}.xhtml",
        html=f"<p>{body}</p>",
        word_count_estimate=len(body.split()),
        has_code=False,
        has_math=False,
        has_tables=False,
    )
    p = raw_dir / f"{index:02d}_{title}.json"
    p.write_text(raw.model_dump_json())
    return p


class _FakeResponse:
    def __init__(self, content: str, input_tokens: int = 100, output_tokens: int = 50):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]
        self.usage = type("U", (), {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
        })()


class _FakeClient:
    """Records calls; returns scripted responses in order."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

        outer = self
        class _Completions:
            def create(_self, **kwargs):
                outer.calls.append(kwargs)
                if not outer._responses:
                    raise RuntimeError("FakeClient out of scripted responses")
                return outer._responses.pop(0)

        class _Chat:
            def __init__(_self):
                _self.completions = _Completions()

        self.chat = _Chat()


def _valid_adapted_json(body: str) -> str:
    return json.dumps({
        "adapted_text": body,
        "pronunciation_hints": [],
        "notes": "",
    })


def test_happy_path_writes_adapted_file(scratch: Path) -> None:
    body = " ".join(["word"] * 50)
    _write_raw(scratch, 0, "intro", body)
    client = _FakeClient([_FakeResponse(_valid_adapted_json(body))])
    summary = run_adapt_api(
        scratch, cfg=_make_cfg(), client_factory=lambda _api: client
    )
    assert summary.succeeded == ["00_intro"]
    assert summary.retried == []
    assert summary.failed == []
    assert (scratch / "chapters" / "adapted" / "00_intro.json").exists()
    assert summary.total_input_tokens == 100
    assert summary.total_output_tokens == 50
    # The user message should include the chapter JSON
    assert any("intro" in str(c.get("messages")) for c in client.calls)


def test_skips_already_valid_adapted_file(scratch: Path) -> None:
    body = " ".join(["word"] * 50)
    _write_raw(scratch, 0, "intro", body)
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    (adapted_dir / "00_intro.json").write_text(_valid_adapted_json(body))
    client = _FakeClient([])  # would raise if called
    summary = run_adapt_api(
        scratch, cfg=_make_cfg(), client_factory=lambda _api: client
    )
    assert summary.succeeded == ["00_intro"]
    assert client.calls == []


def test_retry_on_schema_error_then_succeeds(scratch: Path) -> None:
    body = " ".join(["word"] * 50)
    _write_raw(scratch, 0, "intro", body)
    bad = json.dumps({"adapted_text": "", "pronunciation_hints": [], "notes": ""})  # min_length=1 violation
    client = _FakeClient([
        _FakeResponse(bad),
        _FakeResponse(_valid_adapted_json(body)),
    ])
    summary = run_adapt_api(
        scratch, cfg=_make_cfg(), client_factory=lambda _api: client
    )
    assert summary.succeeded == ["00_intro"]
    assert summary.retried == ["00_intro"]
    assert len(client.calls) == 2
    retry_msgs = client.calls[1]["messages"]
    retry_user = "\n".join(m["content"] for m in retry_msgs if m["role"] == "user")
    assert "Previous attempt failed validation" in retry_user
    assert "schema_error" in retry_user


def test_hard_failure_after_two_retries(scratch: Path) -> None:
    body = " ".join(["word"] * 50)
    _write_raw(scratch, 0, "intro", body)
    bad = "not even json"
    client = _FakeClient([_FakeResponse(bad), _FakeResponse(bad), _FakeResponse(bad)])
    summary = run_adapt_api(
        scratch, cfg=_make_cfg(), client_factory=lambda _api: client
    )
    assert summary.succeeded == []
    assert summary.failed and summary.failed[0][0] == "00_intro"
    assert len(client.calls) == 3  # 1 initial + 2 retries
    # Bad adapted file is removed so a future run can re-attempt
    assert not (scratch / "chapters" / "adapted" / "00_intro.json").exists()


def test_book_context_included_when_short(scratch: Path) -> None:
    _write_raw(scratch, 0, "intro", " ".join(["w"] * 50))
    (scratch / "book_full_text.md").write_text("short book")
    client = _FakeClient([_FakeResponse(_valid_adapted_json("ok " * 50))])
    summary = run_adapt_api(
        scratch, cfg=_make_cfg(), client_factory=lambda _api: client
    )
    assert summary.included_book_context is True
    sent = "\n".join(m["content"] for m in client.calls[0]["messages"])
    assert "short book" in sent


def test_book_context_skipped_when_too_large(scratch: Path) -> None:
    _write_raw(scratch, 0, "intro", " ".join(["w"] * 50))
    # 80k chars ≈ 20k tokens; with context_window=4096, 60% = 2458 → skip
    book_content = "x" * 80_000
    (scratch / "book_full_text.md").write_text(book_content)
    client = _FakeClient([_FakeResponse(_valid_adapted_json("ok " * 50))])
    summary = run_adapt_api(
        scratch,
        cfg=_make_cfg(context_window=4096),
        client_factory=lambda _api: client,
    )
    assert summary.included_book_context is False
    # Verify the large book content was not injected into the user message
    user_content = "\n".join(
        m["content"] for m in client.calls[0]["messages"] if m["role"] == "user"
    )
    assert book_content not in user_content


def test_cli_adapt_rejects_agent_mode(scratch: Path) -> None:
    (scratch / "chapters" / "raw").mkdir(parents=True)
    cfg_path = scratch / "config.toml"
    cfg_path.write_text("""
[adapt]
mode = "agent"
""")
    result = runner.invoke(app, ["adapt", str(scratch), "--config", str(cfg_path)])
    assert result.exit_code == 2
    assert "external orchestrator" in result.stdout or "external orchestrator" in result.stderr


def test_cli_adapt_runs_in_api_mode(scratch: Path, monkeypatch) -> None:
    body = " ".join(["word"] * 50)
    _write_raw(scratch, 0, "intro", body)
    cfg_path = scratch / "config.toml"
    cfg_path.write_text("""
[adapt]
mode = "api"

[adapt.api]
base_url = "http://localhost:1234/v1"
model = "test-model"
""")
    # Patch the default factory used inside cli → adapt_api.
    # Because run_adapt_api uses late-binding (client_factory=None, then resolved
    # inside the function), monkeypatching the module attribute works at call time.
    fake_client = _FakeClient([_FakeResponse(_valid_adapted_json(body))])
    import audiobook.adapt_api as ax
    monkeypatch.setattr(ax, "_default_client_factory", lambda _api: fake_client)

    result = runner.invoke(app, ["adapt", str(scratch), "--config", str(cfg_path)])
    assert result.exit_code == 0, result.stdout
    assert "succeeded=1" in result.stdout or "succeeded: 1" in result.stdout
    assert (scratch / "chapters" / "adapted" / "00_intro.json").exists()
