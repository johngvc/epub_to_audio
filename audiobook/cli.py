"""Audiobook pipeline CLI entry point."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

from audiobook.adapt import merge_pronunciation as _merge_pron
from audiobook.adapt import validate_adapted_dir as _validate_dir
from audiobook.parse import parse_epub as _parse_epub

app = typer.Typer(
    name="audiobook",
    help="EPUB-to-audiobook pipeline.",
    no_args_is_help=True,
)


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


if __name__ == "__main__":
    app()
