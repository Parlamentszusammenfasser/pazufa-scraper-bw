"""Integration test for GitHub issue #20, using the real source document.

Issue #20 (https://github.com/Parlamentszusammenfasser/pazufa-scraper-bw/issues/20)
reported that ``is_garbled()`` only recognises DD-015's "Muster 1"
(Latin-Extended substitution) and misses "Muster 2" — the constant ASCII shift
that also emits C1 control characters. Drucksache 17/1201 (Änderungsanträge zum
Staatshaushaltsplan 2022) is affected by Muster 2 only:

* page 1 (the Landtag cover sheet) extracts cleanly,
* pages 2-5 carry the actual Änderungsanträge, but every glyph comes out shifted
  (``6HLWH`` → ``Seite``, ``bQGHUXQJVDQWUDJ`` → ``Änderungsantrag``).

Because the shifted text contains no Latin-Extended characters, ``is_garbled()``
returned ``False``, the OCR retry never ran, and ``normalize_volltext()`` then
stripped the garbled paragraphs — leaving the Station with (near-)empty volltext
instead of the content OCR can recover.

Runs the real pipeline against the real PDF: network + Tesseract (``deu``)
required, no LLM.

Run with: pytest -m integration tests/integration/test_gh20_c1_garbled_ocr.py
"""

import aiohttp
import pytest

from bawue.pdf_text import download_pdf, normalize_volltext, parse_pdf

pytestmark = pytest.mark.integration

# Drucksache 17/1201 — the Sammeldrucksache from the issue's staging log. Each
# Änderungsantrag sits on its own page and is linked with a #page=N fragment.
DS_17_1201_URL = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/1000/17_1201_D.pdf"


async def _volltext(page_hint: int | None) -> str:
    """Download DS 17/1201 and run the production extraction + normalization."""
    async with aiohttp.ClientSession() as session:
        path = await download_pdf(session, DS_17_1201_URL)
    try:
        return normalize_volltext((await parse_pdf(path, page_hint)).text())
    finally:
        path.unlink(missing_ok=True)


class TestAsciiShiftGarblingIsRecovered:
    @pytest.mark.asyncio
    async def test_page_2_volltext_is_recovered_not_stripped(self):
        """The page-2 Änderungsantrag must survive normalization as real German text.

        Before the fix this collapsed to a handful of characters ("6\\n\\n6"),
        which the caller then reports as "PDF text extraction yielded empty
        content" and degrades to a metadata-only Dokument.
        """
        volltext = await _volltext(2)

        assert len(volltext) > 500, f"volltext collapsed to {len(volltext)} chars: {volltext!r}"
        assert "Änderungsantrag" in volltext
        assert "Beihilfen" in volltext
        # No leftover of the shifted encoding.
        assert "bQGHUXQJVDQWUDJ" not in volltext
        assert not any(0x80 <= ord(c) <= 0x9F for c in volltext)

    @pytest.mark.asyncio
    async def test_page_hint_window_still_selects_one_antrag(self):
        """An OCRed page still windows the document: every Änderungsantrag of this
        Sammeldrucksache gets its own volltext."""
        full = await _volltext(None)
        page_4 = await _volltext(4)

        assert len(page_4) < len(full), "page hint did not narrow the document"
        # Page 4 holds the AfD Änderungsantrag on the Landeszentrale für politische
        # Bildung; "Beihilfen aufgrund der Beihilfeverordnung" appears on page 2 only.
        assert "Landeszentrale" in page_4
        assert "Beihilfen" not in page_4
