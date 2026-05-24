from __future__ import annotations

from pathlib import Path

import pytest

from audiobook.adapt_api import AdaptRunSummary, run_adapt_api
from audiobook.config import AppConfig, AdaptConfig, AdaptApiConfig


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
