from __future__ import annotations

from pathlib import Path

import pytest

from audiobook.parse_pdf import (
    EncryptedPdfError,
    ScannedPdfError,
    parse_pdf,
)


def test_parse_pdf_rejects_encrypted(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "encrypted.pdf"
    with pytest.raises(EncryptedPdfError):
        parse_pdf(src, scratch)


def test_parse_pdf_rejects_scanned(repo_root: Path, scratch: Path) -> None:
    src = repo_root / "tests" / "fixtures" / "scanned.pdf"
    with pytest.raises(ScannedPdfError) as exc:
        parse_pdf(src, scratch)
    assert "scanned" in str(exc.value).lower()
    assert "ocr" in str(exc.value).lower()
