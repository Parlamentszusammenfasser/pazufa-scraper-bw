"""Tests for document enrichment: bawue_dok and pdf_text."""

import hashlib
import json
import logging
import re
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from bawue.bawue_dok import (
    BODY_PROMPT_BESCHLUSSEMPF,
    BODY_PROMPT_ENTWURF,
    BODY_PROMPT_REDEPROTOKOLL,
    BODY_PROMPT_REDEPROTOKOLL_TOP,
    ZUSAMMENFASSUNG_TEILE,
    EnrichmentResult,
    LLMMetrics,
    _cache_key,
    _hash_cache,
    _parse_llm_response,
    _prompt_fingerprint,
    _prompt_for_doktyp,
    _sanitize_llm_strings,
    _sanitize_llm_text,
    _validate_scores,
    clear_hash_cache,
    enrich_dokument,
    extract_semantics,
    narrow_to_relevant_section,
    zusammenfassung_text,
)
from bawue.pdf_text import (
    PdfNotAvailableError,
    _agenda_text,
    _extract_relevant_pages,
    _is_garbled,
    _paragraph_quality_score,
    _parse_page_hint,
    ausgegeben_datum,
    download_pdf,
    extract_pdf_text,
    normalize_volltext,
)
from bawue.types import UNSET, Autor, Doktyp, Dokument, Zusammenfassungstupel

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
        hash_="",
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


def _mock_session(status: int) -> MagicMock:
    """aiohttp-like session whose GET answers *status* with SAMPLE_PDF_BYTES."""
    response = AsyncMock()
    response.status = status
    response.read = AsyncMock(return_value=SAMPLE_PDF_BYTES)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    return session


class TestDownloadPdf:
    @pytest.mark.asyncio
    async def test_downloads_to_tempfile(self):
        """The #page=N fragment is stripped; certifi SSL context and a 60 s timeout."""
        session = _mock_session(200)

        path = await download_pdf(session, "https://www.landtag-bw.de/files/plp/17_141.pdf#page=33")
        try:
            assert path.suffix == ".pdf"
            assert path.read_bytes() == SAMPLE_PDF_BYTES
            args, kwargs = session.get.call_args
            assert args[0] == "https://www.landtag-bw.de/files/plp/17_141.pdf"
            assert isinstance(kwargs["ssl"], ssl.SSLContext)
            assert kwargs["timeout"].total == 60
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_http_error_raises_pdf_not_available_without_logging(self, caplog):
        """Issue #69: the raise carries status + URL; the caller logs it once."""
        session = _mock_session(404)

        with (
            pytest.raises(PdfNotAvailableError, match=r"HTTP 404: https://example.com/missing.pdf"),
            caplog.at_level(logging.WARNING, logger="bawue.pdf_text"),
        ):
            await download_pdf(session, "https://example.com/missing.pdf")

        assert caplog.records == []


# ---------------------------------------------------------------------------
# TestExtractPdfText
# ---------------------------------------------------------------------------


class TestExtractPdfText:
    @pytest.mark.asyncio
    async def test_extracts_text_and_hash(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        mock_result = _mock_extraction_result()
        with patch("bawue.pdf_text.extract_file", new_callable=AsyncMock, return_value=mock_result) as mock_extract:
            text, doc_hash = await extract_pdf_text(pdf_file)

        assert text == SAMPLE_FULL_TEXT
        assert doc_hash == SAMPLE_HASH
        assert mock_extract.call_count == 1  # clean text: no OCR retry

    @pytest.mark.asyncio
    async def test_extract_pdf_text_with_page_markers(self, tmp_path):
        """page_markers inserts kreuzberg's page markers and keeps every page: the caller
        cuts the #page=N window, so a protocol can still pick its agenda pages (issue #74)."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)
        marked_text = "".join(f"\n\n<!-- PAGE {i} -->\n\nContent of page {i}" for i in range(1, 40))

        mock_result = _mock_extraction_result(content=marked_text)
        with patch("bawue.pdf_text.extract_file", new_callable=AsyncMock, return_value=mock_result) as mock_extract:
            text, _ = await extract_pdf_text(pdf_file, page_markers=True)

        call_config = mock_extract.call_args_list[0].kwargs["config"]
        assert call_config.pages is not None
        assert call_config.pages.insert_page_markers is True
        assert text == marked_text

    @pytest.mark.asyncio
    async def test_ocr_fallback_on_short_text(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        short_result = _mock_extraction_result(content="Too short")
        ocr_result = _mock_extraction_result(content=SAMPLE_FULL_TEXT)

        with patch(
            "bawue.pdf_text.extract_file",
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

        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, return_value=mock_response) as acomp:
            result = await extract_semantics(llm, SAMPLE_FULL_TEXT, Doktyp.ENTWURF)

        assert acomp.call_args.kwargs["response_format"] == {"type": "json_object"}
        assert result["schlagworte"] == ["umwelt", "klimaschutz", "energie"]
        assert "Gesetzentwurf" in result["zusammenfassung"]
        assert result["kurztitel"] == "Erneuerbare-Energien-Gesetz"
        assert "Förderung" in result["vorwort"]

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
# TestExtractSemanticsNoTruncation — issue #32, root cause #1
# ---------------------------------------------------------------------------


async def _capture_llm_messages(coro_factory) -> list[dict]:
    """Run *coro_factory* with litellm.acompletion patched, capturing the messages."""
    captured: dict = {}

    async def _capture(*_args, **kwargs):
        captured["messages"] = kwargs["messages"]
        return _mock_llm_response(SAMPLE_LLM_RESPONSE_ENTWURF)

    with patch("bawue.bawue_dok.litellm.acompletion", new=_capture):
        await coro_factory()
    return captured["messages"]


class TestExtractSemanticsNoTruncation:
    """Issue #32, root cause #1: the *complete* document text must reach the LLM.

    The removed truncation (12 000-token cap) cut multi-topic plenary protocols
    mid-debate, so a later agenda item (the Fischereigesetz, Drucksache 17/529)
    was dropped while an earlier, unrelated debate survived in full — and the
    summary then described the wrong topic.
    """

    @pytest.mark.asyncio
    async def test_full_text_reaches_llm_untruncated(self):
        llm = _make_llm_mock()
        # A distinctive marker placed far past the old 12 000-token cut-off.
        marker = "FISCHEREIGESETZ_TAGESORDNUNGSPUNKT_MARKER"
        long_text = ("Debatte über ein völlig anderes Thema. " * 6000) + " " + marker

        messages = await _capture_llm_messages(lambda: extract_semantics(llm, long_text, Doktyp.REDEPROTOKOLL))

        user_msg = messages[-1]["content"]
        assert marker in user_msg, "the tail of a long protocol must not be truncated away"


class TestExtractSemanticsDocumentContext:
    """Issue #32, root cause #2: the prompt must identify which bill to summarize."""

    @pytest.mark.asyncio
    async def test_prompt_includes_drucksnr_and_titel(self):
        llm = _make_llm_mock()
        messages = await _capture_llm_messages(
            lambda: extract_semantics(
                llm,
                SAMPLE_FULL_TEXT,
                Doktyp.REDEPROTOKOLL,
                dok_titel="Gesetz zur Änderung des Fischereigesetzes",
                drucksnr="17/529",
            )
        )
        user_msg = messages[-1]["content"]
        assert "17/529" in user_msg, "Drucksachennummer must anchor the summary"
        assert "Fischereigesetz" in user_msg, "bill title must anchor the summary"

    @pytest.mark.asyncio
    async def test_context_omitted_when_no_identity_available(self):
        """Without titel/drucksnr the prompt is the plain body prompt (no dangling header)."""
        llm = _make_llm_mock()
        messages = await _capture_llm_messages(lambda: extract_semantics(llm, SAMPLE_FULL_TEXT, Doktyp.ENTWURF))
        assert SAMPLE_FULL_TEXT in messages[-1]["content"]


class TestPromptFingerprintContext:
    """Issue #32: two bills debated in the *same* protocol PDF share a file hash
    and doktyp, so the LLM-cache key must also vary by document identity — else
    the second bill silently gets the first bill's cached (wrong) summary."""

    def test_different_drucksnr_yield_different_fingerprints(self):
        a = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, drucksnr="17/529", titel="Fischereigesetz")
        b = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, drucksnr="17/1567", titel="Open-Data-Gesetz")
        assert a != b

    def test_same_identity_is_stable(self):
        a = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, drucksnr="17/529", titel="Fischereigesetz")
        b = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, drucksnr="17/529", titel="Fischereigesetz")
        assert a == b

    def test_same_title_different_vorgang_vnr_yield_different_fingerprints(self):
        """Issue #35 defense-in-depth: two Vorgänge sharing one protocol PDF stay on
        separate cache entries even when their titles coincide, because the
        initiating Drucksache (vorgang_vnr) differentiates them."""
        a = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, titel="Nachtragshaushalt", vorgang_vnr="17/400")
        b = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, titel="Nachtragshaushalt", vorgang_vnr="17/508")
        assert a != b

    def test_absent_vorgang_vnr_is_backward_compatible(self):
        """Omitting vorgang_vnr must not perturb the fingerprint."""
        a = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, drucksnr="17/529", titel="Fischereigesetz")
        b = _prompt_fingerprint(Doktyp.REDEPROTOKOLL, drucksnr="17/529", titel="Fischereigesetz", vorgang_vnr=None)
        assert a == b


# ---------------------------------------------------------------------------
# TestNarrowToRelevantSection — issue #32, corelib section extraction
# ---------------------------------------------------------------------------


def _make_section_llm_mock(section_return="RELEVANTER ABSCHNITT ÜBER FISCHEREI"):
    """LLMConnector mock with a stubbed extract_relevant_section."""
    llm = _make_llm_mock()
    llm.extract_relevant_section = AsyncMock(return_value=section_return)
    return llm


