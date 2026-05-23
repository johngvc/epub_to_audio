"""Stage 3 — sentence segmentation, pronunciation pass, greedy packing."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pysbd  # type: ignore[import-untyped]

from audiobook.models import ChapterAdapted, ChapterChunks, Chunk, PronunciationHint

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)


def apply_pronunciation(text: str, hints: list[PronunciationHint]) -> str:
    """Whole-word find-replace. Case-sensitive for terms that are all-uppercase
    (acronyms) or mixed-case (CLI tool names); case-insensitive otherwise."""
    result = text
    for h in hints:
        case_sensitive = h.term != h.term.lower()
        pattern = r"\b" + re.escape(h.term) + r"\b"
        flags = 0 if case_sensitive else re.IGNORECASE
        result = re.sub(pattern, h.spoken_as, result, flags=flags)
    return result


def pack_sentences(sentences: list[str], max_chars: int, min_orphan_chars: int) -> list[str]:
    """Greedy-pack sentences into chunks ≤ max_chars without splitting sentences.

    Sentences shorter than ``min_orphan_chars`` are merged with the next chunk
    so a tiny "X." doesn't end up alone (Chatterbox handles short utterances
    poorly).
    """
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

    for sent in sentences:
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


def chunk_chapter(
    *,
    index: int,
    title: str,
    adapted: ChapterAdapted,
    pronunciation: list[PronunciationHint],
    max_chars: int,
    paragraph_silence_ms: int,
    section_silence_ms: int,
) -> ChapterChunks:
    text = apply_pronunciation(adapted.adapted_text, pronunciation + adapted.pronunciation_hints)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    all_chunks: list[Chunk] = []
    chunk_id = 0
    for p_i, paragraph in enumerate(paragraphs):
        section_break = paragraph.strip() == "---"
        if section_break:
            if all_chunks:
                all_chunks[-1] = Chunk(
                    id=all_chunks[-1].id,
                    text=all_chunks[-1].text,
                    trailing_silence_ms=section_silence_ms,
                )
            continue
        sentences = _SEGMENTER.segment(paragraph)
        packed = pack_sentences(list(sentences), max_chars=max_chars, min_orphan_chars=20)
        for i, ptext in enumerate(packed):
            is_last_in_paragraph = i == len(packed) - 1
            trailing = (
                paragraph_silence_ms if is_last_in_paragraph and p_i < len(paragraphs) - 1 else 0
            )
            all_chunks.append(Chunk(id=f"{chunk_id:04d}", text=ptext, trailing_silence_ms=trailing))
            chunk_id += 1

    return ChapterChunks(index=index, title=title, chunks=all_chunks)


def chunk_work_dir(
    work_dir: Path,
    *,
    max_chars: int,
    paragraph_silence_ms: int,
    section_silence_ms: int,
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
    for adapted_path in sorted(adapted_dir.glob("*.json")):
        out_path = chunks_dir / adapted_path.name
        if out_path.exists():
            continue
        adapted = ChapterAdapted.model_validate_json(adapted_path.read_text())
        raw_path = raw_dir / adapted_path.name
        if not raw_path.exists():
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
        )
        out_path.write_text(chunks.model_dump_json(indent=2) + "\n")
        count += 1
    return count
