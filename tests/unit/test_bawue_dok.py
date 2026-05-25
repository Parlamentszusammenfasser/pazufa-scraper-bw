"""Tests for the bawue_dok document enrichment module."""

import hashlib
import json
import logging
import ssl
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from openapi_client.models.autor import Autor
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.dokument import Dokument

from bawue.bawue_dok import (
    EnrichmentResult,
    LLMMetrics,
    _cache_key,
    _extract_relevant_pages,
    _hash_cache,
    _is_garbled,
    _paragraph_quality_score,
    _parse_llm_response,
    _parse_page_hint,
    _prompt_fingerprint,
    _prompt_for_doktyp,
    _sanitize_llm_strings,
    _sanitize_llm_text,
    _validate_scores,
    clear_hash_cache,
    download_pdf,
    enrich_dokument,
    extract_pdf_text,
    extract_semantics,
    normalize_volltext,
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
        "vorwort": "Ziel dieses Gesetzentwurfs ist die Förderung erneuerbarer Energien.",
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


def _mock_llm_response(json_str: str):
    """Create a mock litellm response with the given JSON content."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json_str
    return mock_response


def _patch_llm(json_str: str):
    """Context manager that mocks litellm.acompletion to return the given JSON."""
    return patch(
        "bawue.bawue_dok.litellm.acompletion",
        new_callable=AsyncMock,
        return_value=_mock_llm_response(json_str),
    )


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

    @pytest.mark.asyncio
    async def test_download_pdf_strips_url_fragment(self):
        """URL fragment (#page=33) should be stripped before HTTP request."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=SAMPLE_PDF_BYTES)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        path = await download_pdf(session, "https://www.landtag-bw.de/files/plp/17_141.pdf#page=33")
        try:
            args, _kwargs = session.get.call_args
            assert args[0] == "https://www.landtag-bw.de/files/plp/17_141.pdf"
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_passes_ssl_context_and_timeout(self):
        """download_pdf should pass an SSL context (certifi) and a 60s timeout."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=SAMPLE_PDF_BYTES)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        path = await download_pdf(session, "https://example.com/test.pdf")
        try:
            _, kwargs = session.get.call_args
            assert isinstance(kwargs["ssl"], ssl.SSLContext)
            assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
            assert kwargs["timeout"].total == 60
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_logs_http_status_on_download_failure(self, caplog):
        """Non-200 status should be logged as warning before raising."""
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        with (
            pytest.raises(RuntimeError),
            caplog.at_level(logging.WARNING, logger="bawue.bawue_dok"),
        ):
            await download_pdf(session, "https://example.com/missing.pdf")

        assert any("404" in r.message for r in caplog.records)


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
    async def test_extract_pdf_text_with_page_hint(self, tmp_path):
        """When page_hint is set, PageConfig(insert_page_markers=True) should be used."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        # Build text with page markers — page 33 has the relevant content
        pages = []
        for i in range(1, 40):
            pages.append(f"\n\n<!-- PAGE {i} -->\n\nContent of page {i}")
        marked_text = "".join(pages)

        mock_result = _mock_extraction_result(content=marked_text)
        with patch("bawue.bawue_dok.extract_file", new_callable=AsyncMock, return_value=mock_result) as mock_extract:
            text, _ = await extract_pdf_text(pdf_file, page_hint=33)

        # Verify PageConfig was used
        call_config = mock_extract.call_args_list[0].kwargs["config"]
        assert call_config.pages is not None
        assert call_config.pages.insert_page_markers is True
        # Verify only relevant pages returned
        assert "Content of page 33" in text
        assert "Content of page 1" not in text

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


def _make_llm_mock():
    """Create a mock LLMConnector with proper non-coroutine attributes."""
    llm = MagicMock()
    llm.api_key = "test-key"
    llm.temperature = 0.1
    llm.timeout_seconds = 60.0
    return llm


class TestExtractSemantics:
    @pytest.mark.asyncio
    async def test_parses_json_response(self):
        llm = _make_llm_mock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = SAMPLE_LLM_RESPONSE_ENTWURF

        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await extract_semantics(llm, SAMPLE_FULL_TEXT, Doktyp.ENTWURF)

        assert result["schlagworte"] == ["umwelt", "klimaschutz", "energie"]
        assert "Gesetzentwurf" in result["zusammenfassung"]
        assert result["kurztitel"] == "Erneuerbare-Energien-Gesetz"
        assert result["trojanergefahr"] == 3

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json_from_provider(self):
        """If provider returns truly unrecoverable output, JSONDecodeError is re-raised after repair attempt."""
        llm = _make_llm_mock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not json at all"

        with (
            patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, return_value=mock_response),
            pytest.raises(json.JSONDecodeError),
        ):
            await extract_semantics(llm, SAMPLE_FULL_TEXT, Doktyp.ENTWURF)


# ---------------------------------------------------------------------------
# TestParseLlmResponse
# ---------------------------------------------------------------------------


class TestParseLlmResponse:
    """Direct tests for the JSON-parsing helper, no LLM mock needed."""

    def test_parses_valid_json(self):
        result = _parse_llm_response('{"schlagworte": ["a"], "kurztitel": "x"}')
        assert result == {"schlagworte": ["a"], "kurztitel": "x"}

    def test_repairs_missing_comma_delimiter(self):
        # Production-shaped defect: missing comma between two fields.
        # Mirrors the JSONDecodeError pattern observed in gemma4:e4b output.
        broken = (
            '{"schlagworte": ["umwelt", "klimaschutz"]'
            ' "zusammenfassung": "Kurz gefasst.",'
            ' "kurztitel": "EEG",'
            ' "trojanergefahr": 3}'
        )
        result = _parse_llm_response(broken)
        assert result["schlagworte"] == ["umwelt", "klimaschutz"]
        assert result["kurztitel"] == "EEG"
        assert result["trojanergefahr"] == 3

    def test_repairs_trailing_comma(self):
        broken = '{"schlagworte": ["a", "b",], "kurztitel": "y", "trojanergefahr": 1,}'
        result = _parse_llm_response(broken)
        assert result["schlagworte"] == ["a", "b"]
        assert result["trojanergefahr"] == 1

    def test_raises_when_content_is_unrepairable_garbage(self):
        # json-repair returns "" for pure non-JSON input → non-dict → we re-raise.
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response("not json at all")

    def test_raises_when_repair_returns_non_dict(self):
        # Array-shaped content: json.loads raises (no outer brackets),
        # json-repair likely yields a list. A list must not silently pass
        # through to _validate_scores, which assumes a dict.
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response('"a", "b", "c"')


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
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            result = await enrich_dokument(session, llm, dok)

        assert isinstance(result, EnrichmentResult)
        assert result.dokument.volltext == SAMPLE_FULL_TEXT
        assert result.dokument.hash == SAMPLE_HASH
        assert result.dokument.zusammenfassung == "Ein Gesetzentwurf zur Förderung erneuerbarer Energien."
        assert result.dokument.schlagworte == ["umwelt", "klimaschutz", "energie"]
        assert result.dokument.kurztitel == "Erneuerbare-Energien-Gesetz"
        assert result.trojanergefahr == 3

    @pytest.mark.asyncio
    async def test_preserves_parlis_metadata(self):
        """PARLIS metadata (titel, autoren, drucksnr, timestamps) must not change."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.titel == "Testgesetz"
        assert result.dokument.autoren[0].person == "Max Mustermann"
        assert result.dokument.drucksnr == "17/10266"
        assert result.dokument.zp_modifiziert == datetime(2026, 1, 15, tzinfo=UTC)
        assert result.dokument.typ == Doktyp.ENTWURF

    @pytest.mark.asyncio
    async def test_stellungnahme_gets_meinung(self):
        dok = _make_plain_dokument(typ=Doktyp.STELLUNGNAHME)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_STELLUNGNAHME):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.meinung == 4
        assert result.trojanergefahr is None

    @pytest.mark.asyncio
    async def test_beschlussempf_gets_meinung_and_trojanergefahr(self):
        dok = _make_plain_dokument(typ=Doktyp.BESCHLUSSEMPF)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_BESCHLUSSEMPF):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.meinung == 5
        assert result.trojanergefahr == 2

    @pytest.mark.asyncio
    async def test_generic_has_no_trojanergefahr(self):
        dok = _make_plain_dokument(typ=Doktyp.MITTEILUNG)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC):
            result = await enrich_dokument(session, llm, dok)

        assert result.trojanergefahr is None

    @pytest.mark.asyncio
    async def test_text_only_fallback_on_llm_failure(self):
        """LLM fails → volltext+hash set, no LLM fields, no trojanergefahr."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        llm_fail = patch(
            "bawue.bawue_dok.litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=Exception("LLM unavailable"),
        )
        with _patch_pdf_pipeline(), llm_fail:
            result = await enrich_dokument(session, llm, dok)

        # Text-only: volltext and hash populated
        assert result.dokument.volltext == SAMPLE_FULL_TEXT
        assert result.dokument.hash == SAMPLE_HASH
        # No LLM fields
        assert result.dokument.zusammenfassung is None
        assert result.dokument.schlagworte is None
        assert result.trojanergefahr is None

    @pytest.mark.asyncio
    async def test_enrich_dokument_with_page_hint(self):
        """URL with #page=N: fragment stripped for download, page_hint passed to extraction, link preserved."""
        page_url = "https://www.landtag-bw.de/files/plp/17_141.pdf#page=33"
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, link=page_url)
        session = MagicMock()
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=SAMPLE_LLM_RESPONSE_GENERIC)

        with (
            patch(
                "bawue.bawue_dok.download_pdf",
                new_callable=AsyncMock,
                return_value=Path("/tmp/fake.pdf"),
            ) as mock_download,
            patch(
                "bawue.bawue_dok.extract_pdf_text",
                new_callable=AsyncMock,
                return_value=SAMPLE_TEXT_AND_HASH,
            ) as mock_extract,
        ):
            result = await enrich_dokument(session, llm, dok)

        # download_pdf receives the full URL (fragment stripping happens inside)
        mock_download.assert_called_once_with(session, page_url)
        # extract_pdf_text receives the page_hint
        mock_extract.assert_called_once_with(Path("/tmp/fake.pdf"), page_hint=33)
        # dok.link preserves the original URL with fragment
        assert result.dokument.link == page_url

    @pytest.mark.asyncio
    async def test_metadata_only_fallback_on_download_failure(self):
        """PDF download fails → original Dokument unchanged, no trojanergefahr."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()

        with patch("bawue.bawue_dok.download_pdf", new_callable=AsyncMock, side_effect=Exception("Download failed")):
            result = await enrich_dokument(session, llm, dok)

        # Original document returned unchanged
        assert result.dokument.volltext == ""
        assert result.dokument.hash == ""
        assert result.dokument.zusammenfassung is None
        assert result.trojanergefahr is None

    @pytest.mark.asyncio
    async def test_metadata_only_fallback_on_empty_extracted_text(self):
        """PDF extracts to empty text → original Dokument returned, LLM not invoked.

        The backend rejects empty ``volltext`` (required StrictStr), so we
        must not build a Dokument with ``volltext=""`` and must not ask the
        LLM to hallucinate metadata for nothing.
        """
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()

        llm_call = patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock)
        with _patch_pdf_pipeline(text_and_hash=("", "deadbeef")), llm_call as mock_llm:
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument is dok
        assert result.trojanergefahr is None
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_tempfile_cleaned_up_after_enrichment(self):
        """Temporary PDF file should be cleaned up after enrichment."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(SAMPLE_PDF_BYTES)

        dok = _make_plain_dokument(typ=Doktyp.MITTEILUNG)
        session = MagicMock()
        llm = _make_llm_mock()

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
            _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC),
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

    def test_truncated_output_is_valid_utf8(self):
        """Truncation at token boundary must not produce orphaned multi-byte sequences."""
        # Build text with many multi-byte chars (umlauts, sharp-s) to maximize
        # the chance of hitting a multi-byte boundary when slicing tokens
        text = "Änderung des Gesetzes über Maßnahmen für Schülerinnen und Schüler " * 300
        result = truncate_text(text, max_tokens=100, model="gpt-5-nano")
        # Re-encode and decode to verify clean UTF-8 round-trip
        assert result == result.encode("utf-8").decode("utf-8")
        # Must also survive JSON serialization (the actual failure mode)
        import json

        json.dumps({"text": result})  # raises on invalid surrogates/sequences


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
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp:
            first = await enrich_dokument(session, llm, dok)
            second = await enrich_dokument(session, llm, dok)

        # LLM called only once despite two enrichments
        assert mock_acomp.call_count == 1
        # Both results have the same semantics
        assert first.dokument.zusammenfassung == second.dokument.zusammenfassung
        assert first.dokument.schlagworte == second.dokument.schlagworte
        assert first.trojanergefahr == second.trojanergefahr

    @pytest.mark.asyncio
    async def test_cache_miss_calls_llm(self):
        """First call always invokes the LLM."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp:
            result = await enrich_dokument(session, llm, dok)

        assert mock_acomp.call_count == 1
        assert result.dokument.zusammenfassung is not None

    @pytest.mark.asyncio
    async def test_different_hashes_both_call_llm(self):
        """Different PDF hashes result in separate LLM calls."""
        session = MagicMock()
        llm = _make_llm_mock()

        hash_a = "aaaa" * 16
        hash_b = "bbbb" * 16
        dok_a = _make_plain_dokument(link="https://example.com/a.pdf")
        dok_b = _make_plain_dokument(link="https://example.com/b.pdf")

        with _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp:
            with _patch_pdf_pipeline(text_and_hash=(SAMPLE_FULL_TEXT, hash_a)):
                await enrich_dokument(session, llm, dok_a)

            with _patch_pdf_pipeline(text_and_hash=(SAMPLE_FULL_TEXT, hash_b)):
                await enrich_dokument(session, llm, dok_b)

        assert mock_acomp.call_count == 2

    @pytest.mark.asyncio
    async def test_same_pdf_different_doktyp_calls_llm_twice(self):
        """Same PDF text/hash but different Doktyp → different prompt → must re-call LLM.

        Regression: the cache key used to depend only on the PDF hash, so enriching
        the same PDF as ENTWURF then as STELLUNGNAHME returned the Entwurf's semantics
        (with trojanergefahr) instead of running the Stellungnahme prompt (with meinung).
        """
        session = MagicMock()
        llm = _make_llm_mock()

        dok_entwurf = _make_plain_dokument(typ=Doktyp.ENTWURF, link="https://example.com/same.pdf")
        dok_stln = _make_plain_dokument(typ=Doktyp.STELLUNGNAHME, link="https://example.com/same.pdf")

        with _patch_pdf_pipeline():  # same text + hash for both
            with _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_entwurf:
                await enrich_dokument(session, llm, dok_entwurf)
            assert mock_entwurf.call_count == 1

            with _patch_llm(SAMPLE_LLM_RESPONSE_STELLUNGNAHME) as mock_stln:
                result = await enrich_dokument(session, llm, dok_stln)

        # Different prompt → fresh LLM call required
        assert mock_stln.call_count == 1
        # And Stellungnahme-specific field must come through (not the cached Entwurf answer)
        assert result.dokument.meinung == 4

    @pytest.mark.asyncio
    async def test_same_pdf_same_doktyp_hits_cache_on_second_run(self):
        """Same PDF + same Doktyp across two in-memory runs → exactly one LLM call."""
        session = MagicMock()
        llm = _make_llm_mock()

        dok_a = _make_plain_dokument(typ=Doktyp.BESCHLUSSEMPF, link="https://example.com/a.pdf")
        dok_b = _make_plain_dokument(typ=Doktyp.BESCHLUSSEMPF, link="https://example.com/b.pdf")

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_BESCHLUSSEMPF) as mock_acomp:
            await enrich_dokument(session, llm, dok_a)
            await enrich_dokument(session, llm, dok_b)

        assert mock_acomp.call_count == 1


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
        llm = _make_llm_mock()

        with (
            _patch_pdf_pipeline(),
            _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF),
            caplog.at_level(logging.INFO, logger="bawue.bawue_dok"),
        ):
            await enrich_dokument(session, llm, dok)

        assert any("token" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_truncation(self, caplog):
        """Truncation is logged when text exceeds limit."""
        long_text = "Dies ist ein langer Testtext. " * 500
        long_hash = "cccc" * 16
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with (
            _patch_pdf_pipeline(text_and_hash=(long_text, long_hash)),
            _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF),
            caplog.at_level(logging.INFO, logger="bawue.bawue_dok"),
        ):
            await enrich_dokument(session, llm, dok, max_tokens=100)

        assert any("truncat" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_cache_hit(self, caplog):
        """Cache hit is logged when hash matches."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with (
            _patch_pdf_pipeline(),
            _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF),
            caplog.at_level(logging.INFO, logger="bawue.bawue_dok"),
        ):
            await enrich_dokument(session, llm, dok)
            await enrich_dokument(session, llm, dok)

        assert any("cache hit" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# TestParagraphQualityScore
# ---------------------------------------------------------------------------


class TestParagraphQualityScore:
    def test_clean_german_scores_high(self):
        text = "Gemäß § 50a Absatz 2 der Geschäftsordnung habe ich im Einvernehmen mit den Antragstellern"
        assert _paragraph_quality_score(text) > 0.9

    def test_garbled_shifted_text_scores_low(self):
        """Shifted font encoding: consonant-heavy gibberish with C1 control chars."""
        text = "6WlGWHWDJ%DGHQ\x81UWWHPEHUJ\x873RVWIDFK\x876WXWWJDUW"
        assert _paragraph_quality_score(text) < 0.5

    def test_garbled_latin_extended_scores_low(self):
        """Latin Extended-B garbled output from broken ToUnicode maps."""
        text = "ůĂƐƐĞŶ-ŵŝƚ-ǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶ-ƵŶĚ"
        assert _paragraph_quality_score(text) < 0.5

    def test_empty_text_scores_high(self):
        assert _paragraph_quality_score("") == 1.0

    def test_whitespace_only_scores_high(self):
        assert _paragraph_quality_score("   \n  ") == 1.0

    def test_german_with_umlauts_scores_high(self):
        text = "\u00c4nderung des Schulgesetzes f\u00fcr Baden-W\u00fcrttemberg - Drucksache 17/4142"
        assert _paragraph_quality_score(text) > 0.9

    def test_page_header_scores_high(self):
        """Page headers like 'Landtag von Baden-Württemberg Drucksache 17 / 4244' are clean."""
        text = "Landtag von Baden-Württemberg Drucksache 17 / 4244"
        assert _paragraph_quality_score(text) > 0.9

    def test_long_garbled_paragraph_scores_low(self):
        text = (
            "6WlGWHWDJVVWHOOXQJQDKPH]XP*HVHW]HQWZXUI-GHU)'3'93]XU:LHGHUKHUVWHOOXQJ\r\n"
            "GHU-9HUELQGOLFKNHLW-GHU*UXQGVFKXOHPSIHKOXQJ/DQGWDJVGUXFNVDFKH\r\n"
        )
        assert _paragraph_quality_score(text) < 0.5


# ---------------------------------------------------------------------------
# TestNormalizeVolltext
# ---------------------------------------------------------------------------


class TestNormalizeVolltext:
    def test_clean_text_unchanged(self):
        """Clean German text should pass through without modification."""
        text = "Gemäß § 50a Absatz 2 der Geschäftsordnung."
        assert normalize_volltext(text) == text

    def test_crlf_normalized_to_lf(self):
        assert normalize_volltext("Zeile eins\r\nZeile zwei") == "Zeile eins\nZeile zwei"

    def test_lone_cr_normalized_to_lf(self):
        assert normalize_volltext("Zeile eins\rZeile zwei") == "Zeile eins\nZeile zwei"

    def test_excessive_blank_lines_collapsed(self):
        text = "Absatz eins\n\n\n\n\nAbsatz zwei"
        result = normalize_volltext(text)
        assert result == "Absatz eins\n\nAbsatz zwei"

    def test_trailing_whitespace_stripped(self):
        text = "Zeile mit Leerzeichen   \nNächste Zeile  "
        result = normalize_volltext(text)
        assert result == "Zeile mit Leerzeichen\nNächste Zeile"

    def test_angle_brackets_replaced_with_guillemets(self):
        text = "Kontakt: <poststelle@sm.bwl.de> für Anfragen"
        result = normalize_volltext(text)
        assert "<" not in result
        assert ">" not in result
        assert "\u2039poststelle@sm.bwl.de\u203a" in result

    def test_multiple_angle_brackets_replaced(self):
        text = "<Poststelle@lfdi.bwl.de> und <info@example.com>"
        result = normalize_volltext(text)
        assert result == "\u2039Poststelle@lfdi.bwl.de\u203a und \u2039info@example.com\u203a"

    def test_c1_control_chars_stripped(self):
        text = "Text\x81mit\x87Steuerzeichen\x89hier"
        result = normalize_volltext(text)
        assert "\x81" not in result
        assert "\x87" not in result
        assert "\x89" not in result
        assert "Textmit" in result  # chars removed, words joined

    def test_garbled_paragraphs_removed(self):
        """Mixed document: clean intro followed by garbled body."""
        clean = "Landtag von Baden-Württemberg\nDrucksache 17/4244"
        garbled = "6WlGWHWDJ%DGHQ\x81UWWHPEHUJ\x873RVWIDFK\x876WXWWJDUW"
        text = f"{clean}\n\n{garbled}"
        result = normalize_volltext(text)
        assert "Landtag" in result
        assert "6WlGWHWDJ" not in result

    def test_garbled_latin_extended_paragraphs_removed(self):
        clean = "Ein normaler deutscher Absatz mit korrektem Text."
        garbled = "ůĂƐƐĞŶ-ŵŝƚ-ǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶ-ƵŶĚ"
        text = f"{clean}\n\n{garbled}"
        result = normalize_volltext(text)
        assert "normaler" in result
        assert "ůĂƐƐĞŶ" not in result

    def test_empty_string_returns_empty(self):
        assert normalize_volltext("") == ""

    def test_unicode_nfkc_normalization(self):
        """NFKC should normalize compatibility characters."""
        # ﬁ (U+FB01 LATIN SMALL LIGATURE FI) → fi
        text = "Deﬁnition eines Begriffs"
        result = normalize_volltext(text)
        assert "Definition" in result

    def test_all_garbled_returns_empty(self):
        """If entire text is garbled, result should be empty or near-empty."""
        text = "6WlGWHWDJ%DGHQ\x81UWWHPEHUJ\x873RVWIDFK\x876WXWWJDUW"
        result = normalize_volltext(text)
        assert len(result.strip()) == 0 or "6WlGWHWDJ" not in result

    def test_replacement_character_removed(self):
        """U+FFFD replacement characters from encoding failures should be stripped."""
        text = "Ein \ufffd Text mit \ufffd Zeichen"
        result = normalize_volltext(text)
        assert "\ufffd" not in result

    def test_replacement_character_joins_word_fragments(self):
        """U+FFFD between word parts should be removed, joining the fragments."""
        text = "ausgezeich\ufffdnet"
        result = normalize_volltext(text)
        assert result == "ausgezeichnet"


# ---------------------------------------------------------------------------
# TestSanitizeLlmText (DD-027)
# ---------------------------------------------------------------------------


class TestSanitizeLlmText:
    """Tests for the LLM-output sanitizer that defends against the backend's
    XSS validator. Motivated by V-243670 in the staging run 2026-05-10/11:
    gpt-5-nano sporadically appends artefacts like ``</narrow>`` to its
    response despite the system prompt forbidding formatting.
    """

    def test_strips_trailing_narrow_artefact(self):
        text = "Der Landtag hat das Gesetz beschlossen.</narrow>"
        assert _sanitize_llm_text(text) == "Der Landtag hat das Gesetz beschlossen."

    def test_strips_inline_html_like_tags(self):
        text = "Foo <strong>bar</strong> baz"
        assert _sanitize_llm_text(text) == "Foo bar baz"

    def test_strips_self_closing_tag(self):
        assert _sanitize_llm_text("Zeile<br/>Umbruch") == "ZeileUmbruch"

    def test_clean_text_unchanged(self):
        text = "Eine Zusammenfassung ohne jede Formatierung."
        assert _sanitize_llm_text(text) == text

    def test_lone_brackets_replaced_with_guillemets(self):
        # Defense in depth: stray < or > that didn't form a tag still get
        # neutralised so the backend XSS validator never sees raw brackets.
        assert _sanitize_llm_text("x < 5 und y > 3") == "x \u2039 5 und y \u203a 3"

    def test_empty_input_returns_none(self):
        # An all-tag input becomes empty after stripping \u2192 return None so the
        # API client omits the field rather than sending an empty string.
        assert _sanitize_llm_text("<narrow></narrow>") is None
        assert _sanitize_llm_text("   ") is None
        assert _sanitize_llm_text("") is None

    def test_none_input_returns_none(self):
        assert _sanitize_llm_text(None) is None

    def test_pathological_long_tag_left_alone(self):
        # Tags longer than the 80-char regex cap fall through to the
        # bracket-substitution path, still neutralising the danger.
        text = "ok <" + "a" * 200 + "> done"
        result = _sanitize_llm_text(text)
        assert "<" not in result
        assert ">" not in result

    def test_strings_helper_drops_empty_results(self):
        assert _sanitize_llm_strings(["foo", "<narrow></narrow>", "bar"]) == ["foo", "bar"]

    def test_strings_helper_passes_through_clean_list(self):
        assert _sanitize_llm_strings(["umwelt", "klimaschutz"]) == ["umwelt", "klimaschutz"]

    def test_strings_helper_handles_none_and_empty(self):
        assert _sanitize_llm_strings(None) is None
        assert _sanitize_llm_strings([]) == []


class TestEnrichDokumentSanitization:
    """End-to-end: when the LLM returns </narrow> in zusammenfassung, the
    Dokument that enrich_dokument hands back must not contain it (DD-027)."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_narrow_artefact_stripped_from_zusammenfassung(self):
        """Reproduces the V-243670 staging failure: gpt-5-nano response
        ends with </narrow>, sanitizer removes it before Dokument construction."""
        polluted_response = json.dumps(
            {
                "schlagworte": ["umwelt"],
                "zusammenfassung": "Der Landtag hat das Gesetz beschlossen.</narrow>",
                "kurztitel": "Klima",
                "trojanergefahr": 3,
                "vorwort": "Ziel: Klimaschutz <hr/>",
            }
        )
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(polluted_response):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.zusammenfassung == "Der Landtag hat das Gesetz beschlossen."
        assert result.dokument.vorwort == "Ziel: Klimaschutz"
        # No raw brackets reach the API model \u2014 guards against the backend XSS validator.
        assert "<" not in result.dokument.zusammenfassung
        assert ">" not in result.dokument.zusammenfassung


# ---------------------------------------------------------------------------
# TestIsGarbled
# ---------------------------------------------------------------------------


class TestIsGarbled:
    def test_clean_german_text_not_garbled(self):
        text = (
            "Gemäß § 50a Absatz 2 der Geschäftsordnung habe ich im Einvernehmen "
            "mit den Antragstellern die Landesregierung gebeten, zu dem Gesetzentwurf "
            "der Fraktion FDP/DVP die Anhörung durchzuführen."
        )
        assert _is_garbled(text) is False

    def test_latin_extended_garbled_text_detected(self):
        """Latin Extended characters from broken ToUnicode CMap."""
        text = "ZĞ͗'ĞƐĞƚǌĞŶƚǁƵƌĨĚĞƌ&ƌĂŬƚŝŽŶĚĞƌ&WͬsWͲ'ĞƐĞƚǌǌƵƌtŝĞĚĞƌŚĞƌƐƚĞůůƵŶŐĚĞƌsĞƌďŝŶĚůŝĐŚŬĞŝƚĚĞƌ'ƌƵŶĚƐĐŚƵůĞŵƉĨĞŚůƵŶŐ"
        assert _is_garbled(text) is True

    def test_mixed_clean_and_garbled_detected(self):
        """Document with clean first page and garbled rest — overall garbled."""
        clean = "Landtag von Baden-Württemberg\n17. Wahlperiode\nDrucksache 17/4244\n" * 3
        garbled = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 20
        text = clean + garbled
        assert _is_garbled(text) is True

    def test_shifted_ascii_not_latin_extended(self):
        """ASCII shift garbling (ROT-29 type) doesn't have Latin Extended chars.

        This type is caught by normalize_volltext's paragraph quality scoring
        (C1 control chars + vowelless words), not by _is_garbled.
        """
        text = "6WlGWHWDJ%DGHQUWWHPEHUJ3RVWIDFK6WXWWJDUW )UDNWLRQGHU)'3'93]XU:LHGHUHLQIKUXQJ"
        assert _is_garbled(text) is False

    def test_empty_text_not_garbled(self):
        assert _is_garbled("") is False

    def test_short_text_not_garbled(self):
        """Very short text should not be flagged — too little signal."""
        assert _is_garbled("Kurz.") is False

    def test_text_with_few_umlauts_not_garbled(self):
        """German umlauts (U+00C4-U+00FC) are NOT Latin Extended, must not trigger."""
        text = "Änderung des Gesetzes für Straßenverkehr mit Ölprüfung und Übergabe"
        assert _is_garbled(text) is False

    def test_threshold_boundary(self):
        """Just below the garbling threshold should not be flagged."""
        # 95 clean ASCII alpha chars + 4 Latin Extended = 4/99 ≈ 4% (below 5%)
        clean = "a" * 95
        garbled = "ůĂƐŝ"  # 4 Latin Extended chars
        text = clean + garbled
        assert _is_garbled(text) is False


# ---------------------------------------------------------------------------
# Improvement 1: TestVorwortExtraction
# ---------------------------------------------------------------------------


class TestVorwortExtraction:
    """Entwurf prompt asks for vorwort, and it gets wired into the Dokument."""

    def test_entwurf_prompt_has_vorwort(self):
        prompt = _prompt_for_doktyp(Doktyp.ENTWURF)
        assert "vorwort" in prompt.lower() or "Vorwort" in prompt

    def test_preparl_entwurf_prompt_has_vorwort(self):
        prompt = _prompt_for_doktyp(Doktyp.PREPARL_MINUS_ENTWURF)
        assert "vorwort" in prompt.lower() or "Vorwort" in prompt

    def test_stellungnahme_prompt_has_no_vorwort(self):
        prompt = _prompt_for_doktyp(Doktyp.STELLUNGNAHME)
        assert "vorwort" not in prompt.lower()

    def test_generic_prompt_has_no_vorwort(self):
        prompt = _prompt_for_doktyp(Doktyp.MITTEILUNG)
        assert "vorwort" not in prompt.lower()

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_entwurf_enrichment_populates_vorwort(self):
        """LLM returns vorwort → Dokument.vorwort gets populated."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.vorwort == "Ziel dieses Gesetzentwurfs ist die Förderung erneuerbarer Energien."

    @pytest.mark.asyncio
    async def test_stellungnahme_has_no_vorwort(self):
        """Non-Entwurf types should not populate vorwort."""
        dok = _make_plain_dokument(typ=Doktyp.STELLUNGNAHME)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_STELLUNGNAHME):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.vorwort is None