class TestNarrowToRelevantSection:
    """The corelib section extractor runs only for REDEPROTOKOLL, and always
    degrades to the full window (issue #32)."""

    WINDOW = "Punkt 3 Open Data … Punkt 4 Fischereigesetz Drucksache 17/529 …"

    @pytest.mark.asyncio
    async def test_narrows_redeprotokoll(self):
        llm = _make_section_llm_mock()
        out = await narrow_to_relevant_section(llm, self.WINDOW, Doktyp.REDEPROTOKOLL, "Fischereigesetz", "17/529")
        assert out == "RELEVANTER ABSCHNITT ÜBER FISCHEREI"
        llm.extract_relevant_section.assert_awaited_once_with(self.WINDOW, "Fischereigesetz", vorgang_vnr="17/529")

    @pytest.mark.asyncio
    async def test_skips_non_redeprotokoll(self):
        llm = _make_section_llm_mock()
        out = await narrow_to_relevant_section(llm, self.WINDOW, Doktyp.ENTWURF, "Ein Gesetz", "17/529")
        assert out == self.WINDOW
        llm.extract_relevant_section.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_when_no_section_found(self):
        llm = _make_section_llm_mock(section_return=None)
        out = await narrow_to_relevant_section(llm, self.WINDOW, Doktyp.REDEPROTOKOLL, "Fischereigesetz", "17/529")
        assert out == self.WINDOW

    @pytest.mark.asyncio
    async def test_falls_back_on_extractor_error(self):
        llm = _make_section_llm_mock()
        llm.extract_relevant_section = AsyncMock(side_effect=RuntimeError("provider down"))
        out = await narrow_to_relevant_section(llm, self.WINDOW, Doktyp.REDEPROTOKOLL, "Fischereigesetz", "17/529")
        assert out == self.WINDOW

    @pytest.mark.asyncio
    async def test_skips_when_no_title(self):
        llm = _make_section_llm_mock()
        out = await narrow_to_relevant_section(llm, self.WINDOW, Doktyp.REDEPROTOKOLL, "  ", "17/529")
        assert out == self.WINDOW
        llm.extract_relevant_section.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enrich_no_longer_narrows_redeprotokoll(self):
        """Issue #49 supersedes issue #32/#35's per-bill narrowing: the backend
        stores one Dokument row per shared REDEPROTOKOLL PDF, so a per-bill
        narrowed section can never survive multiple bills' uploads intact —
        whichever bill is scraped last silently overwrites the others. enrich_dokument
        must no longer call the corelib section extractor for REDEPROTOKOLL, and the
        full window must reach the summarizer unmodified."""
        _hash_cache.clear()
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL)
        llm = _make_section_llm_mock(section_return="NUR DIE FISCHEREI-DEBATTE")
        session = MagicMock()
        captured: dict = {}

        async def _capture(*_args, **kwargs):
            captured["messages"] = kwargs["messages"]
            return _mock_llm_response(SAMPLE_LLM_RESPONSE_GENERIC)

        with (
            _patch_pdf_pipeline(text_and_hash=("Voller Protokolltext mit mehreren Themen", "d" * 64)),
            patch("bawue.bawue_dok.litellm.acompletion", new=_capture),
        ):
            await enrich_dokument(session, llm, dok, vorgang_titel="Fischereigesetz", vorgang_vnr="17/529")

        llm.extract_relevant_section.assert_not_awaited()
        user_msg = captured["messages"][-1]["content"]
        assert "Voller Protokolltext mit mehreren Themen" in user_msg
        assert "NUR DIE FISCHEREI-DEBATTE" not in user_msg
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_enrich_omits_bill_context_for_redeprotokoll(self):
        """Issue #49: no bill title/Drucksache anchor reaches the REDEPROTOKOLL
        prompt — the summary must stay neutral, not narrowed to one Vorgang."""
        _hash_cache.clear()
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL)
        llm = _make_section_llm_mock()
        session = MagicMock()
        captured: dict = {}

        async def _capture(*_args, **kwargs):
            captured["messages"] = kwargs["messages"]
            return _mock_llm_response(SAMPLE_LLM_RESPONSE_GENERIC)

        with (
            _patch_pdf_pipeline(text_and_hash=("Voller Protokolltext mit mehreren Themen", "e" * 64)),
            patch("bawue.bawue_dok.litellm.acompletion", new=_capture),
        ):
            await enrich_dokument(session, llm, dok, vorgang_titel="Fischereigesetz", vorgang_vnr="17/529")

        llm.extract_relevant_section.assert_not_awaited()
        user_msg = captured["messages"][-1]["content"]
        assert "Fischereigesetz" not in user_msg
        assert "17/529" not in user_msg
        assert "KONTEXT" not in user_msg
        _hash_cache.clear()


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

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param("not json at all", id="garbage"),  # json-repair returns ""
            pytest.param('"a", "b", "c"', id="array"),  # a list must not reach _validate_scores
        ],
    )
    def test_raises_when_repair_yields_no_dict(self, content):
        with pytest.raises(json.JSONDecodeError):
            _parse_llm_response(content)


# ---------------------------------------------------------------------------
# TestPromptForDoktyp
# ---------------------------------------------------------------------------


class TestPromptForDoktyp:
    @pytest.mark.parametrize(
        ("doktyp", "vorwort", "meinung"),
        [
            (Doktyp.ENTWURF, True, False),
            (Doktyp.STELLUNGNAHME, False, True),
            (Doktyp.BESCHLUSSEMPF, False, True),
            (Doktyp.REDEPROTOKOLL, False, False),
            (Doktyp.SONSTIG, False, False),
            (Doktyp.MITTEILUNG, False, False),
        ],
    )
    def test_prompt_asks_for_the_doktyp_fields(self, doktyp, vorwort, meinung):
        prompt = _prompt_for_doktyp(doktyp).lower()
        assert "schlagworte" in prompt
        assert ("vorwort" in prompt) is vorwort
        assert ("meinung" in prompt) is meinung

    def test_preparl_entwurf_uses_entwurf_prompt(self):
        assert _prompt_for_doktyp(Doktyp.PREPARL_ENTWURF) == _prompt_for_doktyp(Doktyp.ENTWURF)

    def test_redeprotokoll_uses_dedicated_neutral_prompt(self):
        """Issue #49: REDEPROTOKOLL gets its own prompt (not the generic one),
        asking for a session-level summary rather than a single-bill one."""
        prompt = _prompt_for_doktyp(Doktyp.REDEPROTOKOLL)
        assert "meinung" not in prompt.lower()
        assert prompt != _prompt_for_doktyp(Doktyp.SONSTIG)


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
    async def test_full_enrichment(self, caplog):
        """PDF + LLM succeed (no Redis cache) → all fields populated, PARLIS metadata kept."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with (
            _patch_pdf_pipeline(),
            _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp,
            caplog.at_level(logging.INFO, logger="bawue.bawue_dok"),
        ):
            result = await enrich_dokument(session, llm, dok)

        assert isinstance(result, EnrichmentResult)
        assert mock_acomp.call_count == 1
        assert any("token" in r.message.lower() for r in caplog.records)
        assert result.dokument.volltext == SAMPLE_FULL_TEXT
        assert result.dokument.hash_ == SAMPLE_HASH
        # Issue #42 (DD-054): sent as the typed tuple, not a plain string.
        assert result.dokument.to_dict()["zusammenfassung"] == [
            {"typ": "full-llm", "inhalt": "Ein Gesetzentwurf zur Förderung erneuerbarer Energien."}
        ]
        assert result.dokument.schlagworte == ["umwelt", "klimaschutz", "energie"]
        assert result.dokument.kurztitel == "Erneuerbare-Energien-Gesetz"
        assert result.dokument.vorwort == "Ziel dieses Gesetzentwurfs ist die Förderung erneuerbarer Energien."
        # PARLIS metadata (titel, autoren, drucksnr, timestamps) must not change.
        assert result.dokument.titel == "Testgesetz"
        assert result.dokument.autoren[0].person == "Max Mustermann"
        assert result.dokument.drucksnr == "17/10266"
        assert result.dokument.zp_modifiziert == datetime(2026, 1, 15, tzinfo=UTC)
        assert result.dokument.zp_referenz == datetime(2026, 1, 15, tzinfo=UTC)
        assert result.dokument.typ == Doktyp.ENTWURF

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("doktyp", "response", "meinung"),
        [
            (Doktyp.STELLUNGNAHME, SAMPLE_LLM_RESPONSE_STELLUNGNAHME, 4),
            (Doktyp.BESCHLUSSEMPF, SAMPLE_LLM_RESPONSE_BESCHLUSSEMPF, 5),
        ],
    )
    async def test_gets_meinung_but_no_vorwort(self, doktyp, response, meinung):
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument(typ=doktyp))

        assert result.dokument.meinung == meinung
        assert result.dokument.vorwort is None

    @pytest.mark.asyncio
    async def test_text_only_fallback_on_llm_failure(self):
        """LLM fails → volltext+hash set, no LLM fields."""
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
        assert result.dokument.hash_ == SAMPLE_HASH
        # No LLM fields
        assert result.dokument.zusammenfassung is UNSET
        assert result.dokument.schlagworte is UNSET

    @pytest.mark.asyncio
    async def test_enrich_dokument_with_page_hint(self):
        """URL with #page=N: fragment stripped for download, page markers requested from extraction, link preserved."""
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
        # An anchor asks for page markers; enrich cuts the window itself (issue #74)
        mock_extract.assert_called_once_with(Path("/tmp/fake.pdf"), page_markers=True)
        # dok.link preserves the original URL with fragment
        assert result.dokument.link == page_url

    @pytest.mark.asyncio
    async def test_page_anchored_siblings_get_distinct_hashes(self):
        """Issue #72: two non-protocol documents packed into one shared PDF but
        anchored to different pages (same file → same file digest) must get
        distinct ``hash_`` values, so the backend keeps both as separate rows
        instead of collapsing them by hash."""
        base = "https://www.landtag-bw.de/resource/blob/3/17_4495.pdf"
        dok1 = _make_plain_dokument(typ=Doktyp.ENTWURF, link=f"{base}#page=1")
        dok5 = _make_plain_dokument(typ=Doktyp.ENTWURF, link=f"{base}#page=5")
        session = MagicMock()
        llm = _make_llm_mock()

        # Both docs extract to the *same* file digest (same underlying PDF).
        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF):
            r1 = await enrich_dokument(session, llm, dok1)
            r5 = await enrich_dokument(session, llm, dok5)

        assert r1.dokument.hash_ != r5.dokument.hash_
        # Neither equals the raw file digest — the anchor is folded in.
        assert r1.dokument.hash_ != SAMPLE_HASH
        assert r5.dokument.hash_ != SAMPLE_HASH

    @pytest.mark.asyncio
    async def test_redeprotokoll_page_anchor_also_changes_hash(self):
        """Issue #25: a REDEPROTOKOLL is no exception to the rule above.

        The protocol PDF is shared across the bills debated in a sitting (DD-036),
        but each cites *its own* ``#page=N`` window, and ``titel``/``link``/``autoren``
        describe that window — so the anchor belongs in the row identity here too.
        Only the semantics cache stays on the plain file digest (issue #49); see
        TestPerVorgangProtocolRows.
        """
        base = "https://www.landtag-bw.de/files/plp/17_141.pdf"
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, link=f"{base}#page=33")
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.hash_ != SAMPLE_HASH

    @pytest.mark.asyncio
    async def test_metadata_only_fallback_on_download_failure(self):
        """PDF download fails → original Dokument unchanged."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = AsyncMock()

        with patch("bawue.bawue_dok.download_pdf", new_callable=AsyncMock, side_effect=Exception("Download failed")):
            result = await enrich_dokument(session, llm, dok)

        # Original document returned unchanged
        assert result.dokument.volltext == ""
        assert result.dokument.hash_ == ""
        assert result.dokument.zusammenfassung is UNSET

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
        mock_llm.assert_not_called()
        # A permanent content problem, not a retryable download failure: it must not
        # keep the Vorgang out of the cache forever.
        assert result.download_failed is False

    @pytest.mark.asyncio
    async def test_download_failure_sets_download_failed_flag(self):
        """Issue #66 follow-up: a failed PDF download is retryable (WP18
        Plenarprotokolle are published weeks after the session, e.g.
        18_0007_01072026.pdf still 404s), so the result must be flagged —
        the scraper then skips caching the Vorgang and retries next cycle."""
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL)
        session = MagicMock()
        llm = AsyncMock()

        failure = PdfNotAvailableError(404, "https://example.com/plp.pdf")
        with patch("bawue.bawue_dok.download_pdf", new_callable=AsyncMock, side_effect=failure):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument is dok
        assert result.download_failed is True

    @pytest.mark.asyncio
    async def test_issue69_unpublished_pdf_logs_one_warning_without_traceback(self, caplog):
        """Issue #69: an unpublished PDF (HTTP 404) is expected, so it logs exactly
        one WARNING with status + URL and no traceback (Cloud Run tags it ERROR)."""
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, link="https://example.com/plp.pdf")

        with caplog.at_level(logging.WARNING, logger="bawue.bawue_dok"):
            result = await enrich_dokument(_mock_session(404), AsyncMock(), dok)

        assert result.download_failed is True
        assert result.http_status == 404  # not published: the weekly report lists it (issue #128)
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        assert record.exc_info is None
        assert "HTTP 404: https://example.com/plp.pdf" in record.getMessage()

    @pytest.mark.asyncio
    async def test_unexpected_download_error_keeps_traceback(self, caplog):
        """Network errors and timeouts are unexpected — they keep the stack (issue #69)."""
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL)

        with (
            patch("bawue.bawue_dok.download_pdf", new_callable=AsyncMock, side_effect=aiohttp.ClientError("boom")),
            caplog.at_level(logging.WARNING, logger="bawue.bawue_dok"),
        ):
            result = await enrich_dokument(MagicMock(), AsyncMock(), dok)

        assert result.download_failed is True
        assert result.http_status is None  # transient: not reported as missing (issue #128)
        assert len(caplog.records) == 1
        assert caplog.records[0].exc_info is not None

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
# TestAusgegebenDatum — issue #23
# ---------------------------------------------------------------------------

