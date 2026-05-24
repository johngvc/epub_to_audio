# Autonomous `audiobook run` + Voice Library

## Problem

After implementing `[adapt].mode = "api"`, every stage of the pipeline can run unattended. But a user still has to:

1. Run 8 commands in sequence and check each one's exit status.
2. Know which dependencies need to be installed (Docker image, host venv, `[api]` extra) and run the install scripts manually.
3. Manage a single voice file (`voice/reference.wav`). No way to keep multiple curated voices alongside raw experimental samples.

This spec adds two coupled pieces:

- **`bin/audiobook run`**: a lean orchestrator that runs the full pipeline end-to-end with auto-install preflight and strict failure handling.
- **A voice library** under `voices/` with a CLI for adding/listing/selecting saved voices by name, plus integration into `run` and `render`.

The two ship together because `run` needs a way to pick a voice without forcing the user to type a file path.

## Goals

- One command — `bin/audiobook run` with defaults — produces a finished `.m4b` from a fresh checkout that has only the EPUB and a voice sample in place.
- Auto-install of every dependency the project itself owns (Docker image, host venv, Python extras, Colima on macOS).
- Multiple saved voices selectable by name, distinct from raw experimental samples.
- Backwards compatibility: existing `voice/reference.wav` setups keep working with a deprecation hint.

## Non-goals

