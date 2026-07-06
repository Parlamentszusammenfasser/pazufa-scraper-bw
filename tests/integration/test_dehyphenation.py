"""Integration test for line-break hyphenation removal (issue #20).

Runs the *real* kreuzberg extraction over a real Landtag Drucksache and
asserts the normalization pipeline reassembles hyphenated words while leaving
genuine compound hyphens intact.

Test data: Drucksache 17/273 (17_0273_D.pdf), bundled under tests/fixtures/pdf/.
Source: https://www.landtag-bw.de/resource/blob/253220/ea06a2c6d62e721e724403c88ea2b578/17_0273_D.pdf

Run with: pytest -m integration tests/integration/test_dehyphenation.py
"""

from pathlib import Path

import pytest

from bawue.bawue_dok import extract_pdf_text, normalize_volltext

pytestmark = pytest.mark.integration

PDF_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "pdf" / "17_0273_D.pdf"


@pytest.mark.asyncio
async def test_real_drucksache_dehyphenation():
    """abzu-senken → abzusenken; Baden-Württemberg preserved (issue #20)."""
    assert PDF_PATH.exists(), f"missing test fixture: {PDF_PATH}"

    raw_text, _ = await extract_pdf_text(PDF_PATH)
    # Sanity: reproduce the defect in the raw extraction.
    assert "abzu-senken" in raw_text

    text = normalize_volltext(raw_text)

    # The reported word is correctly reassembled ...
    assert "abzusenken" in text
    assert "abzu-senken" not in text
    # ... and other real hyphenation artefacts from this document are joined.
    for joined in ("Lebensjahr", "Wahltag", "Gesellschaft", "Jugendverbänden"):
        assert joined in text

    # Genuine compound hyphens must remain untouched.
    assert "Baden-Württemberg" in text
    assert "BadenWürttemberg" not in text
