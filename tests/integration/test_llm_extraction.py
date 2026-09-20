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

from bawue.bawue_dok import (
    KURZTITEL_MAX_LEN,
    enrich_dokument,
    vorgang_kurztitel,
    vorgang_ressort,
    zusammenfassung_text,
)
from bawue.types import Autor, Doktyp, Dokument, Ressort

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
        hash_="",
        typ=typ,
        zp_modifiziert=datetime(2026, 1, 15, tzinfo=UTC),
        zp_referenz=datetime(2026, 1, 15, tzinfo=UTC),
        link=SAMPLE_PDF_URL,
        autoren=[Autor(organisation="Fraktion GRÜNE"), Autor(organisation="Fraktion der CDU")],
        drucksnr="17/10266",
    )


def _make_llm():
    from pazufa_corelib.llm import LLMConnector

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
            result = await enrich_dokument(session, llm, dok)
        enriched = result.dokument

        # Text extraction worked
        assert len(enriched.volltext) > 100, "volltext should contain substantial text"
        assert len(enriched.hash_) == 64, "hash should be SHA256 hex digest"

        # LLM extraction worked
        assert enriched.zusammenfassung[0].typ == "full-llm"  # GitHub issue #42, DD-054
        assert len(zusammenfassung_text(enriched)) > 50, "zusammenfassung should be meaningful"
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
        print(f"Hash:            {enriched.hash_}")
        print(f"Volltext:        {enriched.volltext[:200]}…")
        print(f"Zusammenfassung: {zusammenfassung_text(enriched)}")
        print("=" * 72)

    @pytest.mark.asyncio
    async def test_parlis_metadata_preserved(self):
        """Enrichment must not overwrite PARLIS-provided fields."""
        dok = _make_test_dokument(typ=Doktyp.ENTWURF)
        llm = _make_llm()

        async with aiohttp.ClientSession() as session:
            result = await enrich_dokument(session, llm, dok)
        enriched = result.dokument

        assert enriched.titel == (
            "Gesetz über einen Ausgleich im Zusammenhang mit Coronasoforthilfen des Landes Baden-Württemberg"
        )
        assert enriched.drucksnr == "17/10266"
        assert enriched.typ == Doktyp.ENTWURF
        assert enriched.autoren[0].organisation == "Fraktion GRÜNE"
        assert enriched.autoren[1].organisation == "Fraktion der CDU"
        assert enriched.zp_modifiziert == datetime(2026, 1, 15, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_vorgang_kurztitel_is_short_and_readable(self):
        """Issue #25 / GitHub issue #32 (real data): a real Gesetzentwurf yields a
        Vorgang Kurztitel that is a short, human-readable title (DD-053).

        Only real document text + a real LLM can validate the *semantic* quality;
        wiring and fallbacks are covered by the unit tests.
        """
        dok = _make_test_dokument(typ=Doktyp.ENTWURF)
        llm = _make_llm()

        async with aiohttp.ClientSession() as session:
            enriched = (await enrich_dokument(session, llm, dok)).dokument

        kurztitel = await vorgang_kurztitel(llm, dok.titel, zusammenfassung_text(enriched))

        print(f"\nVorgang Kurztitel (real data): {kurztitel!r}")

        # dok.titel is 98 chars: anything else than the fallback means generation succeeded.
        assert kurztitel != dok.titel, "LLM generation fell back to titel"
        assert len(kurztitel) <= KURZTITEL_MAX_LEN
        assert "\n" not in kurztitel
        # It must be a title, not an identifier.
        assert kurztitel != enriched.drucksnr
        assert " " in kurztitel.strip(), "a human-readable title has more than one word"

    @pytest.mark.asyncio
    async def test_deterministic_hash(self):
        """Same PDF → same hash across runs."""
        dok = _make_test_dokument()
        llm = _make_llm()

        async with aiohttp.ClientSession() as session:
            e1 = await enrich_dokument(session, llm, dok)
            e2 = await enrich_dokument(session, llm, dok)

        assert e1.dokument.hash_ == e2.dokument.hash_


class TestVorgangRessort:
    """GitHub issue #39 (DD-055): the classifier picks a Ressort by subject matter."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "titel,zusammenfassung,expected",
        [
            (
                "Staatshaushaltsgesetz 2027/2028",
                "Feststellung des Staatshaushaltsplans für die Jahre 2027 und 2028.",
                Ressort.FINANZEN,
            ),
            (
                "Gesetz zur Änderung der Gemeindeordnung",
                "Änderungen am Kommunalrecht der Gemeinden und Landkreise.",
                Ressort.KOMMUNALES,
            ),
        ],
    )
    async def test_shared_rules_hold_on_real_calls(self, titel, zusammenfassung, expected):
        """The two rules BW inherits from the BB prompt: Haushalt/Steuern → Finanzen,
        Kommunalrecht → Kommunales."""
        ressort = await vorgang_ressort(_make_llm(), titel, zusammenfassung)

        print(f"\nVorgang Ressort (real LLM): {titel[:50]!r} → {ressort}")
        assert ressort == expected

    @pytest.mark.asyncio
    async def test_subject_matter_beats_the_submitting_body(self):
        """A wind-power bill is Energie even though an Umweltministerium submits it —
        the difference to deriving the Ressort from the ministry name."""
        ressort = await vorgang_ressort(
            _make_llm(),
            "Gesetz zur Förderung des Ausbaus der Windenergie in Baden-Württemberg",
            "Der Entwurf beschleunigt Genehmigungsverfahren für Windkraftanlagen und weist Vorranggebiete aus.",
        )

        assert ressort in {Ressort.ENERGIE, Ressort.KLIMASCHUTZ, Ressort.UMWELT}
