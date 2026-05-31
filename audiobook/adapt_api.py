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
from audiobook.models import ChapterAdapted
from audiobook.utils.progress import pct_line


def _chapter_adapted_response_format() -> dict[str, Any]:
    """LM Studio + OpenAI Structured Outputs: constrain the model to
    produce JSON that matches ChapterAdapted's schema. Eliminates the
    most common retry cause (schema_error)."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ChapterAdapted",
            "schema": ChapterAdapted.model_json_schema(),
        },
    }


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


_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "adapt_system.md"


def _load_system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text()


def _build_messages(
    *,
    system_prompt: str,
    raw_chapter_json: str,
    book_context: str | None,
    last_error: tuple[str, str] | None,
) -> list[dict[str, str]]:
    user_parts = [f"Chapter to adapt (JSON):\n{raw_chapter_json}"]
    if book_context:
        user_parts.append(f"\nWhole-book context (markdown):\n{book_context}")
    if last_error is not None:
        kind, detail = last_error
        user_parts.append(
            f"\nPrevious attempt failed validation with:\n"
            f"  error_kind: {kind}\n"
            f"  detail: {detail}\n"
            f"Please correct the issue and produce a valid response this time."
        )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def _call_once(
    client: Any,
    *,
    api: ResolvedAdaptApi,
    messages: list[dict[str, str]],
) -> tuple[str, int, int]:
    """Return (content, input_tokens, output_tokens)."""
    response = client.chat.completions.create(
        model=api.model,
        messages=messages,
        response_format=_chapter_adapted_response_format(),
        temperature=api.temperature,
        max_tokens=api.max_output_tokens,
        timeout=api.request_timeout_s,
    )
    message = response.choices[0].message
    # LM Studio routes a reasoning model's output to `reasoning_content` even when
    # the actual answer lives there; fall back so we don't lose the response.
    content = message.content or getattr(message, "reasoning_content", None) or ""
    in_tok = getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
    out_tok = getattr(response.usage, "completion_tokens", 0) if response.usage else 0
    return content, in_tok, out_tok


def run_adapt_api(
    work_dir: Path,
    *,
    cfg: AppConfig,
    progress: Callable[[str], None] | None = None,
    client_factory: ClientFactory | None = None,
    verbose: bool = False,
) -> AdaptRunSummary:
    """Drive Stage 2 (adapt) against an OpenAI-compatible API.

    ``client_factory`` defaults to ``_default_client_factory`` when *None* so
    that callers can monkeypatch ``audiobook.adapt_api._default_client_factory``
    at test time without fighting Python's early-binding of default arguments.
    """
    if client_factory is None:
        client_factory = _default_client_factory

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

    api = resolve_adapt_api(cfg.adapt.api)
    book_text_path = work_dir / "book_full_text.md"
    book_text = book_text_path.read_text() if book_text_path.exists() else ""
    summary.included_book_context = bool(book_text) and _should_include_book_context(
        book_text, api.context_window
    )
    if progress and book_text and not summary.included_book_context:
        progress(
            f"book_full_text.md ~{_estimate_tokens(book_text)} tokens > "
            f"60% of context_window={api.context_window}; skipping whole-book context"
        )

    system_prompt = _load_system_prompt()
    client = client_factory(api)
    book_context = book_text if summary.included_book_context else None

    total = len(raw_paths)
    for done, raw_path in enumerate(raw_paths, 1):
        stem = raw_path.stem
        adapted_path = adapted_dir / raw_path.name

        # Idempotency: skip if already valid.
        if adapted_path.exists():
            outcome = validate_adapted_file(raw_path, adapted_path)
            if outcome.ok:
                summary.succeeded.append(stem)
                if progress:
                    progress(f"[{stem}] already valid; skipping")
                if verbose and progress:
                    progress(pct_line("adapt", done, total, f"{stem} skipped (already valid)"))
                continue

        raw_json = raw_path.read_text()
        last_error: tuple[str, str] | None = None
        had_retry = False
        chapter_in = 0
        chapter_out = 0

        for attempt in range(3):  # 1 initial + up to 2 retries
            messages = _build_messages(
                system_prompt=system_prompt,
                raw_chapter_json=raw_json,
                book_context=book_context,
                last_error=last_error,
            )
            try:
                content, in_tok, out_tok = _call_once(client, api=api, messages=messages)
            except Exception as exc:
                last_error = ("transport_error", str(exc))
                had_retry = True
                if progress:
                    progress(f"[{stem}] attempt {attempt + 1} transport error: {exc}")
                continue

            summary.total_input_tokens += in_tok
            summary.total_output_tokens += out_tok
            chapter_in += in_tok
            chapter_out += out_tok
            adapted_path.write_text(content)

            outcome = validate_adapted_file(raw_path, adapted_path)
            if outcome.ok:
                summary.succeeded.append(stem)
                if had_retry:
                    summary.retried.append(stem)
                if progress:
                    progress(f"[{stem}] ok on attempt {attempt + 1}")
                if verbose and progress:
                    progress(pct_line(
                        "adapt", done, total,
                        f"{stem} ok in={chapter_in} out={chapter_out} tok",
                    ))
                break

            last_error = (outcome.error_kind or "unknown", outcome.detail)
            had_retry = True
            if progress:
                progress(f"[{stem}] attempt {attempt + 1} failed: {outcome.error_kind} — {outcome.detail}")
        else:
            # All attempts exhausted.
            summary.failed.append((stem, f"{last_error[0]}: {last_error[1]}" if last_error else "unknown"))
            # Remove the last bad write so the next run can retry cleanly.
            adapted_path.unlink(missing_ok=True)
            if verbose and progress:
                progress(pct_line(
                    "adapt", done, total,
                    f"{stem} fail in={chapter_in} out={chapter_out} tok",
                ))

    summary.wall_seconds = time.monotonic() - started
    return summary
