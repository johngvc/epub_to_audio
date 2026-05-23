"""Stage 2 helpers — validators and merge utilities. Agent mode uses these
via the CLI; this module deliberately contains no LLM transport code."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from audiobook.models import ChapterAdapted, ChapterRaw, PronunciationHint

ErrorKind = Literal[
    "missing_file",
    "json_parse_error",
    "schema_error",
    "markdown_artifact",
    "length_anomaly",
    "empty_text",
]

_MARKDOWN_ARTIFACT_PATTERNS = (
    re.compile(r"<pre[\s>]"),
    re.compile(r"```"),
    re.compile(r"<table[\s>]"),
    re.compile(r"\$\$"),
    re.compile(r"<h1[\s>]", re.I),
)

LENGTH_RATIO_MIN = 0.30
LENGTH_RATIO_MAX = 1.10


@dataclass(slots=True)
class ValidationOutcome:
    chapter_index: int
    raw_path: Path
    adapted_path: Path
    ok: bool
    error_kind: ErrorKind | None = None
    detail: str = ""
    length_ratio: float | None = None


@dataclass(slots=True)
class ValidationReport:
    results: list[ValidationOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "results": [
                    {
                        "chapter_index": r.chapter_index,
                        "raw_path": str(r.raw_path),
                        "adapted_path": str(r.adapted_path),
                        "ok": r.ok,
                        "error_kind": r.error_kind,
                        "detail": r.detail,
                        "length_ratio": r.length_ratio,
                    }
                    for r in self.results
                ],
            },
            indent=2,
        )


def _markdown_artifact(text: str) -> str | None:
    for pat in _MARKDOWN_ARTIFACT_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None


def validate_adapted_file(raw_path: Path, adapted_path: Path) -> ValidationOutcome:
    raw = ChapterRaw.model_validate_json(Path(raw_path).read_text())
    outcome = ValidationOutcome(
        chapter_index=raw.index, raw_path=Path(raw_path), adapted_path=Path(adapted_path), ok=False
    )
    if not Path(adapted_path).exists():
        outcome.error_kind = "missing_file"
        outcome.detail = "adapted file does not exist"
        return outcome
    text = Path(adapted_path).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        outcome.error_kind = "json_parse_error"
        outcome.detail = f"line {exc.lineno} col {exc.colno}: {exc.msg}"
        return outcome
    try:
        adapted = ChapterAdapted.model_validate(data)
    except ValidationError as exc:
        outcome.error_kind = "schema_error"
        outcome.detail = str(exc).splitlines()[0]
        return outcome

    artifact = _markdown_artifact(adapted.adapted_text)
    if artifact:
        outcome.error_kind = "markdown_artifact"
        outcome.detail = f"matched: {artifact!r}"
        return outcome

    src_words = max(raw.word_count_estimate, 1)
    adp_words = len(adapted.adapted_text.split())
    ratio = adp_words / src_words
    outcome.length_ratio = ratio
    if ratio < LENGTH_RATIO_MIN or ratio > LENGTH_RATIO_MAX:
        outcome.error_kind = "length_anomaly"
        outcome.detail = f"ratio={ratio:.2f} outside [{LENGTH_RATIO_MIN}, {LENGTH_RATIO_MAX}]"
        return outcome

    outcome.ok = True
    return outcome


def validate_adapted_dir(work_dir: Path) -> ValidationReport:
    work_dir = Path(work_dir)
    raw_dir = work_dir / "chapters" / "raw"
    adapted_dir = work_dir / "chapters" / "adapted"
    report = ValidationReport()
    for raw_path in sorted(raw_dir.glob("*.json")):
        adapted_path = adapted_dir / raw_path.name
        report.results.append(validate_adapted_file(raw_path, adapted_path))
    return report


def merge_pronunciation(work_dir: Path) -> Path:
    """Combine all chapters' pronunciation_hints into work/pronunciation.json.

    Deduplication policy: first occurrence wins for `spoken_as`; conflicting
    later spellings are recorded in the `reason` field so the user can review.
    """
    work_dir = Path(work_dir)
    adapted_dir = work_dir / "chapters" / "adapted"
    merged: dict[str, PronunciationHint] = {}
    conflicts: dict[str, set[str]] = {}

    for f in sorted(adapted_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            chapter = ChapterAdapted.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            continue
        for h in chapter.pronunciation_hints:
            if h.term not in merged:
                merged[h.term] = h
            elif merged[h.term].spoken_as != h.spoken_as:
                conflicts.setdefault(h.term, set()).add(h.spoken_as)

    out_list = []
    for term, hint in merged.items():
        reason = hint.reason
        if term in conflicts:
            alt = ", ".join(sorted(conflicts[term]))
            reason = (reason + " " if reason else "") + f"[conflict with: {alt}]"
        out_list.append({"term": hint.term, "spoken_as": hint.spoken_as, "reason": reason})

    out = work_dir / "pronunciation.json"
    out.write_text(json.dumps(out_list, indent=2) + "\n")
    return out
