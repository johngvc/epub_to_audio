"""TOML config loader with strict Pydantic validation."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BookConfig(_Strict):
    title: str = ""
    author: str = ""
    narrator: str = ""
    skip_sections: list[str] = Field(
        default_factory=lambda: ["copyright", "dedication", "index", "bibliography"]
    )


class ParseConfig(_Strict):
    parser: Literal["auto", "pymupdf", "marker"] = "auto"
    footnote_policy: Literal["inline", "endnote", "skip"] = "skip"
    chapter_level: int | None = Field(default=None, ge=1, le=6)


class AdaptApiConfig(_Strict):
    base_url: str = "http://localhost:1234/v1"
    model: str = ""
    api_key: str = "lm-studio"
    context_window: int = Field(default=16384, ge=512)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=8192, ge=256)
    request_timeout_s: int = Field(default=600, ge=10)
    # Idle seconds before LM Studio auto-unloads a JIT-loaded model. Sent as the
    # `ttl` field on each request. 0 disables (model stays loaded).
    ttl_seconds: int = Field(default=300, ge=0)
    # When true, `bin/audiobook run` loads the model before adapt and unloads it
    # after, to keep the LLM out of RAM during render (host-only, needs `lms`).
    manage_model: bool = True
    # Context length to load the model with (via `lms load -c`). None = LM Studio
    # default. Lower values dramatically cut KV-cache RAM.
    load_context_length: int | None = Field(default=None, ge=512)


class AdaptConfig(_Strict):
    mode: Literal["agent", "chat", "api"] = "agent"
    model_label: str = "claude-sonnet-4-6"
    concurrency: int = Field(default=8, ge=1, le=32)
    split_long_chapters_at_words: int = Field(default=6000, ge=500)
    max_tokens_per_call: int = 8192
    budget_usd: float = 15.0
    prompt_cache: bool = True
    api: AdaptApiConfig = Field(default_factory=AdaptApiConfig)


class ChunkConfig(_Strict):
    max_chars: int = Field(default=400, ge=50, le=600)
    paragraph_silence_ms: int = Field(default=400, ge=0, le=5000)
    section_silence_ms: int = Field(default=1200, ge=0, le=10_000)


class RenderConfig(_Strict):
    voice: str = ""             # saved voice name; empty = voices/default.wav fallback chain
    device: str = "mps"
    workers: int = Field(default=2, ge=1, le=8)
    exaggeration: float = 0.4
    cfg_weight: float = 0.5
    temperature: float = 0.7
    multilingual: bool = False
    # Collapse Chatterbox silence hallucinations: trim chunk edges and cap any
    # internal silent run to this many ms. 0 disables.
    max_silence_ms: int = Field(default=600, ge=0)


class AssembleConfig(_Strict):
    audio_bitrate_kbps: int = 64
    sample_rate_hz: int = 24000


class AppConfig(_Strict):
    book: BookConfig = Field(default_factory=BookConfig)
    parse: ParseConfig = Field(default_factory=ParseConfig)
    adapt: AdaptConfig = Field(default_factory=AdaptConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    assemble: AssembleConfig = Field(default_factory=AssembleConfig)


def load_config(path: Path) -> AppConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    try:
        return AppConfig.model_validate(data)
    except Exception as exc:
        raise ValueError(f"invalid config {path}: {exc}") from exc


@dataclass(slots=True)
class ResolvedAdaptApi:
    base_url: str
    model: str
    api_key: str
    context_window: int
    temperature: float
    max_output_tokens: int
    request_timeout_s: int
    ttl_seconds: int


_ENV_MAP = {
    "base_url": "OPENAI_BASE_URL",
    "model": "OPENAI_MODEL",
    "api_key": "OPENAI_API_KEY",
}


def resolve_adapt_api(cfg: AdaptApiConfig) -> ResolvedAdaptApi:
    """Apply env-var overrides. Empty env values do NOT override config."""
    overrides = {}
    for field_name, env_name in _ENV_MAP.items():
        env_val = os.environ.get(env_name, "")
        if env_val:  # empty string = no override
            overrides[field_name] = env_val
    return ResolvedAdaptApi(
        base_url=overrides.get("base_url", cfg.base_url),
        model=overrides.get("model", cfg.model),
        api_key=overrides.get("api_key", cfg.api_key),
        context_window=cfg.context_window,
        temperature=cfg.temperature,
        max_output_tokens=cfg.max_output_tokens,
        request_timeout_s=cfg.request_timeout_s,
        ttl_seconds=cfg.ttl_seconds,
    )
