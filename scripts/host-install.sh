#!/usr/bin/env bash
# Sets up the host-side venv for stage 4 (render / voice preview).
# Run from the project root.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on PATH. Install via: brew install uv" >&2
  exit 1
fi

uv venv --python 3.12 .venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e ".[render]"

echo
echo "Host venv ready at .venv. Smoke check:"
echo "  uv run audiobook --help"
