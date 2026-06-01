# TTS engine adapter (Chatterbox | Kokoro) — design

Date: 2026-06-01

## Goal

Let the user choose the Stage-4 TTS engine — Chatterbox (voice-cloning from a
reference WAV) or Kokoro (fixed built-in voices) — without disturbing the rest
of the pipeline. Deliver a Chapter-1 render with Kokoro (`bm_george`) for review.

## The seam

`render_chapter_chunks` already drives a generic
`TTSCallable(text, *, voice_conditioning, **kwargs) -> (np.float32 mono, sr)`.
Only *which* callable backs it changes per engine; `render_chapter_chunks` and
`compress_silence` are untouched.

## Components

New `audiobook/tts.py` (owns the engine layer; no dependency on render.py):

- `TTSCallable` type (moved from render.py).
- `_load_chatterbox(device) -> (model, TTSCallable)` (moved verbatim; cli.py
  voice preview/save import it from here now).
- `_kokoro_lang_for_voice(voice) -> str` — Kokoro voice names are prefixed by
  language (`a`=American, `b`=British…); returns `voice[0]` if alphabetic else `a`.
- `_load_kokoro(device, *, voice) -> TTSCallable` — lazy `from kokoro import
  KPipeline`; build `KPipeline(lang_code=_kokoro_lang_for_voice(voice), device=device)`
  (fall back without `device=` on TypeError). The callable runs
  `pipeline(text, voice=voice_conditioning or voice, speed=...)`, concatenates the
  per-segment audio it yields into one float32 array, returns `(samples, 24000)`.
  Missing dep → `RuntimeError` pointing at the `[kokoro]` extra.
- `load_engine(engine, device, *, voice=None) -> TTSCallable` — dispatch;
  unknown engine raises `ValueError`.

`render.py`:
- import `TTSCallable, load_engine` from `tts.py`; drop the local definitions.
- `render_work_dir(..., engine="chatterbox", voice_conditioning, ...)` (replaces
  `voice_path: Path`): `tts_callable = load_engine(engine, device, voice=voice_conditioning)`
  and pass `voice_conditioning` straight through.

`cli.py`:
- `render` gains `--engine chatterbox|kokoro` (defaults to `[render].engine`).
- Branch: **chatterbox** → `voice_conditioning = str(resolve_voice_path(...))`,
  `tts_kwargs = {exaggeration, cfg_weight, temperature}`. **kokoro** →
  `voice_conditioning = voice or cfg.render.kokoro_voice`,
  `tts_kwargs = {"speed": cfg.render.kokoro_speed}`; the WAV voice library is
  skipped (no `resolve_voice_path`).
- voice preview/save import `_load_chatterbox` from `audiobook.tts`.

## Config (`[render]`)

- `engine: Literal["chatterbox","kokoro"] = "chatterbox"` (committed default unchanged).
- `kokoro_voice: str = "af_heart"`, `kokoro_speed: float = 1.0`.

## Dependencies

New optional extra `kokoro = ["kokoro", "misaki[en]"]`, installed into the host
venv. First `KPipeline` init auto-downloads the 82M model from HuggingFace. If
misaki's G2P needs `espeak-ng`, `brew install espeak-ng` and note it in the README.

## Testing

Docker test env has neither torch nor kokoro, so engine *internals* aren't
unit-tested (same as Chatterbox today). Tested:
- `load_engine` dispatch (monkeypatched loaders) + unknown-engine `ValueError`.
- `_kokoro_lang_for_voice` derivation.
- CLI: `--engine kokoro --voice bm_george` passes `voice_conditioning="bm_george"`
  + `tts_kwargs={"speed":…}`; `--engine chatterbox` still resolves a WAV path.

The Kokoro audio itself is verified by the real Chapter-1 render.

## Out of scope

`run`-orchestrator engine switching (engine read from config there already);
Kokoro voice-blending; non-English languages beyond what the voice prefix implies.
