from __future__ import annotations

from audiobook.chunk import (
    apply_pronunciation,
    chunk_chapter,
    pack_sentences,
)
from audiobook.models import ChapterAdapted, ChapterChunks, PronunciationHint


def test_apply_pronunciation_acronym_case_sensitive() -> None:
    hints = [
        PronunciationHint(term="SQL", spoken_as="sequel", reason=""),
        PronunciationHint(term="kubectl", spoken_as="cube control", reason=""),
    ]
    text = "We deploy SQL queries via kubectl. (Note: 'sql' as a word is not replaced.)"
    out = apply_pronunciation(text, hints)
    assert "sequel" in out
    assert "cube control" in out
    assert "sql" in out  # lowercase preserved


def test_pack_sentences_under_max_chars() -> None:
    sentences = [
        "Short one.",
        "This is a slightly longer sentence that fits.",
        "Another short.",
        "X.",
    ]
    chunks = pack_sentences(sentences, max_chars=80, min_orphan_chars=20)
    assert all(len(c) <= 80 for c in chunks)
    # X. should be merged into the previous chunk (short orphan rule)
    assert not any(c == "X." for c in chunks)


def test_chunk_chapter_writes_expected_structure() -> None:
    adapted = ChapterAdapted(
        adapted_text=(
            "Paragraph one sentence one. Paragraph one sentence two.\n\n"
            "Paragraph two opens here. It contains two sentences.\n\n"
            "Final paragraph."
        ),
        pronunciation_hints=[],
        notes="",
    )
    cc = chunk_chapter(
        index=0,
        title="Intro",
        adapted=adapted,
        pronunciation=[],
        max_chars=400,
        paragraph_silence_ms=400,
        section_silence_ms=1200,
    )
    assert isinstance(cc, ChapterChunks)
    assert len(cc.chunks) >= 3
    para_breaks = [c for c in cc.chunks if c.trailing_silence_ms == 400]
    assert len(para_breaks) >= 2  # at least two paragraph boundaries
