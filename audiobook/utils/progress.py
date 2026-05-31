"""Shared formatter for ``--verbose`` per-step progress lines.

Every stage that supports verbose output routes its lines through
``pct_line`` so the format is identical across parse/adapt/chunk/render/
assemble.
"""
from __future__ import annotations


def pct_line(stage: str, done: int, total: int, detail: str = "") -> str:
    """Return a uniform verbose progress line.

    Format: ``[<stage>] <done>/<total> (<pct>%) <detail>`` where
    ``pct = round(done / total * 100)``, guarded to ``0`` when ``total`` is 0
    (nothing to do). ``detail`` is optional and appended after a space.
    """
    pct = round(done / total * 100) if total else 0
    line = f"[{stage}] {done}/{total} ({pct}%)"
    if detail:
        line = f"{line} {detail}"
    return line