# ---------------------------------------------------------------------------
# Improvement 2: TestJsonStructuredOutput
# ---------------------------------------------------------------------------


class TestJsonStructuredOutput:
    """extract_semantics uses response_format=json_object via litellm."""

    @pytest.mark.asyncio
    async def test_uses_response_format_json_object(self):
        """Verify litellm.acompletion is called with response_format."""
        with _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp:
            await extract_semantics(MagicMock(), SAMPLE_FULL_TEXT, Doktyp.ENTWURF)

        # Verify response_format was passed
        call_kwargs = mock_acomp.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_returns_parsed_json(self):
        """extract_semantics returns parsed dict from litellm response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = SAMPLE_LLM_RESPONSE_GENERIC

        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await extract_semantics(MagicMock(), SAMPLE_FULL_TEXT, Doktyp.MITTEILUNG)

        assert result["schlagworte"] == ["verwaltung", "reform"]
        assert result["kurztitel"] == "Verwaltungsreform"


# ---------------------------------------------------------------------------
# Improvement 3: TestValidateScores
# ---------------------------------------------------------------------------


class TestValidateScores:
    """Post-extraction validation of trojanergefahr (1-10) and meinung (1-5)."""

    def test_valid_trojanergefahr_unchanged(self):
        data = {"trojanergefahr": 5, "schlagworte": ["test"]}
        result = _validate_scores(data)
        assert result["trojanergefahr"] == 5

    def test_trojanergefahr_clamped_to_min(self):
        data = {"trojanergefahr": 0}
        result = _validate_scores(data)
        assert result["trojanergefahr"] == 1

    def test_trojanergefahr_clamped_to_max(self):
        data = {"trojanergefahr": 15}
        result = _validate_scores(data)
        assert result["trojanergefahr"] == 10

    def test_trojanergefahr_negative_clamped(self):
        data = {"trojanergefahr": -3}
        result = _validate_scores(data)
        assert result["trojanergefahr"] == 1

    def test_trojanergefahr_non_int_removed(self):
        data = {"trojanergefahr": "hoch", "schlagworte": ["test"]}
        result = _validate_scores(data)
        assert result.get("trojanergefahr") is None

    def test_valid_meinung_unchanged(self):
        data = {"meinung": 3}
        result = _validate_scores(data)
        assert result["meinung"] == 3

    def test_meinung_clamped_to_min(self):
        data = {"meinung": 0}
        result = _validate_scores(data)
        assert result["meinung"] == 1

    def test_meinung_clamped_to_max(self):
        data = {"meinung": 8}
        result = _validate_scores(data)
        assert result["meinung"] == 5

    def test_meinung_non_int_removed(self):
        data = {"meinung": "positiv"}
        result = _validate_scores(data)
        assert result.get("meinung") is None

    def test_both_scores_validated(self):
        data = {"trojanergefahr": 0, "meinung": 10}
        result = _validate_scores(data)
        assert result["trojanergefahr"] == 1
        assert result["meinung"] == 5

    def test_no_scores_present(self):
        data = {"schlagworte": ["test"], "zusammenfassung": "Test."}
        result = _validate_scores(data)
        assert result == data

    def test_float_trojanergefahr_truncated(self):
        data = {"trojanergefahr": 3.7}
        result = _validate_scores(data)
        assert result["trojanergefahr"] == 3


# ---------------------------------------------------------------------------
# Improvement 4: TestLLMMetrics
# ---------------------------------------------------------------------------


class TestLLMMetrics:
    """LLM enrichment metrics tracking."""

    def test_initial_state(self):
        m = LLMMetrics()
        assert m.success == 0
        assert m.failed == 0
        assert m.cache_hits == 0
        assert m.total == 0

    def test_total_computed(self):
        m = LLMMetrics()
        m.success = 5
        m.failed = 2
        m.cache_hits = 3
        assert m.total == 10

    def test_format_lines(self):
        m = LLMMetrics()
        m.success = 10
        m.failed = 1
        m.cache_hits = 3
        lines = m.format_lines()
        assert any("LLM" in line for line in lines)
        assert any("10" in line for line in lines)
        assert any("cache" in line.lower() for line in lines)

    def test_format_lines_includes_cache_hit_ratio(self):
        m = LLMMetrics()
        m.success = 3
        m.failed = 0
        m.cache_hits = 1  # 1 / 4 = 25%
        lines = m.format_lines()
        cache_line = next(line for line in lines if "Cache hits" in line)
        assert "25%" in cache_line

    def test_format_lines_ratio_omitted_when_no_calls(self):
        m = LLMMetrics()
        lines = m.format_lines()
        cache_line = next(line for line in lines if "Cache hits" in line)
        assert "%" not in cache_line

    def test_above_threshold_is_garbled(self):
        """Just above the threshold should be flagged."""
        # 80 clean ASCII alpha chars + 20 Latin Extended = 20/100 = 20% (above 5%)
        clean = "a" * 80
        garbled = "ůĂƐŝĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂ"  # 20 Latin Extended chars
        text = clean + garbled
        assert _is_garbled(text) is True


# ---------------------------------------------------------------------------
# TestExtractPdfTextOcrRetry
# ---------------------------------------------------------------------------


class TestExtractPdfTextOcrRetry:
    @pytest.mark.asyncio
    async def test_ocr_retry_on_garbled_text(self, tmp_path):
        """When normal extraction returns garbled text, should retry with OCR."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10
        clean_text = "Dies ist ein korrekter deutscher Text mit genügend Zeichen für den Test."

        garbled_result = _mock_extraction_result(content=garbled_text)
        ocr_result = _mock_extraction_result(content=clean_text)

        with patch(
            "bawue.bawue_dok.extract_file",
            new_callable=AsyncMock,
            side_effect=[garbled_result, ocr_result],
        ):
            text, _hash = await extract_pdf_text(pdf_file)

        assert text == clean_text
        assert "ůĂƐƐĞŶ" not in text

    @pytest.mark.asyncio
    async def test_ocr_retry_uses_german_language(self, tmp_path):
        """OCR retry must use language='deu' for proper German text extraction."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10
        clean_text = "Korrekter deutscher Volltext mit ausreichender Länge."

        garbled_result = _mock_extraction_result(content=garbled_text)
        ocr_result = _mock_extraction_result(content=clean_text)

        with patch(
            "bawue.bawue_dok.extract_file",
            new_callable=AsyncMock,
            side_effect=[garbled_result, ocr_result],
        ) as mock_extract:
            await extract_pdf_text(pdf_file)

        # Second call (OCR retry) should use force_ocr=True and language='deu'
        assert mock_extract.call_count == 2
        ocr_call_config = mock_extract.call_args_list[1].kwargs["config"]
        assert ocr_call_config.force_ocr is True
        assert ocr_call_config.ocr is not None
        assert ocr_call_config.ocr.language == "deu"

    @pytest.mark.asyncio
    async def test_clean_text_no_ocr_retry(self, tmp_path):
        """When normal extraction returns clean text, should NOT retry with OCR."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        clean_result = _mock_extraction_result(content=SAMPLE_FULL_TEXT)

        with patch(
            "bawue.bawue_dok.extract_file",
            new_callable=AsyncMock,
            return_value=clean_result,
        ) as mock_extract:
            text, _ = await extract_pdf_text(pdf_file)

        assert text == SAMPLE_FULL_TEXT
        assert mock_extract.call_count == 1

    @pytest.mark.asyncio
    async def test_ocr_failure_keeps_original_text(self, tmp_path):
        """If OCR retry also returns garbled text, keep original (normalize will strip)."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10

        garbled_result = _mock_extraction_result(content=garbled_text)
        still_garbled = _mock_extraction_result(content=garbled_text)

        with patch(
            "bawue.bawue_dok.extract_file",
            new_callable=AsyncMock,
            side_effect=[garbled_result, still_garbled],
        ):
            text, _ = await extract_pdf_text(pdf_file)

        # Original text returned when OCR doesn't improve it
        assert text == garbled_text

    @pytest.mark.asyncio
    async def test_ocr_exception_keeps_original_text(self, tmp_path):
        """If OCR retry raises an exception, keep original text."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10

        garbled_result = _mock_extraction_result(content=garbled_text)

        with patch(
            "bawue.bawue_dok.extract_file",
            new_callable=AsyncMock,
            side_effect=[garbled_result, RuntimeError("OCR failed")],
        ):
            text, _ = await extract_pdf_text(pdf_file)

        assert text == garbled_text

    @pytest.mark.asyncio
    async def test_short_text_triggers_ocr_before_garble_check(self, tmp_path):
        """Short text fallback still works — length check comes before garble check."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        short_result = _mock_extraction_result(content="Too short")
        ocr_result = _mock_extraction_result(content=SAMPLE_FULL_TEXT)

        with patch(
            "bawue.bawue_dok.extract_file",
            new_callable=AsyncMock,
            side_effect=[short_result, ocr_result],
        ):
            text, _ = await extract_pdf_text(pdf_file)

        assert text == SAMPLE_FULL_TEXT

    @pytest.mark.asyncio
    async def test_logs_garbled_detection(self, tmp_path, caplog):
        """Should log a warning when garbled text is detected."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10
        clean_text = "Korrekter Text mit ausreichender Länge für den Test hier."

        garbled_result = _mock_extraction_result(content=garbled_text)
        ocr_result = _mock_extraction_result(content=clean_text)

        with (
            patch(
                "bawue.bawue_dok.extract_file",
                new_callable=AsyncMock,
                side_effect=[garbled_result, ocr_result],
            ),
            caplog.at_level(logging.WARNING, logger="bawue.bawue_dok"),
        ):
            await extract_pdf_text(pdf_file)

        assert any("garbled" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# TestPageHintExtraction
# ---------------------------------------------------------------------------


class TestPageHintExtraction:
    def test_parse_page_hint_extracts_page_number(self):
        assert _parse_page_hint("https://www.landtag-bw.de/files/plp/17_141.pdf#page=33") == 33

    def test_parse_page_hint_no_fragment(self):
        assert _parse_page_hint("https://www.landtag-bw.de/files/plp/17_141.pdf") is None

    def test_parse_page_hint_non_page_fragment(self):
        assert _parse_page_hint("https://example.com/doc.pdf#section1") is None

    def test_extract_relevant_pages_finds_correct_section(self):
        """Pages outside start..start+max_pages should be excluded."""
        pages = []
        for i in range(1, 51):
            pages.append(f"\n\n<!-- PAGE {i} -->\n\nContent of page {i}")
        text = "Preamble" + "".join(pages)

        result = _extract_relevant_pages(text, start_page=33, max_pages=5)
        assert "Content of page 33" in result
        assert "Content of page 37" in result
        assert "Content of page 1" not in result
        assert "Content of page 40" not in result

    def test_extract_relevant_pages_no_markers_returns_full(self):
        """Without page markers, full text is returned as fallback."""
        text = "Plain text without any page markers at all."
        result = _extract_relevant_pages(text, start_page=33)
        assert result == text

    def test_extract_relevant_pages_page_beyond_end(self):
        """Page hint beyond document end falls back to full text."""
        pages = []
        for i in range(1, 11):
            pages.append(f"\n\n<!-- PAGE {i} -->\n\nContent of page {i}")
        text = "Preamble" + "".join(pages)

        result = _extract_relevant_pages(text, start_page=50)
        assert result == text


# ---------------------------------------------------------------------------
# TestClearHashCache
# ---------------------------------------------------------------------------


class TestClearHashCache:
    def test_clear_hash_cache_empties_dict(self):
        """clear_hash_cache() should empty the module-level _hash_cache."""
        _hash_cache["test_key"] = {"data": "value"}
        assert len(_hash_cache) == 1
        clear_hash_cache()
        assert len(_hash_cache) == 0


# ---------------------------------------------------------------------------
# TestRedisCacheIntegration
# ---------------------------------------------------------------------------


class TestRedisCacheIntegration:
    """Tests for two-tier (in-memory + Redis) cache in enrich_dokument."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_redis_cache_stores_on_llm_success(self):
        """After LLM call, semantics should be stored in Redis."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()
        cache = MagicMock()
        cache.get_raw.return_value = None

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            await enrich_dokument(session, llm, dok, cache=cache)

        cache.store_raw.assert_called_once()
        key = cache.store_raw.call_args[0][0]
        assert key.startswith("llm-semantics:")
        value = cache.store_raw.call_args[0][1]
        parsed = json.loads(value)
        assert "zusammenfassung" in parsed

    @pytest.mark.asyncio
    async def test_redis_cache_hit_skips_llm(self):
        """When Redis has cached semantics, LLM should not be called."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()
        cache = MagicMock()
        cache.get_raw.return_value = SAMPLE_LLM_RESPONSE_ENTWURF

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp:
            result = await enrich_dokument(session, llm, dok, cache=cache)

        mock_acomp.assert_not_called()
        assert result.dokument.zusammenfassung == "Ein Gesetzentwurf zur Förderung erneuerbarer Energien."

    @pytest.mark.asyncio
    async def test_in_memory_cache_takes_priority_over_redis(self):
        """In-memory cache should be checked before Redis."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()
        cache = MagicMock()

        # Pre-populate in-memory cache with the composite (doc_hash, prompt_hash) key
        _hash_cache[_cache_key(SAMPLE_HASH, _prompt_fingerprint(Doktyp.ENTWURF))] = json.loads(
            SAMPLE_LLM_RESPONSE_ENTWURF
        )

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp:
            result = await enrich_dokument(session, llm, dok, cache=cache)

        mock_acomp.assert_not_called()
        cache.get_raw.assert_not_called()
        assert result.dokument.zusammenfassung is not None

    @pytest.mark.asyncio
    async def test_redis_hit_populates_in_memory_cache(self):
        """Redis cache hit should populate the in-memory cache for fast subsequent lookups."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()
        cache = MagicMock()
        cache.get_raw.return_value = SAMPLE_LLM_RESPONSE_ENTWURF

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            await enrich_dokument(session, llm, dok, cache=cache)

        assert _cache_key(SAMPLE_HASH, _prompt_fingerprint(Doktyp.ENTWURF)) in _hash_cache

    @pytest.mark.asyncio
    async def test_no_cache_parameter_works(self):
        """enrich_dokument should work without cache parameter (backwards compatible)."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.zusammenfassung is not None

    @pytest.mark.asyncio
    async def test_metrics_counts_redis_cache_hit(self):
        """Redis cache hit should increment metrics.cache_hits."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()
        cache = MagicMock()
        cache.get_raw.return_value = SAMPLE_LLM_RESPONSE_ENTWURF
        metrics = LLMMetrics()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            await enrich_dokument(session, llm, dok, metrics=metrics, cache=cache)

        assert metrics.cache_hits == 1
        assert metrics.success == 0
