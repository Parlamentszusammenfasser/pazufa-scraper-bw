"""Integration tests for issue #30, using the real source documents.

Issue #30 (https://codeberg.org/PaZuFa/pazufa-scraper-bw/issues/30) reported two
semantic defects in the summaries of Vorgang V-215974 on staging:

1. ``Rich terinnen`` — an inner-word break in the summary. Root cause: the
   two-column PARLIS PDF of Drucksache 17/1000 (Staatshaushaltsgesetz 2022)
   hyphenates ``Richterinnen`` at a line end (``Richte-rinnen``); kreuzberg
   keeps the inline hyphen, so the broken word reached the LLM and was echoed
   into the summary. This is a *deterministic* text-normalization defect, fixed
   by the issue-20 hyphen-rejoin pass. The test below downloads the real PDF and
   verifies the current pipeline produces a clean ``Richterinnen`` — no LLM
   required, so it runs on network access alone.

2. ``Dreiklang aus Nachhaltigkeit, Ökologie, Ökonomie und Soziales`` — four items
   where the source (Plenarprotokoll 17/25, printed page 1334) lists only three:
   the speaker says *"Der erste Dreiklang lautet Nachhaltigkeit - Ökologie,
   Wirtschaft, Soziales"* (dash-separated heading) and *"Dieser Dreiklang aus
   Ökologie, Ökonomie und Soziales"*. ``Nachhaltigkeit`` is the heading before
   the dash, not a triad member. This is an
   *LLM comprehension* error, not an extraction error — the source text is clean
   (see :func:`test_dreiklang_source_text_is_clean`, which needs no LLM). A real
   LLM would be required to reproduce the faulty summary, and the outcome is
   non-deterministic; that check lives separately and is intentionally not a hard
   assertion here.

Run with: pytest -m integration tests/integration/test_issue30_real_documents.py
"""

import aiohttp
import pytest

from bawue.bawue_dok import extract_pdf_text, normalize_volltext

pytestmark = pytest.mark.integration

# Drucksache 17/1000 — Staatshaushaltsgesetz 2022, the document whose summary
# contained "Rich terinnen". Canonical files/live URL (redirects to the blob).
DS_17_1000_URL = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/1000/17_1000_D.pdf"

# Plenarprotokoll 17/25 (22.12.2021), 2nd reading of the Haushalt 2022. Printed
# page 1334 carries the "Dreiklang" passage.
PP_17_25_URL = "https://www.landtag-bw.de/resource/blob/255474/2d80f4ffbeffdeab3654eac3927df0a0/17_0025_22122021.pdf"

# Broken forms that must NOT survive normalization (from the kreuzberg extraction
# of DS 17/1000). The trailing hyphen makes each unambiguous — a genuine compound
# like "Landesrichter- und" keeps its hyphen and is not in this list.
_BROKEN_TOKENS = ["Richte-rinnen", "Rich terinnen", "Beam-ten", "Ein-gangsamt"]


async def _extract(session: aiohttp.ClientSession, url: str) -> str:
    """Download a PDF and return its extracted (un-normalized) text."""
    from bawue.bawue_dok import download_pdf

    path = await download_pdf(session, url)
    try:
        text, _hash = await extract_pdf_text(path)
        return text
    finally:
        path.unlink(missing_ok=True)


class TestRichterinnenHyphenation:
    """Problem 1: the deterministic extraction/normalization defect."""

    @pytest.mark.asyncio
    async def test_richterinnen_is_not_broken_after_normalization(self):
        async with aiohttp.ClientSession() as session:
            raw = await _extract(session, DS_17_1000_URL)

        # Precondition: the raw extraction really does contain the hyphenated
        # break — otherwise this document no longer reproduces the bug and the
        # test would pass vacuously.
        assert "Richte-rinnen" in raw, "source PDF no longer exhibits the hyphenation defect"

        clean = normalize_volltext(raw)
        assert "Richterinnen" in clean, "expected the rejoined word to appear"
        for token in _BROKEN_TOKENS:
            assert token not in clean, f"broken token survived normalization: {token!r}"


class TestDreiklangSource:
    """Problem 2: prove the *source* is clean, so the four-item summary is an LLM
    error rather than an extraction artefact. Needs no LLM."""

    @pytest.mark.asyncio
    async def test_dreiklang_source_text_is_clean(self):
        async with aiohttp.ClientSession() as session:
            raw = await _extract(session, PP_17_25_URL)
        clean = normalize_volltext(raw)

        # The triad is stated with exactly three members; "Nachhaltigkeit" is the
        # heading before the dash, never part of the "Dreiklang aus …" list.
        assert "Dreiklang aus Ökologie, Ökonomie und Soziales" in clean
        assert "Dreiklang aus Nachhaltigkeit" not in clean
