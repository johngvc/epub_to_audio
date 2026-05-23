"""Audiobook pipeline CLI entry point."""
from __future__ import annotations

from pathlib import Path

import typer

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


if __name__ == "__main__":
    app()
