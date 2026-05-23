from audiobook.utils.slugify import slugify


def test_basic_lowercase() -> None:
    assert slugify("Chapter 5: Concurrency Primitives") == "chapter-5-concurrency-primitives"


def test_strips_unicode_and_punctuation() -> None:
    assert slugify("Café — résumé!") == "cafe-resume"


def test_collapses_separators() -> None:
    assert slugify("  Hello   World  ") == "hello-world"


def test_empty_yields_untitled() -> None:
    assert slugify("") == "untitled"
