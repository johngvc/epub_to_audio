"""TOML config loader with strict Pydantic validation."""
from __future__ import annotations

import tomllib
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


class AdaptConfig(_Strict):
    mode: Literal["agent", "chat", "api"] = "agent"
    model_label: str = "claude-sonnet-4-6"
    concurrency: int = Field(default=8, ge=1, le=32)
    split_long_chapters_at_words: int = Field(default=6000, ge=500)
    max_tokens_per_call: int = 8192
    budget_usd: float = 15.0
    prompt_cache: bool = True


class ChunkConfig(_Strict):
    max_chars: int = Field(default=400, ge=50, le=600)
    paragraph_silence_ms: int = Field(default=400, ge=0, le=5000)
    section_silence_ms: int = Field(default=1200, ge=0, le=10_000)


class RenderConfig(_Strict):
    device: str = "mps"
    workers: int = Field(default=2, ge=1, le=8)
    exaggeration: float = 0.4
    cfg_weight: float = 0.5
    temperature: float = 0.7
    multilingual: bool = False


class AssembleConfig(_Strict):
    audio_bitrate_kbps: int = 64
    sample_rate_hz: int = 24000


class AppConfig(_Strict):
    book: BookConfig = Field(default_factory=BookConfig)
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
