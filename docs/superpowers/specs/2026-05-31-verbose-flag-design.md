# Verbose flag (`--verbose` / `-v`) — design

Date: 2026-05-31

## Goal

Let the user see step-by-step progress with completion percentages across the
long-running pipeline stages, so they know how a run is doing.

## Decisions (from brainstorming)

- **Per-command flag**, not global. `bin/audiobook` routes by the first arg
  (subcommand) to pick Docker vs host; a global `--verbose` before the
  subcommand would break that routing. So: `audiobook render --verbose`.
- **Default output unchanged.** `--verbose` *adds* extra per-item lines; it
  does not remove or restyle today's output.
- **Granularity:** per-item lines with `X/Y (NN%)`, plus stage-specific extra
  detail (token counts for adapt, render time for render). **No ETA.**

## Scope

`--verbose` / `-v` is added to the iterating commands: `parse`, `adapt`,
`chunk`, `render`, `assemble`. `validate-*` and `status` are instant and are
not touched.

## Line format

A single shared helper produces every line:

```
[<stage>] <done>/<total> (<pct>%) <detail>
```

`audiobook/utils/progress.py`:

```python
def pct_line(stage: str, done: int, total: int, detail: str = "") -> str
```

- `pct = round(done / total * 100)`, guarded to `0` when `total == 0`.
- Trailing `detail` is optional and right-stripped.

Examples:

```
[parse]    6/29 (21%) Chapter 1: Introduction to Artificial Intelligence
[adapt]    2/2 (100%) 01_chapter-2 ok in=20964 out=12038 tok
[chunk]    1/2 (50%) Chapter 1 -> 136 chunks
[render]   152/387 (39%) ch01 0152 1.4s
[assemble] 2/2 (100%) 01_chapter-2-...
```

## Per-stage behavior

Each stage gains a `verbose: bool = False` parameter. When `True`, it emits the
extra lines through its progress sink; when `False`, behavior is identical to
today.

- **parse** (`parse_pdf`, `parse_epub`): emit one line per section as it is
  written, `i/total`. Sink: stderr (parse already logs to stderr).
- **adapt** (`run_adapt_api`): on each chapter completion, emit
  `done/total (pct%) <stem> <ok|fail> in=<n> out=<n> tok`. Existing per-attempt
  lines stay. Sink: same callback adapt already uses.
- **chunk** (`chunk_work_dir`): gains an optional `progress` callback; when
  verbose, emit one line per chapter chunked. Sink: stderr.
- **render** (`render_work_dir` + `render_chapter_chunks`): `render_chapter_chunks`
  gains an `on_chunk` callback fired once per chunk (rendered or skipped).
  `render_work_dir` precomputes the global chunk total, keeps a thread-safe
  counter (chapters render in parallel), and when verbose prints a global
  `done/total (pct%)` line per chunk. Existing per-chapter lines stay. Sink: stdout.
- **assemble** (`assemble_book`): gains an optional `progress` callback; when
  verbose, emit one line per chapter as it is concatenated. Sink: stderr.

## CLI wiring

Each command gets:

```python
verbose: bool = typer.Option(False, "--verbose", "-v", help="Per-step progress with %.")
```

passed through as `verbose=verbose` to the stage function (and, for chunk/assemble,
a stderr `progress` callback is supplied only when verbose).

## Testing (TDD)

- `pct_line`: formatting incl. rounding and `total == 0` guard.
- Each stage with `verbose=True` + a capturing sink emits the `X/Y (NN%)` lines
  in order; with `verbose=False` emits no extra lines. Reuse existing fixtures
  (`_FakeClient` for adapt, `_fake_tts` for render, etc.).
- CLI: `-v` reaches the stage (monkeypatched stage fn records the flag).

## Out of scope

ETA/rate, progress bars (non-TTY logs), restyling default output, verbose for
`validate-*`/`status`.