# Real page-1 layouts from staging / local extraction. The footer with the issue date
# lands anywhere relative to the "Drucksache WP / Nr" header.
PARLIS_17_1102 = datetime(2021, 11, 25, tzinfo=UTC)  # Ausschuss session, Fundstelle date
TEXT_17_1102_NATIVE = (
    "Landtag von Baden-Württemberg\n17. Wahlperiode\nDrucksache 17 / 1102\n"
    "Ausgegeben: 10.12.2021 1\nBeschlussempfehlung\nDer Landtag wolle beschließen:\n"
)
TEXT_17_1102_OCR = (
    "Landtag von Baden-Württemberg Drucksache 17 / 1102\n17. Wahlperiode\n\n"
    "Beschlussempfehlung und Bericht\n\ndes Ausschusses für Finanzen\n\n"
    "Der Landtag wolle beschließen:\n\nAusgegeben: 10.12.2021 1\n\n"
    "Drucksachen und Plenarprotokolle sind im Internet"
)
# Anträge/Entwürfe: "Eingegangen … / Ausgegeben …" footer *before* the header (17/1026).
TEXT_17_1026_FOOTER_FIRST = (
    "Landtag von Baden-Württemberg\n17. Wahlperiode\n1\n"
    "Drucksachen und Plenarprotokolle sind im Internet\n… digitaler \n"
    "Eingegangen: 19.10.2021/Ausgegeben: 20.10.2021\nDrucksache 17 / 1026\n19.10.2021\n"
)


class TestAusgegebenDatum:
    """Issue #23: the issue date printed in a Drucksache's page-1 footer."""

    @pytest.mark.parametrize("text", [TEXT_17_1102_NATIVE, TEXT_17_1102_OCR])
    def test_issue23_drucksache_17_1102_native_and_ocr_layout(self, text):
        assert ausgegeben_datum(text, "17/1102", PARLIS_17_1102) == datetime(2021, 12, 10, tzinfo=UTC)

    def test_issue23_eingegangen_footer_before_header_17_1026(self):
        parlis = datetime(2021, 10, 19, tzinfo=UTC)
        assert ausgegeben_datum(TEXT_17_1026_FOOTER_FIRST, "17/1026", parlis) == datetime(2021, 10, 20, tzinfo=UTC)

    def test_issue23_single_digit_day_17_1104(self):
        text = "Drucksache 17 / 1104\nAusgegeben: 9.12.2021 1\n"
        parlis = datetime(2021, 11, 26, tzinfo=UTC)
        assert ausgegeben_datum(text, "17/1104", parlis) == datetime(2021, 12, 9, tzinfo=UTC)

    def test_issue23_without_footer_returns_none(self):
        assert ausgegeben_datum("Drucksache 17 / 1102\n" + SAMPLE_FULL_TEXT, "17/1102", PARLIS_17_1102) is None

    def test_issue23_without_own_header_returns_none(self):
        """No header of its own: not this Drucksache's PDF (e.g. a protocol or a quote)."""
        text = "Drucksache 17 / 999\nAusgegeben: 10.12.2021"
        assert ausgegeben_datum(text, "17/1102", PARLIS_17_1102) is None

    @pytest.mark.parametrize("drucksnr", ["17/110", "17/11020", "7/1102", "117/1102"])
    def test_issue23_number_must_match_exactly(self, drucksnr):
        assert ausgegeben_datum(TEXT_17_1102_NATIVE, drucksnr, PARLIS_17_1102) is None

    @pytest.mark.parametrize("drucksnr", [None, "", "17-1102", "17/1102.*", ".*/.*"])
    def test_issue23_missing_or_malformed_drucksnr_returns_none(self, drucksnr):
        assert ausgegeben_datum(TEXT_17_1102_NATIVE, drucksnr, PARLIS_17_1102) is None

    @pytest.mark.parametrize(
        "footer",
        [
            "Ausgegeben: 31.02.2021",  # impossible date
            "Ausgegeben: 24.11.2021",  # before the PARLIS date
            "Ausgegeben: 24.2.2022",  # 91 days after it
            "Ausgegeben: 10.12.2027",  # OCR-misread year
        ],
    )
    def test_issue23_implausible_date_is_dropped_with_warning(self, footer, caplog):
        with caplog.at_level(logging.WARNING, logger="bawue.pdf_text"):
            assert ausgegeben_datum(f"Drucksache 17 / 1102\n{footer}", "17/1102", PARLIS_17_1102) is None
        assert "17/1102" in caplog.text

    @pytest.mark.parametrize("days", [0, 90])
    def test_issue23_plausibility_window_is_inclusive(self, days):
        datum = PARLIS_17_1102 + timedelta(days=days)
        text = f"Drucksache 17 / 1102\nAusgegeben: {datum.day}.{datum.month}.{datum.year}"
        assert ausgegeben_datum(text, "17/1102", PARLIS_17_1102) == datum

    def test_issue23_five_digit_year_does_not_match(self):
        assert ausgegeben_datum("Drucksache 17 / 1102\nAusgegeben: 10.12.20219", "17/1102", PARLIS_17_1102) is None


