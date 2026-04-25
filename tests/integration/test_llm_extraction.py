"""Integration tests for LLM document enrichment — requires LLM_PROVIDER_KEY.

Run with: pytest -m integration tests/integration/test_llm_extraction.py

These tests hit a real LLM API and download real PDFs from the BaWue Landtag.
They verify that the full enrichment pipeline works end-to-end:
  PDF download → text extraction (kreuzberg) → LLM semantic extraction → enriched Dokument
"""

import os
from datetime import UTC, datetime

import aiohttp
import pytest
from openapi_client.models.autor import Autor
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.dokument import Dokument

from bawue.bawue_dok import enrich_dokument

pytestmark = pytest.mark.integration

# Skip entire module if no LLM key is available
LLM_PROVIDER_KEY = os.environ.get("LLM_PROVIDER_KEY")
if not LLM_PROVIDER_KEY:
    pytest.skip("LLM_PROVIDER_KEY not set — skipping LLM integration tests", allow_module_level=True)

# A small, known PDF from the BaWue Landtag (Gesetzentwurf, ~10 pages)
SAMPLE_PDF_URL = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Drucksachen/10000/17_10266_D.pdf"


def _make_test_dokument(typ: Doktyp = Doktyp.ENTWURF) -> Dokument:
    return Dokument(
        titel="Gesetz über einen Ausgleich im Zusammenhang mit Coronasoforthilfen des Landes Baden-Württemberg",
        volltext="",
        hash="",
        typ=typ,
        zp_modifiziert=datetime(2026, 1, 15, tzinfo=UTC),
        zp_referenz=datetime(2026, 1, 15, tzinfo=UTC),
        link=SAMPLE_PDF_URL,
        autoren=[Autor(organisation="Fraktion GRÜNE"), Autor(organisation="Fraktion der CDU")],
        drucksnr="17/10266",
    )


def _make_llm():
    from corelib.llm import LLMConnector

    return LLMConnector(
        model=os.environ.get("LLM_MODEL", "gpt-5-nano"),
        api_key=LLM_PROVIDER_KEY,
    )


class TestEntwurfEnrichment:
    @pytest.mark.asyncio
    async def test_full_enrichment_produces_all_fields(self):
        """Real PDF + real LLM → volltext, hash, zusammenfassung, schlagworte, kurztitel."""
        dok = _make_test_dokument(typ=Doktyp.ENTWURF)
        llm = _make_llm()

        async with aiohttp.ClientSession() as session:
            enriched = await enrich_dokument(session, llm, dok)

        # Text extraction worked
        assert len(enriched.volltext) > 100, "volltext should contain substantial text"
        assert len(enriched.hash) == 64, "hash should be SHA256 hex digest"

        # LLM extraction worked
        assert enriched.zusammenfassung is not None
        assert len(enriched.zusammenfassung) > 50, "zusammenfassung should be meaningful"
        assert enriched.schlagworte is not None
        assert len(enriched.schlagworte) >= 2, "should have at least 2 keywords"
        assert enriched.kurztitel is not None
        assert len(enriched.kurztitel) > 3, "kurztitel should be non-trivial"

        # Diagnostic output (visible with pytest -s)
        print("\n\n" + "=" * 72)
        print("ENRICHED DOKUMENT")
        print("=" * 72)
        print(f"Titel:           {enriched.titel}")
        print(f"Kurztitel:       {enriched.kurztitel}")
        print(f"Drucksnr:        {enriched.drucksnr}")
        print(f"Typ:             {enriched.typ}")
        print(f"Schlagworte:     {enriched.schlagworte}")
        print(f"Hash:            {enriched.hash}")
        print(f"Volltext:        {enriched.volltext[:200]}…")
        print(f"Zusammenfassung: {enriched.zusammenfassung}")
        print("=" * 72)

    @pytest.mark.asyncio
    async def test_parlis_metadata_preserved(self):
        """Enrichment must not overwrite PARLIS-provided fields."""
        dok = _make_test_dokument(typ=Doktyp.ENTWURF)
        llm = _make_llm()

        async with aiohttp.ClientSession() as session:
            enriched = await enrich_dokument(session, llm, dok)

        assert enriched.titel == (
            "Gesetz über einen Ausgleich im Zusammenhang mit Coronasoforthilfen des Landes Baden-Württemberg"
        )
        assert enriched.drucksnr == "17/10266"
        assert enriched.typ == Doktyp.ENTWURF
        assert enriched.autoren[0].organisation == "Fraktion GRÜNE"
        assert enriched.autoren[1].organisation == "Fraktion der CDU"
        assert enriched.zp_modifiziert == datetime(2026, 1, 15, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_deterministic_hash(self):
        """Same PDF → same hash across runs."""
        dok = _make_test_dokument()
        llm = _make_llm()

        async with aiohttp.ClientSession() as session:
            e1 = await enrich_dokument(session, llm, dok)
            e2 = await enrich_dokument(session, llm, dok)

        assert e1.hash == e2.hash
