from __future__ import annotations

from audiobook.pdf_cleanup import collapse_whitespace, normalize_punctuation


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