class TestEnrichDokumentAusgegebenDatum:
    """Issue #23: zp_modifiziert is the printed issue date; zp_referenz stays the PARLIS
    session date and zp_erstellt stays unset (the Eingang, not the issue, is the creation)."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @staticmethod
    def _beschlussempf(link: str = "https://www.landtag-bw.de/17_1102_D.pdf") -> Dokument:
        dok = _make_plain_dokument(typ=Doktyp.BESCHLUSSEMPF, link=link)
        dok.drucksnr = "17/1102"
        dok.zp_referenz = dok.zp_modifiziert = PARLIS_17_1102
        return dok

    @pytest.mark.asyncio
    @pytest.mark.parametrize("link", ["https://www.landtag-bw.de/17_1102_D.pdf", "https://x.de/17_1102_D.pdf#page=2"])
    async def test_issue23_drucksache_17_1102(self, link):
        with (
            _patch_pdf_pipeline((TEXT_17_1102_NATIVE, SAMPLE_HASH)),
            _patch_llm(SAMPLE_LLM_RESPONSE_BESCHLUSSEMPF),
        ):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), self._beschlussempf(link))

        assert result.dokument.zp_modifiziert == datetime(2021, 12, 10, tzinfo=UTC)
        assert result.dokument.zp_referenz == PARLIS_17_1102
        assert result.dokument.zp_erstellt is UNSET

    @pytest.mark.asyncio
    async def test_issue23_text_only_fallback_also_gets_the_date(self):
        llm_fail = patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, side_effect=Exception("down"))
        with _patch_pdf_pipeline((TEXT_17_1102_NATIVE, SAMPLE_HASH)), llm_fail:
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), self._beschlussempf())

        assert result.dokument.zp_modifiziert == datetime(2021, 12, 10, tzinfo=UTC)


# ---------------------------------------------------------------------------
# TestSharedRedeprotokollNeutralSummary — issue #49
# ---------------------------------------------------------------------------


class TestSharedRedeprotokollNeutralSummary:
    """Issue #49: Plp 17/0107 links 5 bills (Kita, Privatschulgesetz, 5. HRÄG,
    Gemeindeordnung, Haushaltsbegleitgesetz 2025/2026); the backend stores one
    Dokument row per PDF hash, so only the last-uploaded bill's per-bill
    narrowed summary survived — reproduced against real WP17 dev data, where
    the shared row's zusammenfassung described only the Kita bill.

    The fix makes the REDEPROTOKOLL LLM-semantics cache key collapse to
    (file_hash, doktyp) — identical for every bill sharing the PDF — so the
    first bill enriched "wins" the cache entry and every other bill reuses it
    verbatim instead of computing (and then overwriting with) its own
    bill-narrowed version.

    Issue #25 later split the *row* per Fundstelle (the anchored ``hash_``), which
    is why the bills below no longer share a hash. The cache key stayed on the
    plain file digest, so the guarantee this class pins is unchanged: one summary
    per protocol PDF, whatever the anchor.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @pytest.mark.asyncio
    async def test_two_bills_sharing_one_protocol_get_identical_summary(self):
        session = MagicMock()
        llm = _make_llm_mock()
        shared_hash = SAMPLE_HASH

        # Both bills cite Plp 17/0107, each with its own page window (issue #25).
        plp = "https://www.landtag-bw.de/files/plp/17_0107.pdf"
        dok_kita = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, titel="Zweite Beratung", link=f"{plp}#page=19")
        dok_privatschule = _make_plain_dokument(
            typ=Doktyp.REDEPROTOKOLL, titel="Zweite Beratung", link=f"{plp}#page=42"
        )

        with (
            _patch_pdf_pipeline(text_and_hash=("Fenster um die Kita-Debatte", shared_hash)),
            _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC),
        ):
            result_kita = await enrich_dokument(
                session,
                llm,
                dok_kita,
                vorgang_titel="Gesetz zur Änderung des Kindertagesbetreuungsgesetzes",
                vorgang_vnr="17/6789",
            )

        # Second bill: different title/Drucksache and (simulating a different
        # #page=N window of the same PDF) different extracted text, but the
        # identical PDF hash — this is what the backend dedups on.
        wrong_topic_response = json.dumps(
            {
                "schlagworte": ["privatschule"],
                "zusammenfassung": "FALSCHES THEMA: Privatschulgesetz-spezifische Zusammenfassung.",
                "kurztitel": "Privatschulgesetz",
            }
        )
        with (
            _patch_pdf_pipeline(text_and_hash=("Anderes Seitenfenster desselben Protokolls", shared_hash)),
            _patch_llm(wrong_topic_response),
        ):
            result_privatschule = await enrich_dokument(
                session,
                llm,
                dok_privatschule,
                vorgang_titel="Gesetz zur Änderung des Privatschulgesetzes",
                vorgang_vnr="17/1234",
            )

        # Separate rows (issue #25), one shared summary (issue #49).
        assert result_privatschule.dokument.hash_ != result_kita.dokument.hash_
        assert result_privatschule.dokument.zusammenfassung == result_kita.dokument.zusammenfassung
        assert result_privatschule.dokument.schlagworte == result_kita.dokument.schlagworte
        assert result_privatschule.dokument.kurztitel == result_kita.dokument.kurztitel
        assert "FALSCHES THEMA" not in zusammenfassung_text(result_privatschule.dokument)


# ---------------------------------------------------------------------------
# TestPerVorgangProtocolRows — issue #25
# ---------------------------------------------------------------------------


