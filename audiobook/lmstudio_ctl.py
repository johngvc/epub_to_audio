"""Programmatic control of LM Studio model load/unload via the `lms` CLI.

Used to keep the adapt LLM out of RAM during render: the orchestrator loads the
configured model before Stage 2 (adapt) and unloads it afterward, so it never
coexists with Chatterbox TTS in Stage 4. Host-only — requires the `lms` binary
(installed with LM Studio). All functions degrade to a no-op when `lms` is
absent, so they are safe to call unconditionally.

The `runner` / `which` dependencies are injectable so behavior can be unit
tested without a real LM Studio install.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def lms_available(which: Which = shutil.which) -> bool:
    """True when the `lms` CLI is on PATH."""
    return which("lms") is not None


def loaded_entries(runner: Runner = _default_runner) -> list[dict[str, Any]]:
    """Return the parsed `lms ps --json` array (loaded model instances)."""
    cp = runner(["lms", "ps", "--json"])
    if cp.returncode != 0:
        return []
    try:
        data = json.loads(cp.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def is_loaded(model: str, runner: Runner = _default_runner) -> bool:
    """True if a loaded instance references ``model`` (matched against the full
    JSON of each entry, so it is robust to the exact field name LM Studio uses)."""
    return any(model in json.dumps(entry) for entry in loaded_entries(runner))


def ensure_loaded(
    model: str,
    *,
    context_length: int | None = None,
    ttl: int | None = None,
    runner: Runner = _default_runner,
    which: Which = shutil.which,
) -> str:
    """Load ``model`` if it is not already loaded.

    Returns one of: ``"unavailable"`` (no `lms`), ``"no-model"`` (empty model),
    ``"already-loaded"``, or ``"loaded"``.
    """
    if not model:
        return "no-model"
    if not lms_available(which):
        return "unavailable"
    if is_loaded(model, runner):
        return "already-loaded"
    argv: list[str] = ["lms", "load", model, "-y"]
    if context_length:
        argv += ["-c", str(context_length)]
    if ttl:
        argv += ["--ttl", str(ttl)]
    runner(argv)
    return "loaded"


def unload_all(runner: Runner = _default_runner, which: Which = shutil.which) -> str:
    """Unload every loaded model. Returns ``"unavailable"`` or ``"unloaded"``."""
    if not lms_available(which):
        return "unavailable"
    runner(["lms", "unload", "--all"])
    return "unloaded"
