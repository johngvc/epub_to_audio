# Adapt API Mode — LM Studio (OpenAI-compatible)

## Problem

Stage 2 (`adapt`) is the only stage that cannot run unattended. Today it requires
`mode = "agent"`, where an external orchestrator (Claude Code) dispatches one
subagent per chapter. `config.AdaptConfig` already declares
`mode: Literal["agent", "chat", "api"]`, but `api` is unimplemented.

This spec implements `mode = "api"` against a local LM Studio server
(OpenAI-compatible at `http://localhost:1234/v1` by default), so the full
pipeline can run from one command with no human in the loop.

## Goals

- A new `audiobook adapt ./work` subcommand that, when `[adapt].mode = "api"`,
  drives the entire adapt stage in-process against LM Studio.
- Same output contract as agent mode: writes `work/chapters/adapted/NN_*.json`
  files that pass `validate_adapted_file`.
- Idempotent: re-running skips chapters whose adapted file already validates.
- Honors the same retry policy as CLAUDE.md prescribes for agent mode
  (up to 2 retries with validator error injected into the prompt).
- Includes the whole-book context (`work/book_full_text.md`) automatically when
  it fits in the model's context window.

## Non-goals

- Not implementing `mode = "chat"`. (Could be added later as a sibling adapter.)
- No Anthropic, OpenAI, or other hosted backend support. The new code targets
  *any* OpenAI-compatible endpoint, but tested only against LM Studio for now.
- No cost tracking. `budget_usd` becomes a no-op in api mode (local inference
  is free). Documented but not enforced.
- No prompt caching. `prompt_cache` is Anthropic-specific; ignored in api mode.

## Architecture

### New module: `audiobook/adapt_api.py`

Owns LLM transport and the per-chapter loop. Imports the OpenAI SDK lazily so
the existing Docker image (which does not have `openai` installed) keeps
working for non-api-mode workflows.

Surface:

```python
def run_adapt_api(
    work_dir: Path,
    *,
    cfg: AppConfig,
    progress: Callable[[str], None] | None = None,
) -> AdaptRunSummary: ...
```

`AdaptRunSummary` is a small dataclass:

```python
@dataclass
class AdaptRunSummary:
    succeeded: list[str]    # adapted_path stems
    retried: list[str]      # succeeded eventually but needed >=1 retry
    failed: list[tuple[str, str]]   # (stem, last_error_detail)
    total_input_tokens: int
    total_output_tokens: int
    wall_seconds: float
    included_book_context: bool
```

### Config additions: `[adapt.api]`

New nested block in `AdaptConfig`:

```toml
[adapt]
mode = "api"
concurrency = 1            # adapt.concurrency — see below
...

[adapt.api]
base_url = "http://localhost:1234/v1"
model    = "qwen2.5-14b-instruct"   # whatever the user loaded in LM Studio
api_key  = "lm-studio"      # LM Studio ignores this; sent to satisfy the SDK
context_window = 16384      # used to decide whether to include book_full_text.md
temperature = 0.3           # lower → more schema-compliant JSON
max_output_tokens = 8192    # caps a single response
request_timeout_s = 600     # generous; large chapters are slow on local GPU
```