class TestPerVorgangProtocolRows:
    """Issue #25: `titel`, `link` and `autoren` are per-Vorgang Fundstelle metadata,
    so a shared REDEPROTOKOLL row cannot hold them — whichever Vorgang was uploaded
    last won, and every sibling showed a wrong reading label and a deep link into an
    unrelated debate.

    Ground truth (verified against PARLIS and the PDF): Plenarprotokoll 17/137
    (10.12.2025) is cited by 10 Vorgänge, among them

      * V-244180 „Gesetz zur Änderung des Juristenausbildungsgesetzes",
        Erste Beratung → `#page=70` (PDF page 70 names the Juristenausbildungsgesetz)
      * V-243384 „Gesetz zur Änderung schulgesetzlicher Regelungen",
        Zweite Beratung → `#page=31` (PDF page 31 is the Schulgesetz debate)

    The staging row for V-244180 held V-243384's values (`titel: "Zweite Beratung"`,
    `#page=31`). The fix folds the page anchor into `hash_` for REDEPROTOKOLL too, so
    each Fundstelle becomes its own row; only the LLM semantics cache stays keyed on
    the plain file digest (issue #49, covered by the sibling test below).

    See tests/integration/test_issue25_shared_protocol_rows.py for the same case
    driven from the real PARLIS Fundstellen and the real PDF.
    """

    PLP_17_137 = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP17/Plp/17_0137_10122025.pdf"

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    async def _enrich_pair(self, llm_json=SAMPLE_LLM_RESPONSE_GENERIC):
        """Enrich the two real Plp 17/137 Fundstellen off one shared PDF digest."""
        juristen = _make_plain_dokument(
            typ=Doktyp.REDEPROTOKOLL, titel="Erste Beratung", link=f"{self.PLP_17_137}#page=70"
        )
        schulgesetz = _make_plain_dokument(
            typ=Doktyp.REDEPROTOKOLL, titel="Zweite Beratung", link=f"{self.PLP_17_137}#page=31"
        )
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(llm_json) as mock_llm:
            r_juristen = await enrich_dokument(
                session, llm, juristen, vorgang_titel="Gesetz zur Änderung des Juristenausbildungsgesetzes"
            )
            r_schulgesetz = await enrich_dokument(
                session, llm, schulgesetz, vorgang_titel="Gesetz zur Änderung schulgesetzlicher Regelungen"
            )

        return r_juristen, r_schulgesetz, mock_llm

    @pytest.mark.asyncio
    async def test_two_vorgaenge_sharing_one_protocol(self):
        """Two Fundstellen of one sitting: own rows with own metadata, but one summary."""
        r_juristen, r_schulgesetz, mock_llm = await self._enrich_pair()

        # Distinct rows; neither is the plain file digest, the anchor is folded in.
        assert r_juristen.dokument.hash_ != r_schulgesetz.dokument.hash_
        assert SAMPLE_HASH not in (r_juristen.dokument.hash_, r_schulgesetz.dokument.hash_)
        # titel, link and autoren stay with the Vorgang that produced them.
        assert r_juristen.dokument.titel == "Erste Beratung"
        assert r_juristen.dokument.link.endswith("#page=70")
        assert r_schulgesetz.dokument.titel == "Zweite Beratung"
        assert r_schulgesetz.dokument.link.endswith("#page=31")
        assert r_juristen.dokument.autoren == r_schulgesetz.dokument.autoren  # same fixture author
        # Issue #49 must not regress: the semantics cache stays keyed on the plain file
        # digest, so the second Fundstelle reuses the first one's session-level summary.
        assert mock_llm.await_count == 1
        assert r_schulgesetz.dokument.zusammenfassung == r_juristen.dokument.zusammenfassung
        assert r_schulgesetz.dokument.schlagworte == r_juristen.dokument.schlagworte
        assert r_schulgesetz.dokument.kurztitel == r_juristen.dokument.kurztitel

    @pytest.mark.asyncio
    async def test_identical_fundstelle_still_shares_one_row(self):
        """DD-043's dedup intent survives: same PDF *and* same anchor → one row.

        Two Vorgänge whose Fundstellen point at the same page of the same protocol
        really are the same reference and must keep collapsing into one row.
        """
        link = f"{self.PLP_17_137}#page=70"
        dok_a = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, titel="Erste Beratung", link=link)
        dok_b = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, titel="Erste Beratung", link=link)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC):
            r_a = await enrich_dokument(session, llm, dok_a)
            r_b = await enrich_dokument(session, llm, dok_b)

        assert r_a.dokument.hash_ == r_b.dokument.hash_

    @pytest.mark.asyncio
    async def test_unanchored_protocol_keeps_plain_file_digest(self):
        """Without a `#page=N` anchor there is nothing per-Vorgang to preserve.

        The „Beschluss des Landtags in Zweiter Beratung" Drucksachen map to
        REDEPROTOKOLL (enum_mapper) and are cited without an anchor by many
        Vorgänge — genuinely one document, so the row stays shared.
        """
        dok = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, link=self.PLP_17_137)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC):
            result = await enrich_dokument(session, llm, dok)

        assert result.dokument.hash_ == SAMPLE_HASH


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
    async def test_cache_hit_skips_llm(self, caplog):
        """Second call with same PDF hash reuses cached semantics, no LLM call, logs the hit."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with (
            _patch_pdf_pipeline(),
            _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp,
            caplog.at_level(logging.INFO, logger="bawue.bawue_dok"),
        ):
            first = await enrich_dokument(session, llm, dok)
            second = await enrich_dokument(session, llm, dok)

        # LLM called only once despite two enrichments
        assert mock_acomp.call_count == 1
        assert any("cache hit" in r.message.lower() for r in caplog.records)
        # Both results have the same semantics
        assert first.dokument.zusammenfassung == second.dokument.zusammenfassung
        assert first.dokument.schlagworte == second.dokument.schlagworte
        assert first.dokument.kurztitel == second.dokument.kurztitel

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
        (with vorwort) instead of running the Stellungnahme prompt (with meinung).
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


# ---------------------------------------------------------------------------
# TestParagraphQualityScore
# ---------------------------------------------------------------------------


class TestParagraphQualityScore:
    @pytest.mark.parametrize(
        "text",
        [
            "Gemäß § 50a Absatz 2 der Geschäftsordnung habe ich im Einvernehmen mit den Antragstellern",
            "\u00c4nderung des Schulgesetzes f\u00fcr Baden-W\u00fcrttemberg - Drucksache 17/4142",
            "Landtag von Baden-Württemberg Drucksache 17 / 4244",  # page header
        ],
    )
    def test_clean_german_scores_high(self, text):
        assert _paragraph_quality_score(text) > 0.9

    @pytest.mark.parametrize(
        "text",
        [
            # Shifted font encoding: consonant-heavy gibberish with C1 control chars.
            "6WlGWHWDJ%DGHQ\x81UWWHPEHUJ\x873RVWIDFK\x876WXWWJDUW",
            # Latin Extended-B output from broken ToUnicode maps.
            "ůĂƐƐĞŶ-ŵŝƚ-ǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶ-ƵŶĚ",
            (
                "6WlGWHWDJVVWHOOXQJQDKPH]XP*HVHW]HQWZXUI-GHU)'3'93]XU:LHGHUKHUVWHOOXQJ\r\n"
                "GHU-9HUELQGOLFKNHLW-GHU*UXQGVFKXOHPSIHKOXQJ/DQGWDJVGUXFNVDFKH\r\n"
            ),
        ],
    )
    def test_garbled_text_scores_low(self, text):
        assert _paragraph_quality_score(text) < 0.5

    @pytest.mark.parametrize("text", ["", "   \n  "])
    def test_empty_text_scores_high(self, text):
        assert _paragraph_quality_score(text) == 1.0


# ---------------------------------------------------------------------------
# TestNormalizeVolltext
# ---------------------------------------------------------------------------


class TestNormalizeVolltext:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param(
                "Gemäß § 50a Absatz 2 der Geschäftsordnung.", "Gemäß § 50a Absatz 2 der Geschäftsordnung.", id="clean"
            ),
            pytest.param("", "", id="empty"),
            pytest.param("Zeile eins\r\nZeile zwei", "Zeile eins\nZeile zwei", id="crlf"),
            pytest.param("Zeile eins\rZeile zwei", "Zeile eins\nZeile zwei", id="lone-cr"),
            pytest.param("Absatz eins\n\n\n\n\nAbsatz zwei", "Absatz eins\n\nAbsatz zwei", id="blank-lines"),
            # corelib strips trailing spaces at every line end since v0.3.0 (DD-059).
            pytest.param(
                "Zeile mit Leerzeichen   \nNächste Zeile  ", "Zeile mit Leerzeichen\nNächste Zeile", id="trailing-ws"
            ),
            pytest.param(
                "<Poststelle@lfdi.bwl.de> und <info@example.com>",
                "\u2039Poststelle@lfdi.bwl.de\u203a und \u2039info@example.com\u203a",
                id="angle-brackets",
            ),
            pytest.param("Text\x81mit\x87Steuerzeichen\x89hier", "TextmitSteuerzeichenhier", id="c1-controls"),
            pytest.param("Deﬁnition eines Begriffs", "Definition eines Begriffs", id="nfkc"),
            pytest.param("Ein \ufffd Text mit \ufffd Zeichen", "Ein Text mit Zeichen", id="replacement-char"),
            pytest.param("ausgezeich\ufffdnet", "ausgezeichnet", id="replacement-char-joins-word"),
            # Line-break hyphenation (issue #20): kreuzberg keeps the hyphen of a joined break.
            pytest.param("auf 16 Jahre abzu-senken. Damit", "auf 16 Jahre abzusenken. Damit", id="issue20-inline"),
            pytest.param("auf 16 Jahre abzu-\nsenken.", "auf 16 Jahre abzusenken.", id="issue20-across-newline"),
            pytest.param("die \u00c4n-derung", "die \u00c4nderung", id="issue20-umlaut"),
            # Genuine compounds continue uppercase and stay intact.
            pytest.param("das Land Baden-W\u00fcrttemberg", "das Land Baden-W\u00fcrttemberg", id="compound"),
            pytest.param("per E-Mail", "per E-Mail", id="hyphen-before-uppercase"),
            # Issue #30: real tokens from Drucksache 17/1000 ("Rich terinnen" in the V-215974 summary).
            pytest.param(
                "planm\u00e4\u00dfigen Beamtinnen und Beamten oder Richte-rinnen und Richtern",
                "planm\u00e4\u00dfigen Beamtinnen und Beamten oder Richterinnen und Richtern",
                id="issue30-richterinnen",
            ),
            pytest.param("und Beam-ten sowie", "und Beamten sowie", id="issue30-beamten"),
            pytest.param("im Ein-gangsamt oder", "im Eingangsamt oder", id="issue30-eingangsamt"),
        ],
    )
    def test_normalizes(self, text, expected):
        assert normalize_volltext(text) == expected

    @pytest.mark.parametrize(
        ("clean", "garbled"),
        [
            (
                "Landtag von Baden-Württemberg\nDrucksache 17/4244",
                "6WlGWHWDJ%DGHQ\x81UWWHPEHUJ\x873RVWIDFK\x876WXWWJDUW",
            ),
            ("Ein normaler deutscher Absatz mit korrektem Text.", "ůĂƐƐĞŶ-ŵŝƚ-ǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶ-ƵŶĚ"),
        ],
        ids=["shifted-ascii", "latin-extended"],
    )
    def test_garbled_paragraphs_removed(self, clean, garbled):
        """Mixed document: the clean paragraph stays, the garbled one goes."""
        assert normalize_volltext(f"{clean}\n\n{garbled}") == clean


# ---------------------------------------------------------------------------
# TestSanitizeLlmText (DD-027)
# ---------------------------------------------------------------------------


class TestSanitizeLlmText:
    """Tests for the LLM-output sanitizer that defends against the backend's
    XSS validator. Motivated by V-243670 in the staging run 2026-05-10/11:
    gpt-5-nano sporadically appends artefacts like ``</narrow>`` to its
    response despite the system prompt forbidding formatting.
    """

    @pytest.mark.parametrize("value", [None, ["Kurz", "Titel"], 42, {"a": 1}, True])
    def test_non_string_yields_none(self, value):
        # The LLM's JSON is untyped; a non-string must not raise (DD-027).
        assert _sanitize_llm_text(value) is None

    def test_non_string_list_items_are_dropped(self):
        assert _sanitize_llm_strings(["umwelt", 42, ["x"], "energie"]) == ["umwelt", "energie"]

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

    def test_pathological_long_tag_left_alone(self):
        # Tags longer than the 80-char regex cap fall through to the
        # bracket-substitution path, still neutralising the danger.
        text = "ok <" + "a" * 200 + "> done"
        result = _sanitize_llm_text(text)
        assert "<" not in result
        assert ">" not in result

    @pytest.mark.parametrize(
        ("values", "expected"),
        [
            (["foo", "<narrow></narrow>", "bar"], ["foo", "bar"]),
            (["umwelt", "klimaschutz"], ["umwelt", "klimaschutz"]),
        ],
    )
    def test_strings_helper_drops_empty_results(self, values, expected):
        assert _sanitize_llm_strings(values) == expected

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
                "vorwort": "Ziel: Klimaschutz <hr/>",
            }
        )
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()

        with _patch_pdf_pipeline(), _patch_llm(polluted_response):
            result = await enrich_dokument(session, llm, dok)

        assert zusammenfassung_text(result.dokument) == "Der Landtag hat das Gesetz beschlossen."
        assert result.dokument.vorwort == "Ziel: Klimaschutz"
        # No raw brackets reach the API model \u2014 guards against the backend XSS validator.
        assert "<" not in zusammenfassung_text(result.dokument)
        assert ">" not in zusammenfassung_text(result.dokument)


# ---------------------------------------------------------------------------
# TestIsGarbled
# ---------------------------------------------------------------------------


class TestIsGarbled:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(
                "Gemäß § 50a Absatz 2 der Geschäftsordnung habe ich im Einvernehmen "
                "mit den Antragstellern die Landesregierung gebeten, zu dem Gesetzentwurf "
                "der Fraktion FDP/DVP die Anhörung durchzuführen.",
                id="clean-german",
            ),
            # German umlauts (U+00C4-U+00FC) are NOT Latin Extended.
            pytest.param("Änderung des Gesetzes für Straßenverkehr mit Ölprüfung und Übergabe", id="umlauts"),
            # ASCII shift without C1 chars leaves no reliable signal; normalize_volltext's
            # paragraph scoring (vowelless words + uppercase ratio) still removes it.
            pytest.param(
                "6WlGWHWDJ%DGHQUWWHPEHUJ3RVWIDFK6WXWWJDUW )UDNWLRQGHU)'3'93]XU:LHGHUHLQIKUXQJ", id="shift-without-c1"
            ),
            # A stray C1 char in otherwise clean text must not trigger the OCR retry.
            pytest.param(
                "Landtag von Baden-Württemberg, Drucksache 17/1201, Änderungsanträge zum Haushalt. " * 4 + "\x81",
                id="c1-below-threshold",
            ),
            # 4 Latin Extended of 99 alpha chars ≈ 4 %, just below the 5 % threshold.
            pytest.param("a" * 95 + "ůĂƐŝ", id="latin-ext-below-threshold"),
            pytest.param("", id="empty"),
            pytest.param("Kurz.", id="too-short"),
        ],
    )
    def test_not_garbled(self, text):
        assert _is_garbled(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            # DD-015 Muster 1: Latin Extended characters from a broken ToUnicode CMap.
            pytest.param(
                "ZĞ͗'ĞƐĞƚǌĞŶƚǁƵƌĨĚĞƌ&ƌĂŬƚŝŽŶĚĞƌ&WͬsWͲ'ĞƐĞƚǌǌƵƌtŝĞĚĞƌŚĞƌƐƚĞůůƵŶŐĚĞƌsĞƌďŝŶĚůŝĐŚŬĞŝƚĚĞƌ'ƌƵŶĚƐĐŚƵůĞŵƉĨĞŚůƵŶŐ",
                id="latin-extended",
            ),
            # Clean first page, garbled rest: overall garbled.
            pytest.param(
                "Landtag von Baden-Württemberg\n17. Wahlperiode\nDrucksache 17/4244\n" * 3
                + "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 20,
                id="mixed",
            ),
            # DD-015 Muster 2 (issue #20): verbatim kreuzberg output for page 2 of Drucksache
            # 17/1201, every glyph shifted, umlauts in the C1 range (5 / 367 ≈ 1.4 %).
            pytest.param(
                "6HLWHYRQ\n/DQGWDJ-YRQ%DGHQ:\x81UWWHPEHUJ  'UXFNVDFKH\n:DKOSHULRGH  (LQJDQJ\n\n\n\n"
                "bQGHUXQJVDQWUDJ GHU)UDNWLRQ*5h1(XQG\nGHU)UDNWLRQ-GHU&'8\n\n\n"
                "]X-GHU%HVFKOXVVHPSIHKOXQJ-GHV$XVVFKXVVHV-I\x81U)LQDQ]HQ\n±'UXFNVDFKH1XPPHU\n\n\n"
                "]X-GHP(QWZXUI-GHV-6WDDWVKDXVKDOWVSODQV-I\x81U\n\n\n'HU/DQGWDJ-ZROOH-EHVFKOLH\x89HQ\n\n"
                "(LQ]HOSODQ /DQGWDJ\n\n.DSLWHO $OOJHPHLQH%HZLOOLJXQJHQ\n\n=X-lQGHUQ\n6\n\n"
                "7LWHO\n7LW*U ).= =ZHFNEHVWLPPXQJ\n%HWUDJ\nI\x81U\n\n7VG(85\n"
                "   %HLKLOIHQ-DXIJUXQG-GHU%HLKLOIHYHURUGQXQJ-XGJORKQH-9HU\nVRUJXQJVHPSIlQJHU-LQQHQ\n",
                id="issue20-shift-with-c1",
            ),
        ],
    )
    def test_garbled(self, text):
        assert _is_garbled(text) is True


# ---------------------------------------------------------------------------
# Improvement 1: TestVorwortExtraction
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Improvement 2: TestJsonStructuredOutput
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Improvement 3: TestValidateScores
# ---------------------------------------------------------------------------


class TestValidateScores:
    """Post-extraction validation of the meinung (1-5) range."""

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            pytest.param({"meinung": 3}, {"meinung": 3}, id="valid"),
            pytest.param({"meinung": 0}, {"meinung": 1}, id="clamped-to-min"),
            pytest.param({"meinung": 8}, {"meinung": 5}, id="clamped-to-max"),
            pytest.param({"meinung": 3.7}, {"meinung": 3}, id="float-truncated"),
            pytest.param({"meinung": "positiv"}, {}, id="non-numeric-removed"),
            pytest.param({"schlagworte": ["test"]}, {"schlagworte": ["test"]}, id="no-scores"),
        ],
    )
    def test_validate_scores(self, data, expected):
        assert _validate_scores(data) == expected


# ---------------------------------------------------------------------------
# Improvement 4: TestLLMMetrics
# ---------------------------------------------------------------------------


class TestLLMMetrics:
    """LLM enrichment metrics tracking."""

    def test_total_computed(self):
        m = LLMMetrics()
        assert m.total == 0
        m.success = 5
        m.failed = 2
        m.cache_hits = 3
        assert m.total == 10

    def test_format_lines(self):
        m = LLMMetrics()
        m.success = 3
        m.cache_hits = 1  # 1 / 4 = 25%
        lines = m.format_lines()
        assert "LLM enrichment:" in lines
        assert any("Success:" in line and "3" in line for line in lines)
        cache_line = next(line for line in lines if "Cache hits" in line)
        assert "25%" in cache_line

    def test_format_lines_empty_when_nothing_counted(self):
        """Issue #56: the summaries print the block without a `total > 0` guard."""
        assert LLMMetrics().format_lines() == []

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
            "bawue.pdf_text.extract_file",
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
            "bawue.pdf_text.extract_file",
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
        # No page hint → no page markers needed.
        assert ocr_call_config.pages is None or ocr_call_config.pages.insert_page_markers is False

    @pytest.mark.asyncio
    async def test_ocr_retry_keeps_page_markers(self, tmp_path):
        """With a #page=N hint the OCR retry must insert page markers too.

        OCR replaces the whole text; without markers _extract_relevant_pages
        falls back to the full document, so every Antrag of a Sammeldrucksache
        would share one volltext (GitHub issue #20).
        """
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10
        clean_page_2 = "Änderungsantrag der Fraktion GRÜNE zum Staatshaushaltsplan für 2022."
        ocr_text = f"<!-- PAGE 1 -->\n\nDeckblatt des Landtags\n\n<!-- PAGE 2 -->\n\n{clean_page_2}"

        with patch(
            "bawue.pdf_text.extract_file",
            new_callable=AsyncMock,
            side_effect=[_mock_extraction_result(content=garbled_text), _mock_extraction_result(content=ocr_text)],
        ) as mock_extract:
            text, _hash = await extract_pdf_text(pdf_file, page_markers=True)

        ocr_call_config = mock_extract.call_args_list[1].kwargs["config"]
        assert ocr_call_config.force_ocr is True
        assert ocr_call_config.pages.insert_page_markers is True
        # The hint window survives: page 2 only, not the whole OCR output.
        assert _extract_relevant_pages(text, 2).strip() == clean_page_2

    @pytest.mark.asyncio
    async def test_ocr_failure_keeps_original_text(self, tmp_path):
        """If OCR retry also returns garbled text, keep original (normalize will strip)."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10

        garbled_result = _mock_extraction_result(content=garbled_text)
        still_garbled = _mock_extraction_result(content=garbled_text)

        with patch(
            "bawue.pdf_text.extract_file",
            new_callable=AsyncMock,
            side_effect=[garbled_result, still_garbled],
        ):
            text, _ = await extract_pdf_text(pdf_file)

        # Original text returned when OCR doesn't improve it
        assert text == garbled_text

    @pytest.mark.asyncio
    async def test_ocr_rejected_when_it_yields_less_usable_text(self, tmp_path):
        """A clean-but-shorter OCR result must not replace a partly garbled original.

        `_is_garbled` measures the whole document, so it also fires on PDFs whose
        text is only partly broken. OCR replaces everything — including the pages
        that extracted cleanly — so "not garbled" alone is too weak a bar.
        """
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        # Mostly clean German prose, with a garbled tail that trips _is_garbled.
        clean_body = "Der Landtag wolle beschließen, den Gesetzentwurf unverändert anzunehmen. " * 10
        garbled_tail = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 3
        partly_garbled = f"{clean_body}\n\n{garbled_tail}"
        # OCR came back clean but recovered far less of the document.
        sparse_ocr = "Der Landtag wolle beschließen, den Gesetzentwurf anzunehmen."

        assert _is_garbled(partly_garbled) is True
        assert _is_garbled(sparse_ocr) is False

        with patch(
            "bawue.pdf_text.extract_file",
            new_callable=AsyncMock,
            side_effect=[
                _mock_extraction_result(content=partly_garbled),
                _mock_extraction_result(content=sparse_ocr),
            ],
        ):
            text, _ = await extract_pdf_text(pdf_file)

        assert text == partly_garbled

    @pytest.mark.asyncio
    async def test_ocr_exception_keeps_original_text(self, tmp_path):
        """If OCR retry raises an exception, keep original text."""
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(SAMPLE_PDF_BYTES)

        garbled_text = "ůĂƐƐĞŶŵŝƚǀĞƌŐůĞŝĐŚďĂƌĞŶĞŐĂďƵŶŐĞŶƵŶĚ " * 10

        garbled_result = _mock_extraction_result(content=garbled_text)

        with patch(
            "bawue.pdf_text.extract_file",
            new_callable=AsyncMock,
            side_effect=[garbled_result, RuntimeError("OCR failed")],
        ):
            text, _ = await extract_pdf_text(pdf_file)

        assert text == garbled_text

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
                "bawue.pdf_text.extract_file",
                new_callable=AsyncMock,
                side_effect=[garbled_result, ocr_result],
            ),
            caplog.at_level(logging.WARNING, logger="bawue.pdf_text"),
        ):
            await extract_pdf_text(pdf_file)

        assert any("garbled" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# TestPageHintExtraction
# ---------------------------------------------------------------------------


class TestPageHintExtraction:
    @pytest.mark.parametrize(
        ("url", "page"),
        [
            ("https://www.landtag-bw.de/files/plp/17_141.pdf#page=33", 33),
            ("https://www.landtag-bw.de/files/plp/17_141.pdf", None),
            ("https://example.com/doc.pdf#section1", None),
        ],
    )
    def test_parse_page_hint(self, url, page):
        assert _parse_page_hint(url) == page

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

    @pytest.mark.parametrize(
        ("text", "start_page"),
        [
            pytest.param("Plain text without any page markers at all.", 33, id="no-markers"),
            pytest.param(
                "Preamble" + "".join(f"\n\n<!-- PAGE {i} -->\n\nContent of page {i}" for i in range(1, 11)),
                50,
                id="page-beyond-end",
            ),
            # Issue #50: a window with < MIN_TEXT_LENGTH usable chars (a page-number-only
            # page, or a placeholder "TODO") falls back instead of persisting it.
            pytest.param(
                "\n\n<!-- PAGE 1 -->\n\n"
                "Dies ist der vollständige Gesetzestext mit reichlich Inhalt, "
                "der als Volltext des Dokuments dienen soll und weit über die "
                "Mindestlänge hinausgeht."
                "\n\n<!-- PAGE 2 -->\n\n6\n\n6",
                2,
                id="issue50-near-empty-window",
            ),
            pytest.param(
                "\n\n<!-- PAGE 1 -->\n\n"
                "Ausführlicher Beschlussempfehlungstext, der deutlich länger ist "
                "als die konfigurierte Mindesttextlänge und echten Inhalt trägt."
                "\n\n<!-- PAGE 2 -->\n\nTODO",
                2,
                id="issue50-todo-only-window",
            ),
        ],
    )
    def test_extract_relevant_pages_falls_back_to_full_text(self, text, start_page):
        assert _extract_relevant_pages(text, start_page=start_page) == text

    def test_extract_relevant_pages_first_marker_at_string_start_issue27(self):
        """Issue #27: a #page=1 anchor must return page 1, not silently substitute
        whatever page follows it.

        Real-world regression: GBl2026012.pdf (V-244180) has no preamble before the
        page-1 marker (kreuzberg emits it as the very first thing in the extracted
        text), so the marker's required leading "\\n\\n" never existed. The old regex
        failed to match it, `_extract_relevant_pages` treated the whole of page 1 as
        unlabeled "text before the first marker" and discarded it outright — leaving
        only the page 2 signature block, which was then persisted as `volltext` and
        summarized by the LLM as if it were the law's content.
        """
        text = (
            "<!-- PAGE 1 -->\n\n"
            "Gesetz zur Änderung des Juristenausbildungsgesetzes. Artikel 1 regelt ..."
            "\n\n<!-- PAGE 2 -->\n\n"
            "Stuttgart, den 10. Februar 2026\nDie Regierung des Landes Baden-Württemberg:"
        )
        result = _extract_relevant_pages(text, start_page=1, max_pages=1)
        assert "Artikel 1" in result
        assert "Die Regierung des Landes" not in result

    def test_extract_relevant_pages_trailing_marker_without_content_issue27(self):
        """Issue #27 (symmetric edge): a marker with nothing after it (last page has
        no extracted content) must not leak its raw "<!-- PAGE N -->" syntax into the
        previous page's text.
        """
        page_one = "Seite eins mit ausreichend langem Inhalt, damit der MIN_TEXT_LENGTH-Rückfall nicht greift."
        text = f"\n\n<!-- PAGE 1 -->\n\n{page_one}\n\n<!-- PAGE 2 -->"
        result = _extract_relevant_pages(text, start_page=1, max_pages=1)
        assert "<!-- PAGE 2 -->" not in result
        assert result.strip() == page_one


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
        assert zusammenfassung_text(result.dokument) == "Ein Gesetzentwurf zur Förderung erneuerbarer Energien."

    @pytest.mark.asyncio
    async def test_issue105_redis_error_fails_without_llm_call(self):
        """Redis down is no cache miss and no text-only document: the Vorgang build fails and is retried."""
        from bawue.cache import RedisUnavailableError

        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        cache = MagicMock()
        cache.get_raw.side_effect = RedisUnavailableError("redis down")

        with (
            _patch_pdf_pipeline(),
            _patch_llm(SAMPLE_LLM_RESPONSE_ENTWURF) as mock_acomp,
            pytest.raises(RedisUnavailableError),
        ):
            await enrich_dokument(MagicMock(), _make_llm_mock(), dok, cache=cache)

        mock_acomp.assert_not_called()
        cache.store_raw.assert_not_called()

    @pytest.mark.asyncio
    async def test_in_memory_cache_takes_priority_over_redis(self):
        """In-memory cache should be checked before Redis."""
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        session = MagicMock()
        llm = _make_llm_mock()
        cache = MagicMock()

        # Pre-populate in-memory cache with the composite (doc_hash, prompt_hash) key.
        # The prompt hash includes the document identity (titel + Drucksache), as the
        # production cache key does (issue #32).
        _hash_cache[
            _cache_key(SAMPLE_HASH, _prompt_fingerprint(Doktyp.ENTWURF, drucksnr=dok.drucksnr, titel=dok.titel))
        ] = json.loads(SAMPLE_LLM_RESPONSE_ENTWURF)

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

        assert (
            _cache_key(SAMPLE_HASH, _prompt_fingerprint(Doktyp.ENTWURF, drucksnr=dok.drucksnr, titel=dok.titel))
            in _hash_cache
        )

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


class TestIssue42TypedZusammenfassung:
    """GitHub issue #42 step 1 (DD-054): our summary is LLM-written, so it goes out
    as the typed tuple ``[(full-llm, …)]`` instead of a plain string (= ``full``)."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    def test_zusammenfassung_text_reads_the_full_llm_inhalt(self):
        dok = _make_plain_dokument(typ=Doktyp.ENTWURF)
        assert zusammenfassung_text(dok) is None
        dok.zusammenfassung = None
        assert zusammenfassung_text(dok) is None
        dok.zusammenfassung = [Zusammenfassungstupel(typ="full-llm", inhalt="Kurz gesagt.")]
        assert zusammenfassung_text(dok) == "Kurz gesagt."


