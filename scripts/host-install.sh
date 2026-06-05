#!/usr/bin/env bash
# Sets up the host-side venv for stage 4 (render / voice preview) and stage 5
# (assemble). Run from the project root.
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found on PATH. Install via: brew install uv" >&2
  exit 1
fi

# Stage 5 (assemble) shells out to ffmpeg on the host so it can write the final
# .m4b straight to the configured out_dir (often an external/cloud folder).
if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing ffmpeg (needed by 'audiobook assemble')..."
    brew install ffmpeg
  else
    echo "warning: ffmpeg not found and no brew to install it. 'audiobook assemble' will fail until ffmpeg is on PATH." >&2
  fi
fi

uv venv --python 3.12 .venv
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -e ".[render]"

echo
echo "Host venv ready at .venv. Smoke check:"
echo "  uv run audiobook --help"
