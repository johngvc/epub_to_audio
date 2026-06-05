# Local Config Overlay + Configurable Output Folder — Design

**Date:** 2026-06-05
**Status:** Approved (design)

## Problem

The final `.m4b` output folder is hardcoded (`./out/book.m4b` in `bin/audiobook-run`;
`assemble --out` is a required flag). The user wants output written to a
machine-specific OneDrive folder, configured **locally** without committing that
path — and a generic, reusable local-override mechanism.

## Approach

Three layers, precedence **env > config.local.toml > config.toml**:

1. **Committed, generic:** add `[assemble].out_dir = "./out"` to `config.toml`.
2. **Local TOML overlay:** `load_config` deep-merges a gitignored
   `config.local.toml` (sibling of the config file) over `config.toml`; local
   wins. Generic — any field becomes locally overridable, still Pydantic-validated.
3. **Env override:** `AUDIOBOOK_OUT_DIR` overrides the resolved `out_dir`
   (mirrors the existing `OPENAI_*` env pattern in `resolve_adapt_api`).

The output filename derives from `[book].title` (sanitized), e.g.
`Learning Domain-Driven Design.m4b`.

## Changes

### `audiobook/config.py`
- `AssembleConfig`: add `out_dir: str = "./out"`.
- `load_config(path)`: deep-merge `path.with_name(f"{path.stem}.local{path.suffix}")`
  if it exists, then validate. Add `_deep_merge(base, overlay)`.
- `safe_filename(name, fallback="book")`: strip filesystem-illegal chars
  (`\ / : * ? " < > |` + control), collapse whitespace, fallback if empty.
- `resolve_out_path(cfg, explicit, title) -> Path`: explicit `--out` wins;
  else `Path(env_or_cfg_out_dir).expanduser() / f"{safe_filename(title)}.m4b"`.

### `audiobook/cli.py`
- `assemble_cmd`: `--out` becomes optional (`None`); resolve via
  `resolve_out_path` after the title check; `mkdir -p` the parent.
- New `out-path` command: prints the resolved output path (loads cfg + title,
  no ffmpeg). Used by the wrapper and handy for the user.

### `config.toml`
- Add `out_dir = "./out"` under `[assemble]` with a comment pointing to
  `config.local.toml` for local overrides.

### `bin/audiobook-run`
- Default `OUT=""`. Before the assemble stage, if `OUT` is empty,
  `OUT="$(bin/audiobook out-path --config "$CONFIG")"`. Still passes `--out "$OUT"`
  so downstream size/duration reporting is unchanged.

### `.gitignore`
- Add `config.local.toml`.

### Local file (not committed)
- Create `config.local.toml` with the OneDrive `out_dir`.

## Testing (`tests/test_config.py`)
- `AssembleConfig().out_dir == "./out"`.
- Deep-merge: local overlay overrides one field, leaves siblings intact.
- `safe_filename` strips illegal chars; empty → fallback.
- `resolve_out_path`: explicit wins; dir+title compose; `AUDIOBOOK_OUT_DIR` env wins.

No ffmpeg needed — all logic is pure path resolution.

## Out of scope
- No change to `assemble_book` internals or the m4b format.
