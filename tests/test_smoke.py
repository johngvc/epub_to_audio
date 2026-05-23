from __future__ import annotations

import subprocess
import sys


def test_help_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "audiobook.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "audiobook" in result.stdout.lower()
