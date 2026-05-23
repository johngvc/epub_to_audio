from __future__ import annotations

import json
from pathlib import Path

from audiobook.state import State, load_state, save_state


def test_initialize_and_roundtrip(scratch: Path) -> None:
    s = State(epub_sha256="deadbeef", adapt_mode="agent")
    save_state(scratch, s)
    loaded = load_state(scratch)
    assert loaded.epub_sha256 == "deadbeef"
    assert loaded.adapt_mode == "agent"


def test_partial_update_persists(scratch: Path) -> None:
    s = State(epub_sha256="d", adapt_mode="agent")
    s.stages_completed["parse"] = True
    s.stages_completed["adapt"] = {"00": "done", "01": "failed"}
    save_state(scratch, s)
    raw = json.loads((scratch / "state.json").read_text())
    assert raw["stages_completed"]["parse"] is True
    assert raw["stages_completed"]["adapt"]["01"] == "failed"
