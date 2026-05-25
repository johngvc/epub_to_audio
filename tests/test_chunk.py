from __future__ import annotations

from audiobook.chunk import (
    apply_pronunciation,
    chunk_chapter,
    pack_sentences,
    sanitize_spoken_as,
    split_long_sentence,
)
from audiobook.models import ChapterAdapted, ChapterChunks, PronunciationHint


def test_sanitize_strips_dashes_from_stress_marks() -> None:
    assert sanitize_spoken_as("MEER-ah") == "meer ah"
    assert sanitize_spoken_as("BYAR-neh stroo-stroop") == "byar neh stroo stroop"


def test_sanitize_preserves_pure_all_caps_acronyms() -> None:
    # Pure ALL-CAPS like "MIT" must NOT be lowercased — TTS may otherwise read
    # "mit" as a single word rhyming with "hit".
    assert sanitize_spoken_as("MIT") == "MIT"
    assert sanitize_spoken_as("HTTP") == "HTTP"


def test_sanitize_preserves_pure_lowercase() -> None:
    assert sanitize_spoken_as("cube control") == "cube control"
    assert sanitize_spoken_as("ay yah fyat la yo kutl") == "ay yah fyat la yo kutl"


def test_sanitize_collapses_whitespace() -> None:
    assert sanitize_spoken_as("byar  neh   stroop") == "byar neh stroop"
    assert sanitize_spoken_as("  Meera  ") == "meera"


def test_sanitize_handles_underscores() -> None:
    assert sanitize_spoken_as("L_net plus L_db") == "l net plus l db"


def test_apply_pronunciation_sanitizes_dashed_spoken_as() -> None:
    """Hints with dashed stress marks (the common LLM failure mode) must be
    substituted in cleaned form, not literally."""
    hints = [
        PronunciationHint(term="Mira", spoken_as="MEER-ah", reason=""),
        PronunciationHint(term="Stroustrup", spoken_as="STROO-stroop", reason=""),
    ]
    text = "Mira and Stroustrup met."
    out = apply_pronunciation(text, hints)
    assert "-" not in out
    assert "meer ah" in out
    assert "stroo stroop" in out


def test_apply_pronunciation_sanitization_preserves_acronym_spoken_as() -> None:
    """A user who writes spoken_as='MIT' literally (acronym) keeps it."""
    hints = [PronunciationHint(term="MIT", spoken_as="MIT", reason="")]
    out = apply_pronunciation("She studied at MIT.", hints)
    assert "MIT" in out


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


def test_split_long_sentence_at_clause_boundaries() -> None:
    # 410-char sentence with commas — pronunciation expansion can produce this.
    sent = (
        "And he does more than explain the principles of D D D. The latter portion "
        "of the book shares some important practices that have evolved from D D D, "
        "such as EventStorming, addresses the problem of evolving the business focus "
        "or organization and how this might affect the software, and discusses how "
        "D D D aligns with microservices and how you can integrate it with a slew "
        "of well-known software patterns."
    )
    parts = split_long_sentence(sent, max_chars=400)
    assert len(parts) >= 2
    assert all(len(p) <= 400 for p in parts)
    # Roundtripping the joined parts should preserve the original text.
    assert " ".join(parts).replace("  ", " ") == sent.replace("  ", " ")


def test_split_long_sentence_no_separators_falls_back_to_whitespace() -> None:
    sent = "word " * 100  # 500 chars, no clause separators
    parts = split_long_sentence(sent.strip(), max_chars=100)
    assert all(len(p) <= 100 for p in parts)


def test_pack_sentences_handles_oversize_input() -> None:
    """A sentence larger than max_chars must not produce an oversize chunk."""
    big = "alpha, " * 80  # 560 chars, splittable on commas
    chunks = pack_sentences([big.strip().rstrip(",")], max_chars=400, min_orphan_chars=20)
    assert all(len(c) <= 400 for c in chunks)


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
