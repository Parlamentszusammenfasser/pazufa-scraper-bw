"""Tests for the bawue_dok document enrichment module."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openapi_client.models.autor import Autor
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.dokument import Dokument

from bawue.bawue_dok import (
    _hash_cache,
    _prompt_for_doktyp,
    download_pdf,
    enrich_dokument,
    extract_pdf_text,
    extract_semantics,
    truncate_text,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PDF_BYTES = b"%PDF-1.4 fake content for testing"
SAMPLE_FULL_TEXT = "Dies ist ein Testtext mit mehr als 64 Zeichen, der als Volltext eines Gesetzesentwurfs dienen soll."
SAMPLE_HASH = hashlib.sha256(SAMPLE_PDF_BYTES).hexdigest()

SAMPLE_LLM_RESPONSE_ENTWURF = json.dumps(
    {
        "schlagworte": ["umwelt", "klimaschutz", "energie"],
        "zusammenfassung": "Ein Gesetzentwurf zur Förderung erneuerbarer Energien.",
        "kurztitel": "Erneuerbare-Energien-Gesetz",
        "trojanergefahr": 3,
    }
)

SAMPLE_LLM_RESPONSE_STELLUNGNAHME = json.dumps(
    {
        "schlagworte": ["bildung", "digitalisierung"],
        "zusammenfassung": "Eine Stellungnahme zur Digitalisierung der Schulen.",
        "kurztitel": "Schuldigitalisierung",
        "meinung": 4,
    }
)

SAMPLE_LLM_RESPONSE_BESCHLUSSEMPF = json.dumps(
    {
        "schlagworte": ["haushalt", "finanzen"],
        "zusammenfassung": "Empfehlung zur Annahme des Haushaltsgesetzes.",
        "kurztitel": "Haushaltsgesetz",
        "meinung": 5,
        "trojanergefahr": 2,
    }
)

SAMPLE_LLM_RESPONSE_GENERIC = json.dumps(
    {
        "schlagworte": ["verwaltung", "reform"],
        "zusammenfassung": "Ein allgemeines Dokument zur Verwaltungsreform.",
        "kurztitel": "Verwaltungsreform",
    }
)


def _make_plain_dokument(
    typ: Doktyp = Doktyp.ENTWURF,
    titel: str = "Testgesetz",
    link: str = "https://www.landtag-bw.de/test.pdf",
) -> Dokument:
    """Create a plain Dokument as the scraper currently builds it."""
    return Dokument(
        titel=titel,
        volltext="",
        hash="",
        typ=typ,
        zp_modifiziert=datetime(2026, 1, 15, tzinfo=UTC),
        zp_referenz=datetime(2026, 1, 15, tzinfo=UTC),
        link=link,
        autoren=[Autor(person="Max Mustermann", organisation="Fraktion GRÜNE")],
        drucksnr="17/10266",
    )


SAMPLE_TEXT_AND_HASH = (SAMPLE_FULL_TEXT, SAMPLE_HASH)


def _mock_extraction_result(content: str = SAMPLE_FULL_TEXT):
    """Create a mock kreuzberg ExtractionResult."""
    result = MagicMock()
    result.content = content
    result.metadata = {"created_at": "2026-01-15T12:00:00+00:00", "modified_at": "2026-01-15T12:00:00+00:00"}
    return result


def _patch_pdf_pipeline(text_and_hash=SAMPLE_TEXT_AND_HASH):
    """Context manager that mocks download_pdf and extract_pdf_text."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with (
            patch(
                "bawue.bawue_dok.download_pdf",
                new_callable=AsyncMock,
                return_value=Path("/tmp/fake.pdf"),
            ),
            patch(
                "bawue.bawue_dok.extract_pdf_text",
                new_callable=AsyncMock,
                return_value=text_and_hash,
            ),
        ):
            yield

    return _ctx()


# ---------------------------------------------------------------------------
# TestDownloadPdf
# ---------------------------------------------------------------------------


