"""Audiobook pipeline CLI entry point."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

from audiobook.adapt import merge_pronunciation as _merge_pron
from audiobook.adapt import validate_adapted_dir as _validate_dir
from audiobook.assemble import assemble_book as _assemble
from audiobook.chunk import chunk_work_dir as _chunk_dir
from audiobook.config import AppConfig, load_config
from audiobook.parse import parse_epub as _parse_epub
from audiobook.parse_pdf import PdfParseError, parse_pdf as _parse_pdf
from audiobook.render import render_work_dir, validate_render_dir as _validate_render_dir
from audiobook.state import load_state
from audiobook.voice import validate_voice_reference


def load_config_default() -> AppConfig:
    """Return a default AppConfig without reading from disk."""
    return AppConfig()


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


@voice_app.command("preview")
def voice_preview(
    reference: Path | None = typer.Argument(None, exists=False, dir_okay=False),  # noqa: B008
    voice: str | None = typer.Option(None, "--voice", help="Saved voice name OR path"),
    text: str = typer.Option(
        "When we examine the architecture of a distributed system, three concerns "
        "dominate: consistency, availability, and partition tolerance.",
        "--text",
    ),
    out: Path = typer.Option(Path("./voice/preview.wav"), "--out"),  # noqa: B008
    config: Path = typer.Option(Path("./config.toml"), "--config"),  # noqa: B008
    exaggeration: float | None = typer.Option(
        None, "--exaggeration", help="Override [render].exaggeration for this preview."
    ),
    cfg_weight: float | None = typer.Option(
        None, "--cfg-weight", help="Override [render].cfg_weight for this preview."
    ),
    temperature: float | None = typer.Option(
        None, "--temperature", help="Override [render].temperature for this preview."
    ),
) -> None:
    """Render a preview using the supplied reference voice. HOST ONLY.

    The reference can come from (in order): positional REFERENCE path,
    --voice NAME-or-PATH, or the library default. Use `audiobook voice list`
    to see saved names. Pass --exaggeration / --cfg-weight / --temperature
    to override the [render] config for fast accent tuning.
    """
    import soundfile as sf  # type: ignore[import-untyped]

    from audiobook.render import _load_chatterbox
    from audiobook.voice_library import NoVoiceConfigured, resolve_voice_path

    cfg = load_config(config) if config.exists() else load_config_default()
    selector: str | None = voice or (str(reference) if reference else None)
    try:
        ref = resolve_voice_path(selector, cfg=cfg, project_root=Path.cwd())
    except NoVoiceConfigured as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None

    tts_kwargs = {
        "exaggeration": exaggeration if exaggeration is not None else cfg.render.exaggeration,
        "cfg_weight": cfg_weight if cfg_weight is not None else cfg.render.cfg_weight,
        "temperature": temperature if temperature is not None else cfg.render.temperature,
    }
    typer.echo(
        f"params: exaggeration={tts_kwargs['exaggeration']} "
        f"cfg_weight={tts_kwargs['cfg_weight']} temperature={tts_kwargs['temperature']}"
    )

    _, tts = _load_chatterbox("mps")
    samples, sr = tts(text, voice_conditioning=str(ref), **tts_kwargs)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), samples, sr, subtype="PCM_16")
    typer.echo(f"wrote {out}")


@voice_app.command("save")
def voice_save(
    sample: Path = typer.Argument(..., exists=True, dir_okay=False),  # noqa: B008
    name: str = typer.Option(..., "--name"),
    force: bool = typer.Option(False, "--force"),
    preview: bool = typer.Option(False, "--preview", help="Also generate voices/<name>.preview.wav"),
) -> None:
    """Convert a raw audio sample to 24 kHz mono PCM and save it as a named voice."""
    from audiobook.voice_library import save_voice

    project_root = Path.cwd()
    try:
        out, warnings = save_voice(sample, name=name, project_root=project_root, force=force)
    except FileExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"wrote {out}")
    for w in warnings:
        typer.echo(f"warn: {w}", err=True)

    if preview:
        # Reuse the existing voice preview implementation.
        from audiobook.render import _load_chatterbox  # type: ignore[no-untyped-call]
        import soundfile as sf  # type: ignore[import-untyped]

        preview_text = (
            "When we examine the architecture of a distributed system, three "
            "concerns dominate: consistency, availability, and partition tolerance."
        )
        _, tts = _load_chatterbox("mps")
        samples, sr = tts(preview_text, voice_conditioning=str(out))
        preview_path = out.with_suffix(".preview.wav")
        sf.write(str(preview_path), samples, sr, subtype="PCM_16")
        typer.echo(f"wrote {preview_path}")


@voice_app.command("list")
def voice_list(
    config: Path = typer.Option(Path("./config.toml"), "--config"),  # noqa: B008
) -> None:
    """List saved voices. The voice that would be picked by an unflagged run is marked with *."""
    from audiobook.voice_library import list_voices

    cfg = load_config(config) if config.exists() else load_config_default()
    items = list_voices(cfg=cfg, project_root=Path.cwd())
    if not items:
        typer.echo("(no saved voices — run `audiobook voice save SAMPLE --name NAME`)")
        return
    for v in items:
        prefix = "*" if v.is_active_default else " "
        size_kb = v.size_bytes // 1024
        typer.echo(
            f"{prefix} {v.name:20s} {v.duration_s:6.1f}s  {v.sample_rate:>6d} Hz  {size_kb:>5d} KB"
        )


@voice_app.command("rm")
def voice_rm(
    name: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", help="Skip confirmation prompt"),
) -> None:
    """Remove a saved voice from the library."""
    from audiobook.voice_library import rm_voice

    if not force:
        confirm = typer.confirm(f"delete voice '{name}'?")
        if not confirm:
            typer.echo("aborted")
            raise typer.Exit(1)
    try:
        rm_voice(name, project_root=Path.cwd())
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"removed voices/{name}.wav")


@app.callback()
def _root() -> None:
    """Root callback (placeholder; subcommands attached in later tasks)."""


@app.command("parse")
def parse(
    input_path: Path = typer.Argument(  # noqa: B008
        ..., exists=True, dir_okay=False, readable=True,
        help="Input book: .epub or .pdf",
    ),
    out: Path = typer.Option(Path("./work"), "--out", help="Output work directory."),  # noqa: B008
    config: Path = typer.Option(Path("./config.toml"), "--config"),  # noqa: B008
    parser: str | None = typer.Option(None, "--parser", help="PDF only: auto|pymupdf|marker."),
    footnote_policy: str | None = typer.Option(
        None, "--footnote-policy", help="PDF only: inline|endnote|skip."
    ),
    chapter_level: int | None = typer.Option(
        None, "--chapter-level", help="PDF only: heading level used as chapter boundaries (1-6)."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print per-section progress with completion %."
    ),
) -> None:
    """Stage 1 — parse an EPUB or PDF into per-chapter JSON + book_full_text.md."""
    suffix = input_path.suffix.lower()
    if suffix == ".epub":
        chapters = _parse_epub(
            input_path, out, progress=lambda line: typer.echo(line, err=True), verbose=verbose
        )
        if not chapters:
            typer.echo(
                "warning: 0 chapters extracted — check the input or [book].skip_sections.",
                err=True,
            )
        typer.echo(f"parsed {len(chapters)} chapters -> {out}")
        return
    if suffix == ".pdf":
        cfg = load_config(config) if config.exists() else load_config_default()
        p = parser or cfg.parse.parser
        fp = footnote_policy or cfg.parse.footnote_policy
        cl = chapter_level if chapter_level is not None else cfg.parse.chapter_level
        try:
            chapters = _parse_pdf(
                input_path,
                out,
                parser=p,  # type: ignore[arg-type]
                footnote_policy=fp,  # type: ignore[arg-type]
                chapter_level=cl,
                book_title=cfg.book.title or None,
                progress=lambda line: typer.echo(line, err=True),
                verbose=verbose,
            )
        except PdfParseError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
        if not chapters:
            typer.echo(
                "warning: 0 chapters extracted — check the input or [book].skip_sections.",
                err=True,
            )
        typer.echo(f"parsed {len(chapters)} chapters -> {out}")
        return
    typer.echo(
        f"error: unsupported input format {suffix!r}. Supported: .epub, .pdf", err=True
    )
    raise typer.Exit(2)


@app.command("adapt")
def adapt_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),  # noqa: B008
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print per-chapter progress with completion % and tokens."
    ),
) -> None:
    """Stage 2 — adapt chapters in-process (mode = "api"). Agent mode is
    driven by an external orchestrator and cannot be run via this command."""
    cfg = load_config(config)
    if cfg.adapt.mode == "agent":
        typer.echo(
            "agent mode is driven by an external orchestrator (Claude Code). "
            'Set [adapt].mode = "api" in config.toml to run unattended.',
            err=True,
        )
        raise typer.Exit(2)
    if cfg.adapt.mode != "api":
        typer.echo(f"unsupported adapt mode: {cfg.adapt.mode}", err=True)
        raise typer.Exit(2)

    # Lazy import so non-api flows don't require the openai SDK to be installed.
    from audiobook.adapt_api import run_adapt_api
    from audiobook.config import resolve_adapt_api

    api = resolve_adapt_api(cfg.adapt.api)
    if not api.model:
        typer.echo(
            "error: [adapt.api].model is empty. Set it in config.toml to the name "
            "of the model loaded in LM Studio (e.g. \"qwen2.5-14b-instruct\"), "
            "or override with the OPENAI_MODEL env var.",
            err=True,
        )
        raise typer.Exit(2)

    summary = run_adapt_api(
        work_dir, cfg=cfg, progress=lambda line: typer.echo(line), verbose=verbose
    )
    typer.echo(
        f"adapt complete: succeeded={len(summary.succeeded)} "
        f"retried={len(summary.retried)} failed={len(summary.failed)} "
        f"tokens_in={summary.total_input_tokens} tokens_out={summary.total_output_tokens} "
        f"book_context={'included' if summary.included_book_context else 'skipped'} "
        f"wall_s={summary.wall_seconds:.1f}"
    )
    if summary.failed:
        for stem, detail in summary.failed:
            typer.echo(f"FAILED {stem}: {detail}", err=True)
        raise typer.Exit(1)


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
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print per-chapter progress with completion %."
    ),
) -> None:
    """Stage 3 — chunk adapted chapters into TTS-sized pieces."""
    cfg = load_config(config)
    n = _chunk_dir(
        work_dir,
        max_chars=cfg.chunk.max_chars,
        paragraph_silence_ms=cfg.chunk.paragraph_silence_ms,
        section_silence_ms=cfg.chunk.section_silence_ms,
        progress=(lambda line: typer.echo(line, err=True)) if verbose else None,
        verbose=verbose,
    )
    typer.echo(f"chunked {n} chapters")


@app.command("render")
def render_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    voice: str | None = typer.Option(None, "--voice", help="Saved voice name OR path"),
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),  # noqa: B008
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print global per-chunk progress with completion %."
    ),
) -> None:
    """Stage 4 — render chunked text to WAVs. HOST ONLY (uses MPS).

    The voice can be a saved name (from `audiobook voice list`), an explicit
    path, or omitted to fall back to [render].voice in config, voices/default.wav,
    or legacy voice/reference.wav.
    """
    from audiobook.voice_library import NoVoiceConfigured, resolve_voice_path

    cfg = load_config(config)
    try:
        voice_path = resolve_voice_path(voice, cfg=cfg, project_root=Path.cwd())
    except NoVoiceConfigured as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None

    render_work_dir(
        work_dir,
        device=cfg.render.device,
        workers=cfg.render.workers,
        voice_path=voice_path,
        tts_kwargs={
            "exaggeration": cfg.render.exaggeration,
            "cfg_weight": cfg.render.cfg_weight,
            "temperature": cfg.render.temperature,
        },
        verbose=verbose,
    )
    typer.echo("render complete")


@app.command("validate-render")
def validate_render(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
) -> None:
    """Validate every chunk WAV under audio/chunks/. Emits JSON report on stdout."""
    report = _validate_render_dir(work_dir)
    typer.echo(report.to_json())
    sys.exit(0 if report.ok else 1)


@app.command("assemble")
def assemble_cmd(
    work_dir: Path = typer.Argument(..., exists=True, file_okay=False),  # noqa: B008
    title: str = typer.Option("", "--title", help="Overrides [book].title in config.toml"),
    author: str = typer.Option("", "--author", help="Overrides [book].author in config.toml"),
    narrator: str = typer.Option(
        "", "--narrator", help="Overrides [book].narrator in config.toml"
    ),
    out: Path = typer.Option(..., "--out"),  # noqa: B008
    cover: Path | None = typer.Option(None, "--cover", exists=True, dir_okay=False),  # noqa: B008
    config: Path = typer.Option(Path("./config.toml"), "--config", exists=True),  # noqa: B008
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print per-chapter assembly progress with completion %."
    ),
) -> None:
    """Stage 5 — assemble final .m4b with chapter markers and tags."""
    cfg = load_config(config)
    title = title or cfg.book.title
    author = author or cfg.book.author
    narrator = narrator or cfg.book.narrator
    if not title or not author:
        typer.echo(
            "error: title and author required. Set [book].title and [book].author in "
            "config.toml or pass --title/--author.",
            err=True,
        )
        raise typer.Exit(2)
    _assemble(
        work_dir,
        title=title,
        author=author,
        narrator=narrator,
        out_path=out,
        bitrate_kbps=cfg.assemble.audio_bitrate_kbps,
        cover_path=cover,
        progress=(lambda line: typer.echo(line, err=True)) if verbose else None,
        verbose=verbose,
    )
    typer.echo(f"wrote {out}")


@app.command("status")
def status(work_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:  # noqa: B008
    """Print a human-readable summary of work/state.json."""
    try:
        state = load_state(work_dir)
    except FileNotFoundError:
        typer.echo("no state.json yet — nothing has run")
        raise typer.Exit(0) from None
    typer.echo(state.model_dump_json(indent=2))


@app.command("lms-load")
def lms_load_cmd(
    config: Path = typer.Option(Path("./config.toml"), "--config"),  # noqa: B008
    context_length: int | None = typer.Option(
        None, "--context-length", help="Override [adapt.api].load_context_length."
    ),
    ttl: int | None = typer.Option(None, "--ttl", help="Override idle TTL seconds."),
) -> None:
    """Load the configured LM Studio model if not already loaded (host-only).

    No-op when the `lms` CLI is absent. Loading with a smaller --context-length
    sharply reduces KV-cache RAM.
    """
    from audiobook.config import resolve_adapt_api
    from audiobook.lmstudio_ctl import ensure_loaded

    cfg = load_config(config) if config.exists() else load_config_default()
    api = resolve_adapt_api(cfg.adapt.api)
    ctx = context_length if context_length is not None else cfg.adapt.api.load_context_length
    t = ttl if ttl is not None else api.ttl_seconds
    status = ensure_loaded(api.model, context_length=ctx, ttl=t)
    typer.echo(f"lms-load ({api.model or 'no model'}): {status}")


@app.command("lms-unload")
def lms_unload_cmd() -> None:
    """Unload all LM Studio models to free RAM (host-only; no-op without `lms`)."""
    from audiobook.lmstudio_ctl import unload_all

    typer.echo(f"lms-unload: {unload_all()}")


if __name__ == "__main__":
    app()
