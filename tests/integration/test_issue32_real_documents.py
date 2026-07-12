"""Integration tests for issue #32, using the real source documents.

Issue #32 (https://codeberg.org/PaZuFa/pazufa-scraper-bw/issues/32) reported that
the Redeprotokoll summaries of a fishing-law bill (V-212391, *Gesetz zur Änderung
des Fischereigesetzes für Baden-Württemberg*, Drucksache 17/529) described a
completely different topic — an open-data/transparency bill — because two root
causes combined:

1. **Truncation.** ``enrich_dokument`` sent only the first 12 000 tokens of the
   30-page protocol window to the LLM. In Plenarprotokoll 17/12 (29.9.2021) the
   window opens on agenda item 3 (open data, Drucksache 17/513) and only reaches
   the fishing debate — agenda item 4, Drucksache 17/529 — around token ~8 700,
   running past token 17 000. Truncating at 12 000 tokens therefore preserved the
   *complete* open-data debate and cut the fishing debate down to roughly a third,
   so the model summarized the wrong bill. Truncation has been removed entirely.

2. **Missing document identity.** The prompt named neither the bill nor its
   Drucksache, so even with the full text the model had no anchor telling it which
   of several debated bills to summarize. ``enrich_dokument`` now prepends a
   context header (Vorgang title + Drucksache).

``test_full_protocol_window_retains_fishing_debate`` proves both the defect and the
fix deterministically from the real PDF and needs no LLM: it shows the window
exceeds the old 12 000-token cut, that the historical truncation would have gutted
the fishing debate while keeping the open-data one, and that the full text now sent
to the LLM contains the complete fishing debate.

``TestEnrichedSummaryIsNeutralAcrossBills`` is the end-to-end guarantee and needs a
real LLM (it is skipped without ``LLM_PROVIDER_KEY``). It no longer asserts the
summary is exclusively about the fishing bill — that per-bill anchoring was reverted
by issue #49 (https://codeberg.org/PaZuFa/pazufa-scraper-bw/issues/49): the backend
stores one Dokument row per PDF hash, shared by every bill debated in one sitting, so
a summary narrowed to a single bill cannot survive more than one bill's upload intact.
Instead it proves the actual issue #49 fix: two different Vorgänge (different bill
title/Drucksache) sharing this same real protocol PDF get back an *identical*
zusammenfassung/schlagworte/kurztitel, because the cache key no longer varies by
bill identity for REDEPROTOKOLL.

Run with: pytest -m integration tests/integration/test_issue32_real_documents.py
"""

import os
from datetime import UTC, datetime

import aiohttp
import litellm
import pytest

from bawue.bawue_dok import _hash_cache, download_pdf, extract_pdf_text, normalize_volltext
from bawue.types import Doktyp, Dokument

pytestmark = pytest.mark.integration

# Plenarprotokoll 17/12 (29.9.2021). Agenda item 4 (Drucksache 17/529) is the
# fishing-law first reading; the URL fragment #page=30 is the scraper's page hint.
PP_17_12_URL = "https://www.landtag-bw.de/resource/blob/254492/08f94bbbdcbea5a7a2d1a21de354c0a4/17_0012_29092021.pdf"
PP_17_12_PAGE_HINT = 30

# The token budget the removed truncation used (DD-013). The regression this test
# guards is precisely that the relevant debate lived beyond this cut.
_OLD_TRUNCATE_TOKENS = 12000
_MODEL = "gpt-5-nano"

# Bill identity, exactly as printed in the protocol: agenda item 4, "Gesetz zur
# Änderung des Fischereigesetzes für Baden-Württemberg", Drucksache 17/529.
FISHING_TITEL = "Gesetz zur Änderung des Fischereigesetzes für Baden-Württemberg"
FISHING_DRUCKSNR = "17/529"


def _old_truncation(text: str) -> str:
    """Reconstruct what the removed ``truncate_text`` produced at 12 000 tokens.

    Kept local (not imported) so the test documents the historical behaviour
    without resurrecting the deleted feature.
    """
    tokens = litellm.encode(model=_MODEL, text=text)
    if len(tokens) <= _OLD_TRUNCATE_TOKENS:
        return text
    clipped = litellm.decode(model=_MODEL, tokens=tokens[:_OLD_TRUNCATE_TOKENS])
    return clipped.encode("utf-8", errors="ignore").decode("utf-8")


async def _extract_window(url: str, page_hint: int) -> str:
    async with aiohttp.ClientSession() as session:
        path = await download_pdf(session, url)
        try:
            text, _hash = await extract_pdf_text(path, page_hint=page_hint)
        finally:
            path.unlink(missing_ok=True)
    return normalize_volltext(text)


