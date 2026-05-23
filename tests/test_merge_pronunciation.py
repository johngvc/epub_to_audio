from __future__ import annotations

import json
from pathlib import Path

from audiobook.adapt import merge_pronunciation


def _write_adapted(dir_: Path, name: str, hints: list[dict[str, str]]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(json.dumps({
        "adapted_text": "body",
        "pronunciation_hints": hints,
        "notes": "",
    }))


def test_dedupe_and_merge(scratch: Path) -> None:
    adapted = scratch / "chapters" / "adapted"
    _write_adapted(adapted, "00_a.json", [
        {"term": "kubectl", "spoken_as": "cube control", "reason": "CLI"},
        {"term": "SQL", "spoken_as": "sequel", "reason": "acronym"},
    ])
    _write_adapted(adapted, "01_b.json", [
        {"term": "kubectl", "spoken_as": "cube control", "reason": "CLI"},
        {"term": "k8s", "spoken_as": "kates", "reason": "acronym"},
    ])
    out = merge_pronunciation(scratch)
    assert out == scratch / "pronunciation.json"
    payload = json.loads(out.read_text())
    terms = {h["term"]: h["spoken_as"] for h in payload}
    assert terms == {"kubectl": "cube control", "SQL": "sequel", "k8s": "kates"}


def test_conflicting_spelling_keeps_first_and_notes_conflict(scratch: Path) -> None:
    adapted = scratch / "chapters" / "adapted"
    _write_adapted(adapted, "00_a.json", [{"term": "API", "spoken_as": "A P I", "reason": ""}])
    _write_adapted(adapted, "01_b.json", [{"term": "API", "spoken_as": "appy", "reason": ""}])
    out = merge_pronunciation(scratch)
    payload = json.loads(out.read_text())
    api = next(h for h in payload if h["term"] == "API")
    assert api["spoken_as"] == "A P I"
    assert "conflict" in api["reason"].lower()
