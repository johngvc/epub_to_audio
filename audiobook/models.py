"""Pydantic v2 models for every on-disk artifact in the pipeline."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    """Common base: forbid extras, validate on assignment."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=False)


class ChapterRaw(_Strict):
    index: int = Field(ge=0)
    title: str = Field(min_length=1)
    source_spine_id: str
    html: str
    word_count_estimate: int = Field(ge=0)
    has_code: bool
    has_math: bool
    has_tables: bool
    part: int | None = Field(default=None, ge=1)
    part_of: int | None = Field(default=None, ge=1)


class PronunciationHint(_Strict):
    term: str = Field(min_length=1)
    spoken_as: str = Field(min_length=1)
    reason: str = ""


class ChapterAdapted(_Strict):
    adapted_text: str = Field(min_length=1)
    pronunciation_hints: list[PronunciationHint] = Field(default_factory=list)
    notes: str = ""


class Chunk(_Strict):
    id: str = Field(pattern=r"^\d{4}$")
    text: str = Field(min_length=1, max_length=400)
    trailing_silence_ms: int = Field(ge=0, le=10_000)


class ChapterChunks(_Strict):
    index: int = Field(ge=0)
    title: str = Field(min_length=1)
    chunks: list[Chunk]

    @model_validator(mode="after")
    def _unique_chunk_ids(self) -> "ChapterChunks":
        ids = [c.id for c in self.chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("chunk ids must be unique within a chapter")
        return self


class BookMetadata(_Strict):
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    narrator: str = ""
    publisher: str = ""
    year: int | None = None
    genre: str = ""
    description: str = ""
