# Trusted base: Astral's official uv image, ships uv preinstalled.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# System deps: ffmpeg for assembly (chapter markers via ffmetadata),
# espeak-ng for any fallback phonemization needs (referenced by source-spec §4).
# Note: spec §18 mentions `mp4chaps` from `mp4v2`, but that package was
# dropped from Debian Bookworm. We use ffmpeg's ffmetadata approach instead,
# which produces equivalently correct .m4b chapter markers in modern ffmpeg.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Install Python deps first (cacheable layer)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --extra dev || \
    uv sync --no-install-project --extra dev

# The project source is bind-mounted at runtime; install in editable mode
# via a small entry that re-syncs when the source is present.
COPY audiobook ./audiobook
RUN uv pip install --no-deps -e .

ENTRYPOINT []
CMD ["audiobook", "--help"]