class TestFishingDebateSurvivesTruncationRemoval:
    """Deterministic proof of root cause #1 — no LLM required."""

    @pytest.mark.asyncio
    async def test_full_protocol_window_retains_fishing_debate(self):
        window = await _extract_window(PP_17_12_URL, PP_17_12_PAGE_HINT)

        # Precondition: the real window really is bigger than the old cut and holds
        # the whole fishing debate — otherwise the test would pass vacuously.
        window_tokens = litellm.token_counter(model=_MODEL, text=window)
        assert window_tokens > _OLD_TRUNCATE_TOKENS, "window no longer exceeds the old truncation budget"
        assert FISHING_DRUCKSNR in window, "fishing Drucksache 17/529 missing from window"
        fishing_hits_full = window.count("Fischerei")
        assert fishing_hits_full >= 10, f"expected a substantial fishing debate, found {fishing_hits_full} hits"

        # The bug: the old 12 000-token truncation kept the earlier open-data debate
        # in full but cut the fishing debate down to a fraction.
        truncated = _old_truncation(window)
        assert "Open Data" in truncated or "Open-Data" in truncated, "open-data debate should survive the old cut"
        fishing_hits_truncated = truncated.count("Fischerei")
        assert fishing_hits_truncated < fishing_hits_full / 2, (
            "old truncation should have dropped most of the fishing debate "
            f"(kept {fishing_hits_truncated} of {fishing_hits_full})"
        )

        # The fix: the full text now reaches the LLM, so the complete fishing debate
        # is available — including the parts past the old cut.
        tail = window[len(truncated) :]
        assert "Fischerei" in tail, "fishing content past the old cut must be preserved now that truncation is gone"


# Second agenda item sharing the same protocol PDF/window: TOP 3, Drucksache
# 17/513 (open-data/transparency bill, per DD-029's account of this window).
# Only the Drucksache and the fact that it's a distinct bill matter here — this
# proves cache collapse across *different* bill identities, not a specific title.
OPEN_DATA_TITEL = "Gesetz zur Verbesserung der Transparenz der Verwaltung"
OPEN_DATA_DRUCKSNR = "17/513"


class TestEnrichedSummaryIsNeutralAcrossBills:
    """End-to-end guarantee of the issue #49 fix — requires a real LLM."""

    @pytest.mark.asyncio
    async def test_two_bills_sharing_the_real_protocol_get_identical_summary(self):
        llm_key = os.environ.get("LLM_PROVIDER_KEY")
        if not llm_key:
            pytest.skip("LLM_PROVIDER_KEY not set — skipping LLM end-to-end check")

        from pazufa_corelib.llm import LLMConnector

        from bawue.bawue_dok import enrich_dokument

        llm = LLMConnector(model=os.environ.get("LLM_MODEL", _MODEL), api_key=llm_key)
        _hash_cache.clear()

        def _make_dok(drucksnr: str) -> Dokument:
            # Built exactly as the scraper does: the link carries the page hint;
            # only drucksnr differs between the two bills' own placeholder Dokumente.
            return Dokument(
                titel="Redebeitrag",
                volltext="",
                hash_="",
                typ=Doktyp.REDEPROTOKOLL,
                zp_modifiziert=datetime(2021, 9, 29, tzinfo=UTC),
                zp_referenz=datetime(2021, 9, 29, tzinfo=UTC),
                link=f"{PP_17_12_URL}#page={PP_17_12_PAGE_HINT}",
                autoren=[],
                drucksnr=drucksnr,
            )

        async with aiohttp.ClientSession() as session:
            result_fishing = await enrich_dokument(
                session, llm, _make_dok(FISHING_DRUCKSNR), vorgang_titel=FISHING_TITEL, vorgang_vnr=FISHING_DRUCKSNR
            )
            result_open_data = await enrich_dokument(
                session,
                llm,
                _make_dok(OPEN_DATA_DRUCKSNR),
                vorgang_titel=OPEN_DATA_TITEL,
                vorgang_vnr=OPEN_DATA_DRUCKSNR,
            )

        print(f"\nissue #49 shared summary (real LLM): {result_fishing.dokument.zusammenfassung!r}")

        assert result_fishing.dokument.zusammenfassung, "enrichment must produce a summary"
        assert result_fishing.dokument.hash_ == result_open_data.dokument.hash_, "same PDF must hash identically"
        # The actual issue #49 guarantee: whichever bill is enriched first computes
        # the summary and the second bill's differing identity must not recompute
        # (and thus overwrite) it with a different, equally one-sided version.
        assert result_fishing.dokument.zusammenfassung == result_open_data.dokument.zusammenfassung
        assert result_fishing.dokument.schlagworte == result_open_data.dokument.schlagworte
        assert result_fishing.dokument.kurztitel == result_open_data.dokument.kurztitel