class TestIssue42PartialSummaries:
    """GitHub issue #42 step 2 (DD-056): Entwurf and Beschlussempfehlung additionally
    carry the three sections Brandenburg sends (``intention-llm``,
    ``regelungsinhalt-llm``, ``kosten-llm``), after the ``full-llm`` summary."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    @staticmethod
    def _response(**teile) -> str:
        return json.dumps({"zusammenfassung": "Ganzes Dokument.", "schlagworte": ["x"], **teile})

    def test_labels_mirror_brandenburg(self):
        # Cross-Land vocabulary (pazufa-scraper-bb #67); the website attaches the
        # "AI generated" label by looking for "llm" in the typ.
        assert ZUSAMMENFASSUNG_TEILE == {
            "intention": "intention-llm",
            "regelungsinhalt": "regelungsinhalt-llm",
            "kosten": "kosten-llm",
        }

    @pytest.mark.parametrize("prompt", [BODY_PROMPT_ENTWURF, BODY_PROMPT_BESCHLUSSEMPF])
    def test_single_topic_prompts_ask_for_the_sections(self, prompt):
        for key in ZUSAMMENFASSUNG_TEILE:
            assert f'"{key}"' in prompt

    @pytest.mark.parametrize("prompt", [BODY_PROMPT_ENTWURF, BODY_PROMPT_BESCHLUSSEMPF])
    def test_single_topic_prompts_carry_brandenburgs_rules(self, prompt):
        # The three section rules of pazufa-scraper-bb's ZUSAMMENFASSUNG_STRUKTUR_PROMPT.
        assert "sachlich, ohne Wertung" in prompt
        assert "Keine Aufzählung der Artikelstruktur" in prompt
        assert "Die Zusammenfassung ist immer zu füllen." in prompt

    @pytest.mark.parametrize(
        ("doktyp", "fingerprint"),
        [
            (Doktyp.ENTWURF, "46d34e16fc98edffbffcbe28aa0223a397b04544f8e0bc9861cce955b207e5a1"),
            (Doktyp.PREPARL_ENTWURF, "46d34e16fc98edffbffcbe28aa0223a397b04544f8e0bc9861cce955b207e5a1"),
            (Doktyp.BESCHLUSSEMPF, "27d1504f83e7e9cc2e801b93afa8ebcbd239f6fafa0b4d4da5fe7b7947a5680c"),
        ],
    )
    def test_section_prompts_fingerprint_is_pinned(self, doktyp, fingerprint):
        # Re-pin only on a deliberate prompt change: it re-summarises every
        # document of these doktypes once (DD-056).
        assert _prompt_fingerprint(doktyp, drucksnr="17/1", titel="T") == fingerprint

    @pytest.mark.parametrize(
        ("doktyp", "fingerprint"),
        [
            (Doktyp.STELLUNGNAHME, "a8e05f21f5348e8db35ff22b09bcc4aaefca779a48d35afc96e42c500fd8bb2a"),
            (Doktyp.REDEPROTOKOLL, "ebd3db4b183641dc7c57985b6c9d039204fd42d93c1fa44a70e5d1fde092ada4"),
            (Doktyp.SONSTIG, "73a906dc649ce1c1a7e1a0acb8fabc94030ac2319f0b236bbb19c712d529d4d1"),  # GENERIC
        ],
    )
    def test_other_doktypes_keep_their_cache_fingerprint(self, doktyp, fingerprint):
        # Values from main before DD-056: their llm-semantics: entries must stay valid.
        assert _prompt_fingerprint(doktyp, drucksnr="17/1", titel="T") == fingerprint

    @pytest.mark.asyncio
    @pytest.mark.parametrize("doktyp", [Doktyp.ENTWURF, Doktyp.PREPARL_ENTWURF, Doktyp.BESCHLUSSEMPF])
    async def test_sections_follow_the_full_llm_summary(self, doktyp):
        response = self._response(
            intention="Problem X lösen.", regelungsinhalt="§ 5 wird geändert.", kosten="2 Mio. Euro."
        )
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument(typ=doktyp))

        assert result.dokument.to_dict()["zusammenfassung"] == [
            {"typ": "full-llm", "inhalt": "Ganzes Dokument."},
            {"typ": "intention-llm", "inhalt": "Problem X lösen."},
            {"typ": "regelungsinhalt-llm", "inhalt": "§ 5 wird geändert."},
            {"typ": "kosten-llm", "inhalt": "2 Mio. Euro."},
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("empty", ["", "   ", None, ["kein", "string"], 42])
    async def test_empty_or_malformed_section_is_omitted(self, empty):
        # Kosten is often absent; the backend rejects empty strings.
        response = self._response(intention="Problem X lösen.", regelungsinhalt="Neu.", kosten=empty)
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument())

        assert [t.typ for t in result.dokument.zusammenfassung] == [
            "full-llm",
            "intention-llm",
            "regelungsinhalt-llm",
        ]

    @pytest.mark.asyncio
    async def test_sections_are_sanitised(self):
        response = self._response(intention="Problem <b>X</b> lösen.</narrow>")
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument())

        assert result.dokument.zusammenfassung[1].inhalt == "Problem X lösen."

    @pytest.mark.asyncio
    @pytest.mark.parametrize("full", ["", "   ", "</narrow>", None, ["kein", "string"]])
    async def test_no_sections_without_a_full_summary(self, full):
        # As in BB, the sections only accompany the full summary; on their own they
        # would show up without the text they detail (DD-056).
        response = json.dumps({"schlagworte": ["x"], "zusammenfassung": full, "intention": "Problem X lösen."})
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument())

        assert result.dokument.zusammenfassung is None

    @pytest.mark.asyncio
    async def test_zusammenfassung_text_still_reads_only_full_llm(self):
        response = self._response(intention="Problem X lösen.")
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument())

        assert zusammenfassung_text(result.dokument) == "Ganzes Dokument."

    @pytest.mark.asyncio
    async def test_cached_semantics_without_sections_send_only_full_llm(self):
        # A Redis entry written before DD-056 (Stellungnahme: unchanged prompt, so the
        # entry is still hit) has no section keys.
        cache = MagicMock()
        cache.get_raw.return_value = SAMPLE_LLM_RESPONSE_STELLUNGNAHME
        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_STELLUNGNAHME) as mock_acomp:
            result = await enrich_dokument(
                MagicMock(), _make_llm_mock(), _make_plain_dokument(typ=Doktyp.STELLUNGNAHME), cache=cache
            )

        mock_acomp.assert_not_called()
        assert [t.typ for t in result.dokument.zusammenfassung] == ["full-llm"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "value"), [("kurztitel", ["Kurz", "Titel"]), ("vorwort", 42), ("schlagworte", ["x", 7])]
    )
    async def test_malformed_field_keeps_the_other_llm_fields(self, field, value):
        # A non-string used to raise in _sanitize_llm_text, and the text-only
        # fallback then dropped every LLM field; now only the bad value is dropped.
        response = self._response(**{"kurztitel": "Kurz", "vorwort": "Vorwort.", field: value})
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument())

        assert zusammenfassung_text(result.dokument) == "Ganzes Dokument."
        assert result.dokument.schlagworte == ["x"]
        assert result.dokument.kurztitel == (None if field == "kurztitel" else "Kurz")
        assert result.dokument.vorwort == (None if field == "vorwort" else "Vorwort.")

    @pytest.mark.asyncio
    async def test_malformed_full_summary_keeps_the_other_llm_fields(self):
        # A non-string zusammenfassung used to raise in _sanitize_llm_text and drop
        # every LLM field via the text-only fallback; now only the summary is dropped
        # (with its sections, which never go out on their own).
        response = self._response(zusammenfassung=["kein", "string"], kurztitel="Kurz", intention="Problem X.")
        with _patch_pdf_pipeline(), _patch_llm(response):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), _make_plain_dokument())

        assert result.dokument.zusammenfassung is None
        assert result.dokument.kurztitel == "Kurz"


# ---------------------------------------------------------------------------
# Issue #74: a protocol's summary reads only its agenda item's pages
# ---------------------------------------------------------------------------


def _marked(pages: range, offset: int = 0, short: frozenset[int] = frozenset()) -> str:
    """Extraction text with kreuzberg page markers. Like a real protocol page, each starts
    with its printed number (PDF page + *offset*); SEITE-n names the PDF page."""
    return "".join(
        f"\n\n<!-- PAGE {p} -->\n\n{p + offset}\n"
        + ("" if p in short else f"SEITE-{p} " + "Redebeitrag im Plenum. " * 25)
        for p in pages
    )


_HEADERS_ONLY = "".join(
    f"\n\n<!-- PAGE {p} -->\n\n{p + 256}\nLandtag von Baden-Württemberg - 18. Wahlperiode - 10. Sitzung"
    " - Donnerstag, 23. Juli 2026"
    for p in range(1, 80)
)


class TestIssue74AgendaPages:
    """PlPr 18/10 S. 287-291 (#page=31): the summary covered Palantir and the Fragestunde."""

    PLP = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP18/Plp/18%5F0010%5F23072026.pdf"

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _hash_cache.clear()
        yield
        _hash_cache.clear()

    async def _enrich(self, druckseiten, anchor=31, text=None, typ=Doktyp.REDEPROTOKOLL):
        link = f"{self.PLP}#page={anchor}" if anchor else self.PLP
        dok = _make_plain_dokument(typ=typ, link=link)
        offset = druckseiten[0] - anchor if druckseiten and anchor else 0
        with (
            _patch_pdf_pipeline(text_and_hash=(text or _marked(range(1, 80), offset), SAMPLE_HASH)),
            _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC) as llm_call,
        ):
            result = await enrich_dokument(MagicMock(), _make_llm_mock(), dok, druckseiten=druckseiten)
        prompts = [c.kwargs["messages"][-1]["content"] for c in llm_call.await_args_list]
        return result, prompts

    @staticmethod
    def _pages(prompt: str) -> list[int]:
        return [int(p) for p in re.findall(r"SEITE-(\d+)", prompt)]

    @pytest.mark.asyncio
    async def test_summary_reads_only_the_agenda_pages(self):
        result, [prompt] = await self._enrich(list(range(287, 292)))

        assert self._pages(prompt) == [31, 32, 33, 34, 35]
        assert BODY_PROMPT_REDEPROTOKOLL_TOP in prompt
        # volltext stays the 30-page window from the anchor (DD-049), without markers.
        assert self._pages(result.dokument.volltext) == list(range(31, 61))
        assert "<!-- PAGE" not in result.dokument.volltext

    @pytest.mark.asyncio
    async def test_agenda_text_is_normalized_like_volltext(self):
        """Same cleaning as the window: markup stripped, hyphen artefacts rejoined (issue #20)."""
        text = _marked(range(1, 80), 256).replace("SEITE-32 ", "SEITE-32 Kosten abzu-senken <script>x</script> ")
        _, [prompt] = await self._enrich(list(range(287, 292)), text=text)

        assert "abzusenken" in prompt
        assert "abzu-senken" not in prompt and "<script>" not in prompt

    @pytest.mark.asyncio
    async def test_every_segment_is_kept(self):
        """PlPr 18/7 S. 168-172; 179 @@ D-246981: the vote follows other items."""
        _, [prompt] = await self._enrich([168, 169, 170, 171, 172, 179], anchor=30)

        assert self._pages(prompt) == [30, 31, 32, 33, 34, 41]

    @pytest.mark.asyncio
    async def test_never_more_than_the_window(self):
        """Haushalt sessions span up to 104 pages (PlPr 17/51 S. 2932-3035)."""
        _, [prompt] = await self._enrich(list(range(2932, 3036)), anchor=16)

        assert BODY_PROMPT_REDEPROTOKOLL_TOP in prompt  # narrowed, not the fallback window
        assert self._pages(prompt) == list(range(16, 46))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("druckseiten", "anchor", "text"),
        [
            (None, 31, None),  # no "S." in the Fundstelle
            ([], 31, None),
            (list(range(287, 292)), None, None),  # no #page anchor
            (list(range(287, 292)), 31, "Protokoll ohne Seitenmarker. " * 10),  # OCR text without markers
            (list(range(287, 292)), 78, _marked(range(1, 79), 209)),  # PDF ends on page 78: wrong offset
            (list(range(287, 292)), 31, _marked(range(1, 80), 256, short=frozenset(range(31, 36)))),  # near-empty
            (list(range(287, 292)), 30, _marked(range(1, 80), 256)),  # anchor one page early (review of PR #153)
            ([179, 287, 288], 31, _marked(range(1, 80), 256)),  # "S. 287-288; 179", sorted: 179 is no anchor page
            (list(range(287, 292)), 31, _HEADERS_ONLY),  # each page only its header line (review of PR #153)
        ],
        ids=[
            "no-pages",
            "empty-pages",
            "no-anchor",
            "no-markers",
            "beyond-pdf",
            "near-empty",
            "shifted",
            "unordered",
            "headers-only",
        ],
    )
    async def test_falls_back_to_todays_input(self, druckseiten, anchor, text):
        """Without usable agenda pages nothing changes: same input, prompt and cache key."""
        result, [prompt] = await self._enrich(druckseiten, anchor=anchor, text=text)

        assert BODY_PROMPT_REDEPROTOKOLL in prompt
        assert prompt.endswith(result.dokument.volltext)
        assert list(_hash_cache) == [_cache_key(SAMPLE_HASH, _prompt_fingerprint(Doktyp.REDEPROTOKOLL))]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("typ", [Doktyp.ENTWURF, Doktyp.BESCHLUSSEMPF, Doktyp.SONSTIG])
    async def test_other_doktypen_are_not_narrowed(self, typ):
        _, [prompt] = await self._enrich(list(range(287, 292)), typ=typ)

        assert self._pages(prompt) == list(range(31, 61))

    @pytest.mark.asyncio
    async def test_each_agenda_item_gets_its_own_summary(self):
        """Two items of one sitting: one LLM call each, no longer one per file (DD-037)."""
        dok_a = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, link=f"{self.PLP}#page=31")
        dok_b = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, link=f"{self.PLP}#page=40")
        dok_a2 = _make_plain_dokument(typ=Doktyp.REDEPROTOKOLL, link=f"{self.PLP}#page=31")
        with (
            _patch_pdf_pipeline(text_and_hash=(_marked(range(1, 80), 256), SAMPLE_HASH)),
            _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC) as llm_call,
        ):
            await enrich_dokument(MagicMock(), _make_llm_mock(), dok_a, druckseiten=[287, 288])
            await enrich_dokument(MagicMock(), _make_llm_mock(), dok_b, druckseiten=[296, 297])
            # Jointly debated: same anchor, same pages, same row and summary.
            await enrich_dokument(MagicMock(), _make_llm_mock(), dok_a2, druckseiten=[287, 288])

        assert llm_call.await_count == 2

    @pytest.mark.asyncio
    async def test_anchored_non_protocols_keep_one_summary_each(self):
        """Issue #72: Änderungsanträge packed into one PDF must not share a summary."""
        base = "https://www.landtag-bw.de/resource/blob/3/17_4495.pdf"
        with _patch_pdf_pipeline(), _patch_llm(SAMPLE_LLM_RESPONSE_GENERIC) as llm_call:
            for page in (1, 5):
                dok = _make_plain_dokument(typ=Doktyp.ENTWURF, link=f"{base}#page={page}")
                await enrich_dokument(MagicMock(), _make_llm_mock(), dok, druckseiten=[1, 2])

        assert llm_call.await_count == 2

    @pytest.mark.asyncio
    async def test_same_anchor_other_pages_is_another_summary(self):
        """PlPr 17/140 #page=71: S. 8622 and S. 8622-8623 are two items on one page."""
        _, [one] = await self._enrich([8622], anchor=71)
        _, [two] = await self._enrich([8622, 8623], anchor=71)

        assert (self._pages(one), self._pages(two)) == ([71], [71, 72])


