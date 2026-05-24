"""Stage 2 — `mode = "api"` driver. Calls an OpenAI-compatible endpoint
(LM Studio by default) chapter by chapter. Imports the openai SDK lazily."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from audiobook.adapt import validate_adapted_file
from audiobook.config import AppConfig, ResolvedAdaptApi, resolve_adapt_api


@dataclass(slots=True)
class AdaptRunSummary:
    succeeded: list[str] = field(default_factory=list)
    retried: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    wall_seconds: float = 0.0
    included_book_context: bool = False


ClientFactory = Callable[[ResolvedAdaptApi], Any]


def _default_client_factory(api: ResolvedAdaptApi) -> Any:
    from openai import OpenAI
    return OpenAI(base_url=api.base_url, api_key=api.api_key)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _should_include_book_context(book_text: str, ctx_window: int) -> bool:
    return _estimate_tokens(book_text) <= int(ctx_window * 0.6)


def run_adapt_api(
    work_dir: Path,
    *,
    cfg: AppConfig,
    progress: Callable[[str], None] | None = None,
    client_factory: ClientFactory = _default_client_factory,
) -> AdaptRunSummary:
    """Drive Stage 2 (adapt) against an OpenAI-compatible API."""
    work_dir = Path(work_dir)
    raw_dir = work_dir / "chapters" / "raw"
    adapted_dir = work_dir / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True, exist_ok=True)

    summary = AdaptRunSummary()
    started = time.monotonic()

    raw_paths = sorted(raw_dir.glob("*.json"))
    if not raw_paths:
        summary.wall_seconds = time.monotonic() - started
        return summary

    # Whole-book context decision (one-time).
    api = resolve_adapt_api(cfg.adapt.api)
    book_text_path = work_dir / "book_full_text.md"
    book_text = book_text_path.read_text() if book_text_path.exists() else ""
    summary.included_book_context = bool(book_text) and _should_include_book_context(
        book_text, api.context_window
    )
    if progress:
        if book_text and not summary.included_book_context:
            progress(
                f"book_full_text.md ~{_estimate_tokens(book_text)} tokens > "
                f"60% of context_window={api.context_window}; skipping whole-book context"
            )

    # Per-chapter loop comes in Task 4. For now: no-op.
    _ = client_factory(api)  # validate factory is callable; not used yet
    summary.wall_seconds = time.monotonic() - started
    return summary
