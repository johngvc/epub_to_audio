# epub_to_audio

Local-first pipeline that converts technical EPUB books into `.m4b` audiobooks using Claude Sonnet 4.6 for content adaptation and Chatterbox TTS for narration.

See `epub_to_audio_spec.md` for the full spec and `docs/superpowers/specs/` for design decisions.

## Setup

1. Install [colima](https://github.com/abiosoft/colima) and Docker CLI (already required by `bin/dev`).
2. Build the image: `docker compose build`
3. (Host-side, only when ready for Stage 4 TTS) `scripts/host-install.sh`

## Usage

Everything goes through one wrapper:

    bin/audiobook parse ./input/book.epub --out ./work   # Docker
    bin/audiobook render ./work                          # host (MPS)

Run tests: `bin/audiobook-test`.