class TestDownloadPdf:
    @pytest.mark.asyncio
    async def test_downloads_to_tempfile(self):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=SAMPLE_PDF_BYTES)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        path = await download_pdf(session, "https://example.com/test.pdf")
        try:
            assert path.exists()
            assert path.suffix == ".pdf"
            assert path.read_bytes() == SAMPLE_PDF_BYTES
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with pytest.raises(Exception, match="404"):
            await download_pdf(session, "https://example.com/missing.pdf")


# ---------------------------------------------------------------------------
# TestExtractPdfText
# ---------------------------------------------------------------------------


class TestExtractPdfText:
    @pytest.mark.asyncio
    async def test_extracts_text_and_hash(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        mock_result = _mock_extraction_result()
        with patch("bawue.bawue_dok.extract_file", new_callable=AsyncMock, return_value=mock_result):
            text, doc_hash = await extract_pdf_text(pdf_file)

        assert text == SAMPLE_FULL_TEXT
        assert doc_hash == SAMPLE_HASH

    @pytest.mark.asyncio
    async def test_ocr_fallback_on_short_text(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        short_result = _mock_extraction_result(content="Too short")
        ocr_result = _mock_extraction_result(content=SAMPLE_FULL_TEXT)

        with patch(
            "bawue.bawue_dok.extract_file",
            new_callable=AsyncMock,
            side_effect=[short_result, ocr_result],
        ):
            text, doc_hash = await extract_pdf_text(pdf_file)

        assert text == SAMPLE_FULL_TEXT
        assert doc_hash == SAMPLE_HASH


# ---------------------------------------------------------------------------
# TestExtractSemantics
# ---------------------------------------------------------------------------


class TestExtractSemantics:
    @pytest.mark.asyncio
    async def test_parses_json_response(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        result = await extract_semantics(llm, SAMPLE_FULL_TEXT, Doktyp.ENTWURF)

        assert result["schlagworte"] == ["umwelt", "klimaschutz", "energie"]
        assert "Gesetzentwurf" in result["zusammenfassung"]
        assert result["kurztitel"] == "Erneuerbare-Energien-Gesetz"
        assert result["trojanergefahr"] == 3

    @pytest.mark.asyncio
    async def test_retries_on_invalid_json(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(side_effect=["not valid json", SAMPLE_LLM_RESPONSE_GENERIC])

        result = await extract_semantics(llm, SAMPLE_FULL_TEXT, Doktyp.MITTEILUNG)

        assert result["schlagworte"] == ["verwaltung", "reform"]
        assert llm.generate_text.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value="not json at all")

        with pytest.raises(json.JSONDecodeError):
            await extract_semantics(llm, SAMPLE_FULL_TEXT, Doktyp.ENTWURF)


# ---------------------------------------------------------------------------
# TestPromptForDoktyp
# ---------------------------------------------------------------------------


class TestPromptForDoktyp:
    def test_entwurf_prompt_has_trojanergefahr(self):
        prompt = _prompt_for_doktyp(Doktyp.ENTWURF)
        assert "trojanergefahr" in prompt.lower() or "Trojanergefahr" in prompt

    def test_preparl_entwurf_uses_entwurf_prompt(self):
        assert _prompt_for_doktyp(Doktyp.PREPARL_MINUS_ENTWURF) == _prompt_for_doktyp(Doktyp.ENTWURF)

    def test_stellungnahme_prompt_has_meinung(self):
        prompt = _prompt_for_doktyp(Doktyp.STELLUNGNAHME)
        assert "meinung" in prompt.lower() or "Meinung" in prompt
        assert "trojanergefahr" not in prompt.lower()

    def test_beschlussempf_prompt_has_both(self):
        prompt = _prompt_for_doktyp(Doktyp.BESCHLUSSEMPF)
        assert "meinung" in prompt.lower() or "Meinung" in prompt
        assert "trojanergefahr" in prompt.lower() or "Trojanergefahr" in prompt

    def test_redeprotokoll_uses_generic_prompt(self):
        prompt = _prompt_for_doktyp(Doktyp.REDEPROTOKOLL)
        assert "trojanergefahr" not in prompt.lower()
        assert "meinung" not in prompt.lower()

    def test_sonstig_uses_generic_prompt(self):
        prompt = _prompt_for_doktyp(Doktyp.SONSTIG)
        assert "schlagworte" in prompt.lower() or "Schlagworte" in prompt


# ---------------------------------------------------------------------------
# TestEnrichDokument
# ---------------------------------------------------------------------------


class TestEnrichDokument:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_full_enrichment(self):
        """LLM key set, PDF+LLM succeed → all fields populated."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        with _patch_pdf_pipeline():
            enriched = await enrich_dokument(session, llm, dok)

        assert enriched.volltext == SAMPLE_FULL_TEXT
        assert enriched.hash == SAMPLE_HASH
        assert enriched.zusammenfassung == "Ein Gesetzentwurf zur Förderung erneuerbarer Energien."
        assert enriched.schlagworte == ["umwelt", "klimaschutz", "energie"]
        assert enriched.kurztitel == "Erneuerbare-Energien-Gesetz"

    @pytest.mark.asyncio
    async def test_preserves_parlis_metadata(self):
        """PARLIS metadata (titel, autoren, drucksnr, timestamps) must not change."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        with _patch_pdf_pipeline():
            enriched = await enrich_dokument(session, llm, dok)

        assert enriched.titel == "Testgesetz"
        assert enriched.autoren[0].person == "Max Mustermann"
        assert enriched.drucksnr == "17/10266"
        assert enriched.zp_modifiziert == datetime(2026, 1, 15, tzinfo=UTC)
        assert enriched.typ == Doktyp.ENTWURF

    @pytest.mark.asyncio
    async def test_stellungnahme_gets_meinung(self):
        dok = _make_plain_dokument(typ=Doktyp.STELLUNGNAHME)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_STELLUNGNAHME)

        with _patch_pdf_pipeline():
            enriched = await enrich_dokument(session, llm, dok)

        assert enriched.meinung == 4

    @pytest.mark.asyncio
    async def test_beschlussempf_gets_meinung_and_trojanergefahr(self):
        dok = _make_plain_dokument(typ=Doktyp.BESCHLUSSEMPF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_BESCHLUSSEMPF)

        with _patch_pdf_pipeline():
            enriched = await enrich_dokument(session, llm, dok)

        assert enriched.meinung == 5

    @pytest.mark.asyncio
    async def test_text_only_fallback_on_llm_failure(self):
        """LLM fails → volltext+hash set, no LLM fields."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(side_effect=Exception("LLM unavailable"))

        with _patch_pdf_pipeline():
            enriched = await enrich_dokument(session, llm, dok)

        # Text-only: volltext and hash populated
        assert enriched.volltext == SAMPLE_FULL_TEXT
        assert enriched.hash == SAMPLE_HASH
        # No LLM fields
        assert enriched.zusammenfassung is None
        assert enriched.schlagworte is None

    @pytest.mark.asyncio
    async def test_metadata_only_fallback_on_download_failure(self):
        """PDF download fails → original Dokument unchanged."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()

        with patch("bawue.bawue_dok.download_pdf", new_callable=AsyncMock, side_effect=Exception("Download failed")):
            enriched = await enrich_dokument(session, llm, dok)

        # Original document returned unchanged
        assert enriched.volltext == ""
        assert enriched.hash == ""
        assert enriched.zusammenfassung is None

    @pytest.mark.asyncio
    async def test_tempfile_cleaned_up_after_enrichment(self):
        """Temporary PDF file should be cleaned up after enrichment."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(SAMPLE_PDF_BYTES)

        dok = _make_plain_dokument(typ=Doktyp.MITTEILUNG)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_GENERIC)

        with (
            patch(
                "bawue.bawue_dok.download_pdf",
                new_callable=AsyncMock,
                return_value=tmp_path,
            ),
            patch(
                "bawue.bawue_dok.extract_pdf_text",
                new_callable=AsyncMock,
                return_value=SAMPLE_TEXT_AND_HASH,
            ),
        ):
            await enrich_dokument(session, llm, dok)

        assert not tmp_path.exists(), "Temporary PDF should be cleaned up"


# ---------------------------------------------------------------------------
# TestTruncateText
# ---------------------------------------------------------------------------


class TestTruncateText:
    def test_truncates_when_over_limit(self):
        long_text = "Dies ist ein langer Testtext. " * 500
        result = truncate_text(long_text, max_tokens=100, model="gpt-5-nano")
        import litellm

        token_count = litellm.token_counter(model="gpt-5-nano", text=result)
        assert token_count <= 100
        assert len(result) < len(long_text)

    def test_no_truncation_when_under_limit(self):
        short_text = "Kurzer Text."
        result = truncate_text(short_text, max_tokens=1000, model="gpt-5-nano")
        assert result == short_text

    def test_disabled_when_zero(self):
        long_text = "Dies ist ein langer Testtext. " * 500
        result = truncate_text(long_text, max_tokens=0, model="gpt-5-nano")
        assert result == long_text


# ---------------------------------------------------------------------------
# TestHashCache
# ---------------------------------------------------------------------------


class TestHashCache:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm(self):
        """Second call with same PDF hash reuses cached semantics, no LLM call."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        with _patch_pdf_pipeline():
            first = await enrich_dokument(session, llm, dok)
            second = await enrich_dokument(session, llm, dok)

        # LLM called only once despite two enrichments
        assert llm.generate_text.call_count == 1
        # Both results have the same semantics
        assert first.zusammenfassung == second.zusammenfassung
        assert first.schlagworte == second.schlagworte

    @pytest.mark.asyncio
    async def test_cache_miss_calls_llm(self):
        """First call always invokes the LLM."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        with _patch_pdf_pipeline():
            enriched = await enrich_dokument(session, llm, dok)

        assert llm.generate_text.call_count == 1
        assert enriched.zusammenfassung is not None

    @pytest.mark.asyncio
    async def test_different_hashes_both_call_llm(self):
        """Different PDF hashes result in separate LLM calls."""
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        hash_a = "aaaa" * 16
        hash_b = "bbbb" * 16
        dok_a = _make_plain_dokument(link="https://example.com/a.pdf")
        dok_b = _make_plain_dokument(link="https://example.com/b.pdf")

        with _patch_pdf_pipeline(text_and_hash=(SAMPLE_FULL_TEXT, hash_a)):
            await enrich_dokument(session, llm, dok_a)

        with _patch_pdf_pipeline(text_and_hash=(SAMPLE_FULL_TEXT, hash_b)):
            await enrich_dokument(session, llm, dok_b)

        assert llm.generate_text.call_count == 2


# ---------------------------------------------------------------------------
# TestTokenLogging
# ---------------------------------------------------------------------------


class TestTokenLogging:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_logs_token_count(self, caplog):
        """Token count is logged for each LLM call."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        with _patch_pdf_pipeline(), caplog.at_level(logging.INFO, logger="bawue.bawue_dok"):
            await enrich_dokument(session, llm, dok)

        assert any("token" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_truncation(self, caplog):
        """Truncation is logged when text exceeds limit."""
        long_text = "Dies ist ein langer Testtext. " * 500
        long_hash = "cccc" * 16
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        with (
            _patch_pdf_pipeline(text_and_hash=(long_text, long_hash)),
            caplog.at_level(logging.INFO, logger="bawue.bawue_dok"),
        ):
            await enrich_dokument(session, llm, dok, max_tokens=100)

        assert any("truncat" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_cache_hit(self, caplog):
        """Cache hit is logged when hash matches."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_ENTWURF)

        with _patch_pdf_pipeline(), caplog.at_level(logging.INFO, logger="bawue.bawue_dok"):
            await enrich_dokument(session, llm, dok)
            await enrich_dokument(session, llm, dok)

        assert any("cache hit" in r.message.lower() for r in caplog.records)
