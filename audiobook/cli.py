"""Audiobook pipeline CLI entry point."""
from __future__ import annotations

import typer

app = typer.Typer(
    name="audiobook",
    help="EPUB-to-audiobook pipeline.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Root callback (placeholder; subcommands attached in later tasks)."""


if __name__ == "__main__":
    app()
