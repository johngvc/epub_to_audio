import pytest
from pydantic import ValidationError

from audiobook.models import (
    ChapterAdapted,
    ChapterChunks,
    ChapterRaw,
    Chunk,
    PronunciationHint,
)


def test_chapter_raw_minimal() -> None:
    raw = ChapterRaw(
        index=0,
        title="Intro",
        source_spine_id="ch01.xhtml",
        html="<p>Hello</p>",
        word_count_estimate=2,
        has_code=False,
        has_math=False,
        has_tables=False,
    )
    assert raw.index == 0


def test_pronunciation_hint_fields() -> None:
    h = PronunciationHint(term="kubectl", spoken_as="cube control", reason="CLI tool")
    assert h.term == "kubectl"


def test_chapter_adapted_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChapterAdapted(
            adapted_text="hi",
            pronunciation_hints=[],
            notes="",
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_chapter_adapted_empty_text_rejected() -> None:
    with pytest.raises(ValidationError):
        ChapterAdapted(adapted_text="", pronunciation_hints=[], notes="")


def test_chunk_max_chars_enforced() -> None:
    with pytest.raises(ValidationError):
        Chunk(id="0000", text="x" * 401, trailing_silence_ms=0)


def test_chapter_chunks_ids_unique() -> None:
    with pytest.raises(ValidationError):
        ChapterChunks(
            index=0,
            title="t",
            chunks=[
                Chunk(id="0000", text="a", trailing_silence_ms=0),
                Chunk(id="0000", text="b", trailing_silence_ms=0),
            ],
        )