- No automated install of system packages outside the project (brew packages, LM Studio, model files).
- No threshold-based failure handling (CLAUDE.md mentions thresholds; this spec uses strict-stop-on-any-failure instead — re-runs are idempotent so threshold logic doesn't pay for itself).
- No Python unit tests for the bash orchestrator itself. It's a thin sequencer over already-tested CLI commands; test value is low. Voice library code IS tested.
- No GUI / TUI. CLI only.
- No support for `mode = "agent"` in `run` (Claude Code can't be driven from a shell script). The run command rejects agent mode up front.

## Architecture

### Two new components

```
bin/audiobook              ← existing dispatcher; gains `run)` branch
bin/audiobook-run          ← NEW: bash orchestrator
audiobook/voice_library.py ← NEW: name → path resolution + save/list/rm
audiobook/cli.py           ← existing; gains `voice save|list|rm` + `--voice` on render
audiobook/voice.py         ← existing; unchanged
voices/                    ← NEW directory (gitignored, .gitkeep tracked)
```

### Voice resolution order

Centralized in `audiobook.voice_library.resolve_voice_path(name: str | None, cfg: AppConfig, project_root: Path) -> Path`.

Resolution order:

1. If `name` is non-empty → `voices/<name>.wav`. Must exist or raise.
2. Else if `cfg.render.voice` non-empty → `voices/<cfg.render.voice>.wav`. Must exist or raise.
3. Else if `voices/default.wav` exists → use it.
4. Else if `voice/reference.wav` exists → use it AND log a one-line deprecation suggesting `audiobook voice save voice/reference.wav --name default`.
5. Else raise `NoVoiceConfigured` with: "no voice selected. Run `audiobook voice save SAMPLE --name NAME` to register one, or pass `--voice NAME`."

Every entrypoint that needs a voice path (`audiobook render`, `audiobook voice preview`, `bin/audiobook-run`'s preflight) goes through this helper.

### New voice CLI commands

| Command | Behavior |
|---|---|
| `audiobook voice save SAMPLE --name NAME [--preview] [--force]` | Reads `SAMPLE` (any format `afconvert` or `ffmpeg` can read). Converts to 24 kHz mono PCM. Validates via existing `validate_voice_reference`. Writes `voices/NAME.wav`. With `--preview`, also runs `voice preview` to produce `voices/NAME.preview.wav`. Refuses to overwrite existing without `--force`. |
| `audiobook voice list` | One line per voice in `voices/`: `name, duration, sample_rate, kB`. Prefixes the voice that `resolve_voice_path(None, cfg, project_root)` returns with `*` so the user can see which voice an unflagged `run` would pick. |
| `audiobook voice rm NAME [--force]` | Deletes `voices/NAME.wav`. Confirms unless `--force`. Also removes `voices/NAME.preview.wav` if present. |
| `audiobook voice preview [--voice NAME \| PATH]` | Backwards-compatible: positional `PATH` still works. With `--voice NAME` resolves via the library. |
| `audiobook voice validate PATH` | Unchanged. Operates on a file path. |

Cross-platform conversion: macOS uses `afconvert` (built in). Otherwise we shell out to `ffmpeg`. If neither is present, abort with a clear error message.

### New `[render].voice` config field

```toml
[render]
voice = ""               # saved voice name; empty = voices/default.wav, then voice/reference.wav (legacy)
device = "mps"
workers = 2
...
```

Added to `RenderConfig` as `voice: str = ""`. No test changes needed beyond extending `test_loads_repo_default`.

### `audiobook render` gains `--voice NAME`

The existing render CLI declares `voice: Path = typer.Option(Path("./voice/reference.wav"), exists=True, dir_okay=False)`. We change the type to `str` (value may be a name or a path) and remove the eager existence check. Inside the handler, the value is passed to `resolve_voice_path(value, cfg, project_root)`.

`resolve_voice_path` interprets the value as:

- A **path** if it contains a path separator (`/`) OR matches an existing file on disk — used as-is, must exist.
- A **name** otherwise — resolved to `voices/<value>.wav`, must exist.

If the value is empty/None, the resolver falls back through `[render].voice` config → `voices/default.wav` → legacy `voice/reference.wav` as described above.

This keeps `render`'s call site minimal: it passes whatever the user typed, the helper sorts it out. Existing callers that pass `--voice voice/reference.wav` keep working.

### `bin/audiobook run` (the orchestrator)

**CLI surface:**

```sh
bin/audiobook run [INPUT_EPUB] [--out OUT] [--work DIR] [--config FILE] [--voice NAME] [--fresh] [--skip-preflight] [--help]
```

Defaults: `INPUT_EPUB=./input/book.epub`, `OUT=./out/book.m4b`, `WORK=./work`, `CONFIG=./config.toml`.

**Dispatcher integration:** `bin/audiobook` adds a `run)` branch that `exec`s `bin/audiobook-run "$@"`. The orchestrator is a bash script, not a Python subcommand, because it sequences calls to `bin/audiobook <stage>` and benefits from `set -euo pipefail`.

**Preflight checks (in order):**

| Check | Action if missing |
|---|---|
| EPUB at `INPUT_EPUB` exists | Abort with clear message |
| `voices/` dir exists | `mkdir -p voices` |
| Voice resolvable (via `--voice` → config → `voices/default.wav` → `voice/reference.wav`) | If only a non-wav sample exists in `voice/`, log deprecation hint pointing to `voice save` and one-shot convert it to `voice/reference.wav` (back-compat). Otherwise abort with the resolver's error. |
| `[book].title` and `[book].author` set (in config or via env) | Abort, point to README |
| **macOS only**: Colima running | Auto-run `colima start` |
| Docker image `audiobook:dev` built | Auto-run `docker compose build audiobook` |
| Host `.venv/bin/audiobook` exists | Auto-run `scripts/host-install.sh` |
| If `[adapt].mode == "api"`: `openai` package in venv | Auto-run `uv pip install --python .venv/bin/python -e ".[api]"` |
| If `[adapt].mode == "api"`: LM Studio reachable at `[adapt.api].base_url` | Abort: "start LM Studio and load model X" |
| If `[adapt].mode == "api"`: model from config in LM Studio's `/v1/models` list | Abort: "load <model> in LM Studio" |
| If `[adapt].mode == "agent"`: reject | Abort: "run cannot drive agent mode; use the manual workflow or set [adapt].mode = \"api\"" |

`--skip-preflight` bypasses everything above.
`--fresh` runs `rm -rf "$WORK"` before starting (after a confirm prompt unless `--force`).

**Stage execution (strict-failure mode):**

For each of the 8 stages, print a banner with stage number and name. Run via `bin/audiobook <stage> ...`. Capture exit code. On non-zero, print which stage failed and the resume hint (`re-run bin/audiobook run to pick up from there`) then exit non-zero.

Stage order:
1. `parse INPUT --out WORK`
2. `adapt WORK` (only in api mode)
3. `validate-adapted WORK`
4. `merge-pronunciation WORK`
5. `chunk WORK`
6. `render WORK --voice <resolved-name-or-path>`
7. `validate-render WORK`
8. `assemble WORK --out OUT`

`title`/`author` for assemble come from config (existing behavior). The orchestrator does NOT pass `--title`/`--author` — that's the user's responsibility via config.

**Summary at end:**

```
✓ Audiobook ready: ./out/book.m4b
  size:      435 KB
  duration:  52s
  chapters:  3

Per-stage wall time:
  parse                4s
  adapt               16s
  validate-adapted     1s
  merge-pronunciation  1s
  chunk                2s
  render              18s
  validate-render      1s
  assemble             3s
  total              46s
```

Computed by `time` per stage (storing in a temp file) and `ffprobe`/`afinfo` for duration. If `ffprobe`/`afinfo` aren't available, the duration line is omitted gracefully.

### Gitignore

Add `voices/` (mirroring `voice/`). Keep `voices/.gitkeep` tracked so the directory exists in fresh checkouts.

### Backwards compatibility

- `voice/reference.wav` continues to work as a fourth-tier fallback in resolution, with a one-shot deprecation log.
- `audiobook render --voice voice/reference.wav` (path form) continues to work because resolve_voice_path treats values containing `/` or existing as a file as literal paths.
- `audiobook voice preview voice/reference.wav` (positional path) continues to work.

## Testing

`tests/test_voice_library.py` (new):

- `test_resolve_with_cli_name`
- `test_resolve_with_config_voice`
- `test_resolve_default_wav`
- `test_resolve_legacy_reference_wav`
- `test_resolve_raises_when_nothing_configured`
- `test_voice_save_writes_wav_and_validates` (uses a stub sample WAV)
- `test_voice_save_refuses_overwrite_without_force`
- `test_voice_list_marks_default`
- `test_voice_rm_removes_file_and_preview`

`tests/test_voice_validate.py` and `tests/test_render_plumbing.py` — extend to cover the path-vs-name detection.

`tests/test_config.py` — extend `test_loads_repo_default` to assert `cfg.render.voice == ""`.

No tests for `bin/audiobook-run` (bash orchestrator). Smoke-tested by running it end-to-end on the test EPUB.

## Failure handling

- Resolver errors print actionable messages with the exact next command (`audiobook voice save ... --name ...`).
- Preflight failures abort with the install command the orchestrator would have run, in case the user wants to do it manually.
- Stage failures print the failing stage + resume hint.
- All errors go to stderr; success messages to stdout.

## Open questions

None for this iteration. Future work (separate spec) could add:

- `audiobook voice rename OLD NEW`
- Per-voice notes/metadata (the rejected Option B from brainstorming).
- Cover-art auto-extraction from EPUB.
- `audiobook run --dry-run` that lists what would happen.
- Threshold-based failure tolerance (rejected here; could revisit if strict-fail proves too annoying).
