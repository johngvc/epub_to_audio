"""Audiobook pipeline CLI entry point."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

from audiobook.adapt import merge_pronunciation as _merge_pron
from audiobook.adapt import validate_adapted_dir as _validate_dir
from audiobook.chunk import chunk_work_dir as _chunk_dir
from audiobook.config import load_config
from audiobook.parse import parse_epub as _parse_epub
from audiobook.state import load_state
from audiobook.voice import validate_voice_reference

app = typer.Typer(
    name="audiobook",
    help="EPUB-to-audiobook pipeline.",
    no_args_is_help=True,
)

voice_app = typer.Typer(name="voice", help="Voice reference utilities.")
app.add_typer(voice_app)


@voice_app.command("validate")
def voice_validate(path: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:  # noqa: B008
    r = validate_voice_reference(path)
    typer.echo(f"path: {r.path}")
    for k, v in r.info.items():
        typer.echo(f"  {k}: {v}")
    for p in r.problems:
        typer.echo(f"PROBLEM: {p}")
    for w in r.warnings:
        typer.echo(f"warning: {w}")
    if not r.ok:
        raise typer.Exit(1)
    typer.echo("ok")


@app.callback()
def _root() -> None:
    """Root callback (placeholder; subcommands attached in later tasks)."""


@app.command("parse")
def parse(
    epub_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),  # noqa: B008
    out: Path = typer.Option(Path("./work"), "--out", help="Output work directory."),  # noqa: B008
) -> None:
    """Stage 1 — parse an EPUB into per-chapter JSON + book_full_text.md."""
    chapters = _parse_epub(epub_path, out)
    typer.echo(f"parsed {len(chapters)} chapters -> {out}")


@app.command("validate-adapted")
def validate_adapted(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Validate every chapters/adapted/*.json file. Emits JSON report on stdout."""
    report = _validate_dir(work_dir)
    typer.echo(report.to_json())
    sys.exit(0 if report.ok else 1)


@app.command("merge-pronunciation")
def merge_pron(work_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:  # noqa: B008
    """Merge per-chapter pronunciation hints into work/pronunciation.json."""
    out = _merge_pron(work_dir)
    typer.echo(f"wrote {out}")


@app.command("chunk")
def chunk_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),  # noqa: B008
) -> None:
    """Stage 3 — chunk adapted chapters into TTS-sized pieces."""
    cfg = load_config(config)
    n = _chunk_dir(
        work_dir,
        max_chars=cfg.chunk.max_chars,
        paragraph_silence_ms=cfg.chunk.paragraph_silence_ms,
        section_silence_ms=cfg.chunk.section_silence_ms,
    )
    typer.echo(f"chunked {n} chapters")


@app.command("status")
def status(work_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:  # noqa: B008
    """Print a human-readable summary of work/state.json."""
    try:
        state = load_state(work_dir)
    except FileNotFoundError:
        typer.echo("no state.json yet — nothing has run")
        raise typer.Exit(0)
    typer.echo(state.model_dump_json(indent=2))


if __name__ == "__main__":
    app()
