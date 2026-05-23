"""work/state.json management. Schema mirrors source-spec §13."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CostLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    usd_total: float = 0.0
    note: str = "agent mode — no per-token billing; counts against Claude Code subscription limits"


class FailureEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    chapter: str
    reason: str


class State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epub_sha256: str
    started_at: str = Field(default_factory=lambda: _dt.datetime.now(_dt.UTC).isoformat())
    adapt_mode: str = "agent"
    stages_completed: dict[str, Any] = Field(
        default_factory=lambda: {
            "parse": False,
            "adapt": {},
            "chunk": [],
            "render": {},
            "assemble": False,
        }
    )
    cost: CostLedger = Field(default_factory=CostLedger)
    voice_reference_sha256: str = ""
    voice_preview_done: bool = False
    failures: list[FailureEntry] = Field(default_factory=list)


def state_path(work_dir: Path) -> Path:
    return Path(work_dir) / "state.json"


def save_state(work_dir: Path, state: State) -> None:
    p = state_path(work_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(state.model_dump_json(indent=2) + "\n")


def load_state(work_dir: Path) -> State:
    return State.model_validate_json(state_path(work_dir).read_text())
