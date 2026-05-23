from __future__ import annotations

import json
from pathlib import Path

import pytest

from audiobook.adapt import (
    validate_adapted_dir,
    validate_adapted_file,
)
from audiobook.models import ChapterRaw

FIXTURES = Path(__file__).parent / "fixtures" / "adapted"


def _make_raw(scratch: Path, index: int, title: str, word_count: int) -> Path:
    raw_dir = scratch / "chapters" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    body = " ".join(["word"] * word_count)
    c = ChapterRaw(
        index=index,
        title=title,
        source_spine_id=f"ch{index:02d}.xhtml",
        html=f"<p>{body}</p>",
        word_count_estimate=word_count,
        has_code=False,
        has_math=False,
        has_tables=False,
    )
    f = raw_dir / f"{index:02d}_{title}.json"
    f.write_text(c.model_dump_json())
    return f


def test_valid_passes(scratch: Path) -> None:
    raw = _make_raw(scratch, 0, "intro", 100)
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    (adapted_dir / "00_intro.json").write_text((FIXTURES / "valid.json").read_text())
    outcome = validate_adapted_file(raw, adapted_dir / "00_intro.json")
    assert outcome.ok, outcome


@pytest.mark.parametrize(
    "fixture,expected_kind",
    [
        ("truncated.json", "json_parse_error"),
        ("prose_wrapped.json", "json_parse_error"),
        ("schema_mismatched.json", "schema_error"),
        ("markdown_artifact.json", "markdown_artifact"),
    ],
)
def test_known_bad_fixtures_rejected(
    scratch: Path, fixture: str, expected_kind: str
) -> None:
    raw = _make_raw(scratch, 0, "x", 100)
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    (adapted_dir / "00_x.json").write_text((FIXTURES / fixture).read_text())
    outcome = validate_adapted_file(raw, adapted_dir / "00_x.json")
    assert not outcome.ok
    assert outcome.error_kind == expected_kind, outcome


def test_too_short_flagged(scratch: Path) -> None:
    raw = _make_raw(scratch, 0, "x", 200)
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    (adapted_dir / "00_x.json").write_text((FIXTURES / "too_short.json").read_text())
    outcome = validate_adapted_file(raw, adapted_dir / "00_x.json")
    assert not outcome.ok
    assert outcome.error_kind == "length_anomaly"


def test_too_long_flagged(scratch: Path) -> None:
    raw = _make_raw(scratch, 0, "x", 100)
    adapted_dir = scratch / "chapters" / "adapted"
    adapted_dir.mkdir(parents=True)
    body = " ".join(["wordy"] * 250)
    payload = {"adapted_text": body, "pronunciation_hints": [], "notes": ""}
    (adapted_dir / "00_x.json").write_text(json.dumps(payload))
    outcome = validate_adapted_file(raw, adapted_dir / "00_x.json")
    assert not outcome.ok
    assert outcome.error_kind == "length_anomaly"


def test_validate_dir_reports_each_chapter(scratch: Path) -> None:
    _make_raw(scratch, 0, "good", 100)
    _make_raw(scratch, 1, "bad", 100)
    adapted = scratch / "chapters" / "adapted"
    adapted.mkdir(parents=True)
    (adapted / "00_good.json").write_text((FIXTURES / "valid.json").read_text())
    (adapted / "01_bad.json").write_text((FIXTURES / "markdown_artifact.json").read_text())

    report = validate_adapted_dir(scratch)
    by_idx = {r.chapter_index: r for r in report.results}
    assert by_idx[0].ok
    assert not by_idx[1].ok
    assert by_idx[1].error_kind == "markdown_artifact"