Env-var overrides (so users can keep secrets out of config.toml even though
LM Studio doesn't need one):

| env var          | overrides                |
|------------------|--------------------------|
| `OPENAI_BASE_URL`| `[adapt.api].base_url`   |
| `OPENAI_MODEL`   | `[adapt.api].model`      |
| `OPENAI_API_KEY` | `[adapt.api].api_key`    |

Env overrides win when set and non-empty.

### Concurrency

Default to **sequential (1)** in api mode, regardless of `[adapt].concurrency`.
Rationale: LM Studio's local server typically serializes inference on a single
GPU; parallel requests pile up in its queue without speeding anything up, and
risk OOM with larger models.

A new `--concurrency N` CLI flag on `audiobook adapt` overrides this when the
user is running an actual batched setup. We log the effective concurrency at
start so the override is visible.

### Whole-book context decision

At start of the run, estimate `book_full_text.md` token count using a cheap
heuristic (`tokens ≈ len(text) // 4`, the standard ~4-chars-per-token rule of
thumb) and compare against `context_window * 0.6` (60% budget to leave room
for system prompt, chapter, response). If it fits, include it; if not, skip
it and log once.

Per-chapter prompts then either include or omit the `<book_context>` section
based on this single up-front decision (no per-chapter recomputation).

### Per-chapter loop

```
for raw_path in sorted(chapters/raw/*.json):
    adapted_path = chapters/adapted/<same_name>
    if existing adapted_path passes validate_adapted_file:
        skip (idempotent)
    attempts = 0
    last_error = None
    while attempts <= 2:
        messages = build_messages(raw, book_context if included, last_error)
        response = openai_client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=cfg.temperature,
            max_tokens=cfg.max_output_tokens,
            timeout=cfg.request_timeout_s,
        )
        # accumulate usage tokens
        write adapted_path
        outcome = validate_adapted_file(raw_path, adapted_path)
        if outcome.ok:
            record success (and retried if attempts > 0)
            break
        last_error = (outcome.error_kind, outcome.detail)
        attempts += 1
    else:
        record failure with last_error; delete the bad adapted_path
                 (so the next run isn't blocked by an invalid file)
```

### Prompt construction

System: contents of `prompts/adapt_system.md` verbatim, unchanged.

User: a small structured message:

```
Chapter to adapt (JSON):
<raw chapter json — index, title, html, features>

Whole-book context (markdown, may be truncated):
<contents of book_full_text.md, only if it fits>

Previous attempt failed validation with:
  error_kind: <kind>
  detail: <message>
Please correct the issue and produce a valid response this time.
```

The last block only appears on retry attempts. The system prompt already
demands JSON-only output, so we don't repeat that instruction.

### CLI

```python
@app.command("adapt")
def adapt_cmd(
    work_dir: Path,
    config: Path = Path("./config.toml"),
    concurrency: int | None = None,  # override
):
    cfg = load_config(config)
    if cfg.adapt.mode == "agent":
        echo("agent mode is driven by an external orchestrator (Claude Code). "
             "Set [adapt].mode = \"api\" in config.toml to run unattended.", err=True)
        raise Exit(2)
    if cfg.adapt.mode != "api":
        echo(f"unsupported adapt mode: {cfg.adapt.mode}", err=True)
        raise Exit(2)
    summary = run_adapt_api(work_dir, cfg=cfg, progress=print)
    echo_summary(summary)
    raise Exit(0 if not summary.failed else 1)
```

### Dependencies

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
api = [
    "openai>=1.50",
]
```

A new `[api]` extra (not part of `[render]`) because the Docker image — which
runs adapt for the agent flow — doesn't need it, but a host venv that runs
api mode does. Install with `uv pip install -e ".[api]"`.

The `bin/audiobook` wrapper will route the new `adapt` subcommand to the host
venv (where `openai` is installed). Docker users using agent mode are unaffected.

### Routing in `bin/audiobook`

Add `adapt` to the host-routed subcommands alongside `render` and `voice preview`,
since the host venv is where the openai SDK lives.

## Testing

New `tests/test_adapt_api.py`:

1. **Happy path** — mocked OpenAI client returns a valid JSON adapted chapter
   on first try; assert file is written, summary counts are right.
2. **Retry on schema error** — mock returns malformed JSON, then valid JSON
   on retry; assert `retried` list contains the chapter and the validator
   error appears in the second-attempt prompt.
3. **Hard failure after 2 retries** — mock always returns bad JSON; assert
   `failed` populated, bad adapted file is deleted (so next run can re-try),
   exit code 1.
4. **Skip already-valid chapters** — pre-populate a valid adapted file;
   assert the mock is never called for that chapter.
5. **Whole-book context decision** — long fake `book_full_text.md` skips
   context; short one includes it; verify via the prompt the mock receives.
6. **Concurrency override** — passing `--concurrency 4` results in 4 parallel
   in-flight requests in the mock.

All tests stub the OpenAI client at the seam (`openai.OpenAI` is constructed
inside `run_adapt_api`; tests inject a fake via a module-level factory).
No live LM Studio dependency in CI.

## Failure handling

- **LM Studio unreachable**: surface `httpx.ConnectError` as a clear "couldn't
  reach LM Studio at <base_url>" message and exit 2.
- **Model not loaded**: LM Studio returns a 4xx; surface verbatim and exit 2.
- **Timeout**: counts as a retry attempt; logged with the chapter id.
- **Invalid `response_format` for the model**: LM Studio falls back to free-form;
  the validator catches malformed JSON and retries. No special handling needed.

## Open questions

None for this iteration. Future work (separate spec) could add:

- A `chat` mode that uses Anthropic's SDK directly with prompt caching and
  budget tracking.
- A generic `LLMTransport` abstraction once we have two backends to share code
  between.
- Real cost tracking against `budget_usd` for paid backends.
