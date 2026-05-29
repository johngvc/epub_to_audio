"""Generate small PDF fixtures for Stage-1 PDF parsing tests.

Run inside the container:
    docker compose run --rm audiobook python tests/fixtures/build_tiny_pdf.py
Produces tiny.pdf (2 heading-delimited chapters with a hyphen line-break),
scanned.pdf (image-only, no text layer), and encrypted.pdf (password-locked).
"""
from __future__ import annotations

from pathlib import Path

import fitz  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent


def _write_heading(page: "fitz.Page", y: float, text: str) -> None:
    page.insert_text((72, y), text, fontsize=22, fontname="helv")


def _write_body(page: "fitz.Page", y: float, text: str) -> None:
    page.insert_text((72, y), text, fontsize=11, fontname="helv")


def build_tiny() -> None:
    doc = fitz.open()
    p1 = doc.new_page()
    _write_heading(p1, 80, "Chapter One")
    # A hyphenated line-break split that de-hyphenation should re-join.
    _write_body(p1, 120, "The information re-")
    _write_body(p1, 135, "trieval system worked well across the morning hours.")
    p2 = doc.new_page()
    _write_heading(p2, 80, "Chapter Two")
    _write_body(p2, 120, "On the second day the visitor returned with a wooden box.")
    doc.save(str(HERE / "tiny.pdf"))
    doc.close()


def build_scanned() -> None:
    # A page with no extractable text layer (simulates a scanned page).
    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 300, 200)
    page.draw_rect(rect, color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    doc.save(str(HERE / "scanned.pdf"))
    doc.close()


def build_encrypted() -> None:
    doc = fitz.open()
    page = doc.new_page()
    _write_body(page, 100, "Secret contents that require a password.")
    doc.save(
        str(HERE / "encrypted.pdf"),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()


if __name__ == "__main__":
    build_tiny()
    build_scanned()
    build_encrypted()
    print("wrote tiny.pdf, scanned.pdf, encrypted.pdf")