class TestIssue74RealProtocol:
    """The whole PlPr 18/10 as kreuzberg extracts it (page markers kept), unshortened."""

    TEXT = (Path(__file__).parent.parent / "fixtures" / "pdf" / "18_0010_23072026_marked.txt").read_text()

    def test_glücksspiel_item_without_the_palantir_debate(self):
        """V-247045 Erste Beratung: S. 287-291, #page=31; the window ran into Palantir."""
        agenda, pages = _agenda_text(self.TEXT, 31, list(range(287, 292)))

        assert pages == [31, 32, 33, 34, 35]
        assert agenda.startswith("287\nLandtag von Baden-Württemberg")
        assert "Drucksache\n18/191" in agenda and "Glücksspiel" in agenda
        assert "Palantir" not in agenda
        assert "Palantir" in _extract_relevant_pages(self.TEXT, 31)

    def test_a_shifted_anchor_falls_back(self):
        """#page=30 for S. 287-291 would drop page 35 with the referral of 18/191."""
        assert _agenda_text(self.TEXT, 30, list(range(287, 292))) is None

    def test_an_item_past_the_last_page_falls_back(self):
        """The PDF has 49 pages (printed 257-305): a range beyond them means a wrong offset."""
        assert _agenda_text(self.TEXT, 31, list(range(287, 306)))[1] == list(range(31, 50))
        assert _agenda_text(self.TEXT, 31, list(range(287, 307))) is None
        assert _agenda_text(self.TEXT, 60, list(range(287, 292))) is None
