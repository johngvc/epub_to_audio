from __future__ import annotations

from audiobook.pdf_cleanup import (
    apply_footnote_policy,
    clean_pdf_markdown,
    collapse_whitespace,
    dehyphenate,
    normalize_punctuation,
    strip_page_artifacts,
)


def test_normalize_smart_quotes_to_ascii() -> None:
    assert normalize_punctuation("“Hello,” she said.") == '"Hello," she said.'
    assert normalize_punctuation("it’s fine") == "it's fine"


def test_normalize_dashes_and_ellipsis() -> None:
    # en-dash, em-dash -> hyphen; horizontal ellipsis -> three dots; nbsp -> space
    assert normalize_punctuation("pages 3–5") == "pages 3-5"
    assert normalize_punctuation("wait—what") == "wait-what"
    assert normalize_punctuation("and so on…") == "and so on..."
    assert normalize_punctuation("a b") == "a b"


def test_collapse_whitespace_runs_and_blank_lines() -> None:
    text = "a   b\t c\n\n\n\nd  \n"
    assert collapse_whitespace(text) == "a b c\n\nd"


def test_dehyphenate_joins_real_words() -> None:
    vocab = {"retrieval", "payment"}
    assert dehyphenate("re-\ntrieval", is_word=vocab.__contains__) == "retrieval"
    assert dehyphenate("pay-\nment due", is_word=vocab.__contains__) == "payment due"


def test_dehyphenate_keeps_genuine_compounds() -> None:
    # "ofthe" is not a word -> collapse the newline but keep the hyphen.
    vocab = {"state", "art"}
    assert dehyphenate("state-of-\nthe-art", is_word=vocab.__contains__) == "state-of-the-art"


def test_dehyphenate_preserves_normal_text() -> None:
    assert dehyphenate("no hyphen breaks here", is_word=lambda w: True) == "no hyphen breaks here"


def test_strip_orphan_page_numbers() -> None:
    text = "End of a paragraph.\n42\nStart of the next paragraph."
    assert strip_page_artifacts(text) == "End of a paragraph.\nStart of the next paragraph."


def test_strip_repeated_running_headers() -> None:
    # A short line repeated >= 3 times (a running header) is dropped everywhere.
    text = "\n".join(
        [
            "CHAPTER TITLE",
            "Real body line one.",
            "CHAPTER TITLE",
            "Real body line two.",
            "CHAPTER TITLE",
            "Real body line three.",
        ]
    )
    assert strip_page_artifacts(text) == (
        "Real body line one.\nReal body line two.\nReal body line three."
    )


def test_strip_keeps_markdown_headings_and_long_lines() -> None:
    # Headings (start with #) and long lines are never treated as artifacts.
    text = "# Chapter One\n# Chapter One\n# Chapter One\nbody"
    assert strip_page_artifacts(text) == text


def test_footnote_policy_skip_removes_definitions() -> None:
    text = "Body text.\n[1] A footnote definition.\nMore body."
    assert apply_footnote_policy(text, "skip") == "Body text.\nMore body."


def test_footnote_policy_inline_leaves_text_untouched() -> None:
    text = "Body text.\n[1] A footnote definition.\nMore body."
    assert apply_footnote_policy(text, "inline") == text


def test_footnote_policy_endnote_moves_to_end() -> None:
    text = "Body text.\n[^1]: A footnote definition.\nMore body."
    expected = "Body text.\nMore body.\n\n---\n\n## Notes\n\n[^1]: A footnote definition."
    assert apply_footnote_policy(text, "endnote") == expected


def test_clean_pdf_markdown_runs_full_pipeline() -> None:
    vocab = {"retrieval"}
    raw = "“re-\ntrieval”\n\n\n42\nbody"
    out = clean_pdf_markdown(raw, footnote_policy="skip", is_word=vocab.__contains__)
    assert out == '"retrieval"\n\nbody'


def test_footnote_policy_endnote_with_no_notes_returns_body() -> None:
    assert apply_footnote_policy("Just body.", "endnote") == "Just body."


def test_clean_pdf_markdown_normalizes_crlf_before_dehyphenating() -> None:
    vocab = {"retrieval"}
    assert clean_pdf_markdown("re-\r\ntrieval", is_word=vocab.__contains__) == "retrieval"
