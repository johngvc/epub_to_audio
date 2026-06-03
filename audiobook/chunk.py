"""Stage 3 — sentence segmentation, pronunciation pass, greedy packing."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pysbd  # type: ignore[import-untyped]

from audiobook.models import ChapterAdapted, ChapterChunks, Chunk, PronunciationHint
from audiobook.utils.progress import pct_line

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)

# Literal control token the adapter may emit inside `adapted_text` to mark a
# dramatic pause. Stripped from spoken text; mapped to `beat_silence_ms`.
BEAT_TOKEN = "[[beat]]"


def sanitize_spoken_as(value: str) -> str:
    """Normalize a pronunciation hint's `spoken_as` so the TTS engine doesn't
    read literal dashes or dictionary-style stress marks.

    Even with explicit prompting, every local model we tested produced
    stress-mark patterns like `MEER-ah` or `BYAR-neh stroo-stroop` for hard
    foreign words. The TTS reads dashes literally and may over-emphasize
    ALL-CAPS runs, so we normalize:

    - `-` and `_` → space (TTS reads them otherwise)
    - lowercase the whole value when it's mixed-case (the stress-mark pattern),
      preserving pure ALL-CAPS like ``MIT`` and pure lowercase like
      ``cube control`` unchanged
    - collapse runs of whitespace

    Examples:
        ``MEER-ah``             → ``meer ah``
        ``BYAR-neh stroo-stroop`` → ``byar neh stroo stroop``
        ``MIT``                 → ``MIT``
        ``cube control``        → ``cube control``
    """
    has_upper = any(c.isupper() for c in value)
    has_lower = any(c.islower() for c in value)
    if has_upper and has_lower:
        value = value.lower()
    value = re.sub(r"[-_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _is_redundant_spelling(term: str, cleaned: str) -> bool:
    """True when ``cleaned`` just spells ``term`` out as space-separated single
    letters (e.g. term "AI", cleaned "A I").

    Such hints add nothing a TTS engine doesn't already do for an ALL-CAPS
    acronym, but the spaced single letters make Chatterbox read them with a
    pause (or even hallucinate a multi-second gap), so we skip them and leave
    the acronym intact — the same way pure ALL-CAPS like "MIT" is preserved.
    """
    tokens = cleaned.split()
    if len(tokens) < 2 or not all(len(t) == 1 and t.isalpha() for t in tokens):
        return False
    term_letters = re.sub(r"[^A-Za-z]", "", term).lower()
    return "".join(tokens).lower() == term_letters


def apply_pronunciation(text: str, hints: list[PronunciationHint]) -> str:
    """Whole-word find-replace. Case-sensitive for terms that are all-uppercase
    (acronyms) or mixed-case (CLI tool names); case-insensitive otherwise.

    `spoken_as` is sanitized via :func:`sanitize_spoken_as` before substitution
    so TTS-hostile artifacts (dashes, stress marks) never reach Chatterbox.
    Hints that merely re-spell an acronym as spaced letters are skipped (see
    :func:`_is_redundant_spelling`).
    """
    result = text
    for h in hints:
        cleaned = sanitize_spoken_as(h.spoken_as)
        if _is_redundant_spelling(h.term, cleaned):
            continue
        case_sensitive = h.term != h.term.lower()
        pattern = r"\b" + re.escape(h.term) + r"\b"
        flags = 0 if case_sensitive else re.IGNORECASE
        result = re.sub(pattern, cleaned, result, flags=flags)
    return result


def split_long_sentence(sent: str, max_chars: int) -> list[str]:
    """Split a sentence longer than ``max_chars`` at clause boundaries.

    Tries comma/semicolon/colon first (keeps natural pause points), then falls
    back to splitting at the last whitespace before the boundary. Pronunciation
    expansion (e.g. "DDD" → "D D D") can push an otherwise-fine sentence over
    the limit, so we cannot assume the segmenter already gave us ≤max_chars
    pieces.
    """
    sent = sent.strip()
    if len(sent) <= max_chars:
        return [sent]
    parts = re.split(r"(?<=[,;:])\s+", sent)
    packed: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in parts:
        if buf_len + len(p) + (1 if buf else 0) > max_chars and buf:
            packed.append(" ".join(buf))
            buf, buf_len = [p], len(p)
        else:
            buf.append(p)
            buf_len += len(p) + (1 if len(buf) > 1 else 0)
    if buf:
        packed.append(" ".join(buf))
    # Anything still oversize (no usable clause separator) → split at whitespace.
    final: list[str] = []
    for piece in packed:
        while len(piece) > max_chars:
            cut = piece.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            final.append(piece[:cut].rstrip())
            piece = piece[cut:].lstrip()
        if piece:
            final.append(piece)
    return final


def pack_sentences(sentences: list[str], max_chars: int, min_orphan_chars: int) -> list[str]:
    """Greedy-pack sentences into chunks ≤ max_chars.

    Sentences shorter than ``min_orphan_chars`` are merged with the next chunk
    so a tiny "X." doesn't end up alone (Chatterbox handles short utterances
    poorly). Sentences longer than ``max_chars`` are split at clause boundaries
    via :func:`split_long_sentence` before packing.
    """
    expanded: list[str] = []
    for sent in sentences:
        expanded.extend(split_long_sentence(sent, max_chars))
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    pending_short: str | None = None

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append(" ".join(buf).strip())
            buf = []
            buf_len = 0

    for sent in expanded:
        s = sent.strip()
        if not s:
            continue
        if pending_short is not None:
            s = pending_short + " " + s
            pending_short = None
        if len(s) < min_orphan_chars:
            pending_short = s
            continue
        if buf_len + len(s) + (1 if buf else 0) > max_chars and buf:
            flush()
        buf.append(s)
        buf_len += len(s) + (1 if len(buf) > 1 else 0)

    if pending_short is not None:
        if chunks and len(chunks[-1]) + 1 + len(pending_short) <= max_chars:
            chunks[-1] = chunks[-1] + " " + pending_short
        elif buf:
            buf.append(pending_short)
        else:
            chunks.append(pending_short)
        pending_short = None
    flush()
    return chunks


def _sentence_units(
    sentences: list[str], max_chars: int, min_orphan_chars: int
) -> list[list[str]]:
    """Turn a segment's sentences into per-sentence units.

    Each unit is a list of sub-pieces: a normal sentence yields ``[sentence]``;
    a sentence longer than ``max_chars`` yields several length-split pieces. Tiny
    sentences (< ``min_orphan_chars``) merge forward into the next sentence (or
    backward into the previous one if they are last) so we never emit a lone
    fragment — the same orphan rule the old packer used, minus cross-sentence
    packing.
    """
    merged: list[str] = []
    pending: str | None = None
    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        if pending is not None:
            s = pending + " " + s
            pending = None
        if len(s) < min_orphan_chars:
            pending = s
            continue
        merged.append(s)
    if pending is not None:
        if merged:
            merged[-1] = merged[-1] + " " + pending
        else:
            merged.append(pending)
    return [split_long_sentence(s, max_chars) for s in merged]


def chunk_chapter(
    *,
    index: int,
    title: str,
    adapted: ChapterAdapted,
    pronunciation: list[PronunciationHint],
    max_chars: int,
    paragraph_silence_ms: int,
    section_silence_ms: int,
    sentence_silence_ms: int = 180,
    beat_silence_ms: int = 600,
) -> ChapterChunks:
    text = apply_pronunciation(adapted.adapted_text, pronunciation + adapted.pronunciation_hints)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    all_chunks: list[Chunk] = []
    chunk_id = 0
    for p_i, paragraph in enumerate(paragraphs):
        if paragraph.strip() == "---":
            if all_chunks:
                last = all_chunks[-1]
                all_chunks[-1] = Chunk(
                    id=last.id,
                    text=last.text,
                    trailing_silence_ms=max(last.trailing_silence_ms, section_silence_ms),
                )
            continue

        is_last_paragraph = p_i == len(paragraphs) - 1

        # Split on beat sentinels first so pysbd never sees the token. A beat
        # follows every segment except the last; it attaches to the unit that
        # precedes it (empty segments collapse, so doubled/edge beats are safe).
        units: list[list] = []  # each: [pieces: list[str], beat_after: bool]
        segments = paragraph.split(BEAT_TOKEN)
        for s_idx, seg in enumerate(segments):
            seg = seg.strip()
            beat_here = s_idx < len(segments) - 1
            if seg:
                sentences = list(_SEGMENTER.segment(seg))
                for pieces in _sentence_units(sentences, max_chars, min_orphan_chars=20):
                    units.append([pieces, False])
            if beat_here and units:
                units[-1][1] = True

        for u_idx, (pieces, beat_after) in enumerate(units):
            is_last_unit = u_idx == len(units) - 1
            for pc_idx, piece in enumerate(pieces):
                is_last_piece = pc_idx == len(pieces) - 1
                if not is_last_piece:
                    trailing = 0  # forced mid-sentence split — no audible pause
                elif is_last_unit:
                    trailing = 0 if is_last_paragraph else paragraph_silence_ms
                else:
                    trailing = sentence_silence_ms
                if is_last_piece and beat_after:
                    trailing = max(trailing, beat_silence_ms)
                all_chunks.append(
                    Chunk(id=f"{chunk_id:04d}", text=piece, trailing_silence_ms=trailing)
                )
                chunk_id += 1

    return ChapterChunks(index=index, title=title, chunks=all_chunks)


def chunk_work_dir(
    work_dir: Path,
    *,
    max_chars: int,
    paragraph_silence_ms: int,
    section_silence_ms: int,
    sentence_silence_ms: int = 180,
    beat_silence_ms: int = 600,
    progress: Callable[[str], None] | None = None,
    verbose: bool = False,
) -> int:
    """Chunk every adapted file in work_dir. Skips chapters whose chunks already exist."""
    work_dir = Path(work_dir)
    adapted_dir = work_dir / "chapters" / "adapted"
    chunks_dir = work_dir / "chapters" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = work_dir / "chapters" / "raw"

    pron_path = work_dir / "pronunciation.json"
    pron: list[PronunciationHint] = []
    if pron_path.exists():
        for item in json.loads(pron_path.read_text()):
            pron.append(PronunciationHint(**item))

    count = 0
    adapted_paths = sorted(adapted_dir.glob("*.json"))
    total = len(adapted_paths)
    for pos, adapted_path in enumerate(adapted_paths, 1):
        out_path = chunks_dir / adapted_path.name
        if out_path.exists():
            if verbose and progress:
                progress(pct_line("chunk", pos, total, f"{adapted_path.stem} (skipped)"))
            continue
        adapted = ChapterAdapted.model_validate_json(adapted_path.read_text())
        raw_path = raw_dir / adapted_path.name
        if not raw_path.exists():
            if verbose and progress:
                progress(pct_line("chunk", pos, total, f"{adapted_path.stem} (no raw; skipped)"))
            continue
        raw_data = json.loads(raw_path.read_text())
        chunks = chunk_chapter(
            index=raw_data["index"],
            title=raw_data["title"],
            adapted=adapted,
            pronunciation=pron,
            max_chars=max_chars,
            paragraph_silence_ms=paragraph_silence_ms,
            section_silence_ms=section_silence_ms,
            sentence_silence_ms=sentence_silence_ms,
            beat_silence_ms=beat_silence_ms,
        )
        out_path.write_text(chunks.model_dump_json(indent=2) + "\n")
        count += 1
        if verbose and progress:
            detail = f"{raw_data['title']} -> {len(chunks.chunks)} chunks"
            progress(pct_line("chunk", pos, total, detail))
    return count
