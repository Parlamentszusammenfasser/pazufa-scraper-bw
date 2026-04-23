"""Tests for the BawueBeteiligungScraper."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.parlament import Parlament
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

from bawue.bawue_beteiligung_scraper import DEFAULT_WAHLPERIODE, BawueBeteiligungScraper
from bawue.bawue_dok import LLMMetrics
from bawue.beteiligung_parser import RawBeteiligungDetail, RawBeteiligungProcess

FIXTURES = Path(__file__).parent.parent / "fixtures" / "beteiligung"


def _make_scraper():
    """Create a minimal BawueBeteiligungScraper without full init."""
    from bawue.rate_limiter import AdaptiveRateLimiter

    scraper = object.__new__(BawueBeteiligungScraper)
    scraper._wahlperiode = 17
    scraper._raw_cache = {}
    scraper._client = MagicMock()
    scraper._upload_limiter = AdaptiveRateLimiter(
        initial_delay=0.2, min_delay=0.05, backoff_multiplier=10.0, recovery_factor=0.5
    )
    scraper._published = 0
    scraper._failed = 0
    scraper._skipped = 0
    scraper._failed_items = []
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._llm_metrics = LLMMetrics()
    scraper.session = MagicMock()
    scraper.config = MagicMock()
    return scraper


def _make_detail(
    title: str = "Entbürokratisierung",
    ministry: str = "Ministerium des Inneren, für Digitalisierung und Kommunen",
    pdf_links: list[dict] | None = None,
    comment_deadline: str = "13.11.2025",
    phases: list[str] | None = None,
) -> RawBeteiligungDetail:
    if pdf_links is None:
        pdf_links = [
            {
                "title": "Zweites Gesetz zum Abbau verzichtbarer Formerfordernisse (PDF)",
                "url": "https://beteiligungsportal.baden-wuerttemberg.de/fileadmin/redaktion/beteiligungsportal/IM/251015_Entwurf_Zweites_Gesetz.pdf",
            }
        ]
    if phases is None:
        phases = ["Online-Kommentierung", "Antwort des Ministeriums", "Beratung und Beschluss", "Geltendes Gesetz"]
    return RawBeteiligungDetail(
        title=title,
        ministry=ministry,
        pdf_links=pdf_links,
        comment_deadline=comment_deadline,
        phases=phases,
    )


def _make_process(
    slug: str = "entbuerokratisierung",
    title: str = "Entbürokratisierung",
    status: str = "closed",
) -> RawBeteiligungProcess:
    return RawBeteiligungProcess(
        title=title,
        url=f"/de/mitmachen/lp-17/{slug}",
        slug=slug,
        status=status,
    )


class TestBuildVorgang:
    @pytest.mark.asyncio
    async def test_builds_vorgang_with_preparl_regent_station(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang is not None
        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PREPARL_MINUS_REGENT

    @pytest.mark.asyncio
    async def test_deterministic_api_id(self):
        scraper = _make_scraper()
        detail = _make_detail()
        v1 = await scraper._build_vorgang("entbuerokratisierung", detail)
        v2 = await scraper._build_vorgang("entbuerokratisierung", detail)
        assert v1.api_id == v2.api_id

    @pytest.mark.asyncio
    async def test_different_slugs_produce_different_api_ids(self):
        scraper = _make_scraper()
        v1 = await scraper._build_vorgang("entbuerokratisierung", _make_detail(title="A"))
        v2 = await scraper._build_vorgang("rettungsdienstplanverordnung", _make_detail(title="B"))
        assert v1.api_id != v2.api_id

    @pytest.mark.asyncio
    async def test_api_id_uses_namespace_url(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)
        expected = str(uuid5(NAMESPACE_URL, "beteiligung-entbuerokratisierung"))
        assert str(vorgang.api_id) == expected

    @pytest.mark.asyncio
    async def test_documents_have_preparl_entwurf_type(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        station = vorgang.stationen[0]
        assert len(station.dokumente) == 1
        doc = station.dokumente[0].actual_instance
        assert doc.typ == Doktyp.PREPARL_MINUS_ENTWURF

    @pytest.mark.asyncio
    async def test_ministry_as_initiator(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert len(vorgang.initiatoren) == 1
        assert vorgang.initiatoren[0].organisation == "Ministerium des Inneren, für Digitalisierung und Kommunen"

    @pytest.mark.asyncio
    async def test_gremium_is_landesregierung(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        gremium = vorgang.stationen[0].gremium
        assert gremium.parlament == Parlament.BW
        assert gremium.name == "regierung"
        assert gremium.wahlperiode == 17

    @pytest.mark.asyncio
    async def test_vorgangstyp_is_gg_land_parl(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)
        assert vorgang.typ == Vorgangstyp.GG_MINUS_LAND_MINUS_PARL

    @pytest.mark.asyncio
    async def test_kurztitel_is_slug(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)
        assert vorgang.kurztitel == "entbuerokratisierung"

    @pytest.mark.asyncio
    async def test_ids_contain_beteiligung_url(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)
        assert vorgang.ids is not None
        assert len(vorgang.ids) == 1
        assert "beteiligungsportal" in vorgang.ids[0].id

    @pytest.mark.asyncio
    async def test_zp_start_is_timezone_aware(self):
        """Naive datetimes cause API 422 'premature end of input' errors."""
        scraper = _make_scraper()
        detail = _make_detail(comment_deadline="13.11.2025")
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        station = vorgang.stationen[0]
        assert station.zp_start.tzinfo is not None
        assert station.zp_start == datetime(2025, 11, 13, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_no_deadline_returns_none(self):
        """Missing comment_deadline means station can't be built — skip entire Vorgang."""
        scraper = _make_scraper()
        detail = _make_detail(comment_deadline=None)
        result = await scraper._build_vorgang("test-slug", detail)
        assert result is None

    @pytest.mark.asyncio
    async def test_unparseable_deadline_returns_none(self):
        """Unparseable comment_deadline means station can't be built — skip entire Vorgang."""
        scraper = _make_scraper()
        detail = _make_detail(comment_deadline="not-a-date")
        result = await scraper._build_vorgang("test-slug", detail)
        assert result is None

    @pytest.mark.asyncio
    async def test_document_timestamps_are_timezone_aware(self):
        scraper = _make_scraper()
        detail = _make_detail(comment_deadline="13.11.2025")
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        doc = vorgang.stationen[0].dokumente[0].actual_instance
        assert doc.zp_modifiziert.tzinfo is not None
        assert doc.zp_referenz.tzinfo is not None

    @pytest.mark.asyncio
    async def test_no_pdfs_returns_none(self):
        scraper = _make_scraper()
        detail = _make_detail(pdf_links=[])
        result = await scraper._build_vorgang("klima-register", detail)
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_pdfs(self):
        scraper = _make_scraper()
        detail = _make_detail(
            pdf_links=[
                {"title": "Entwurf A (PDF)", "url": "https://example.com/a.pdf"},
                {"title": "Entwurf B (PDF)", "url": "https://example.com/b.pdf"},
            ]
        )
        vorgang = await scraper._build_vorgang("test-slug", detail)
        assert len(vorgang.stationen[0].dokumente) == 2


def _make_enriched_dok(url: str = "https://example.com/test.pdf"):
    """Create a Dokument suitable for EnrichmentResult (passes Pydantic validation)."""
    from openapi_client.models.autor import Autor
    from openapi_client.models.dokument import Dokument

    return Dokument(
        titel="Enriched",
        volltext="text",
        hash="abc",
        typ=Doktyp.PREPARL_MINUS_ENTWURF,
        zp_modifiziert=datetime(2025, 11, 13, tzinfo=UTC),
        zp_referenz=datetime(2025, 11, 13, tzinfo=UTC),
        link=url,
        autoren=[Autor(organisation="Test")],
    )


class TestTrojanergefahr:
    @pytest.mark.asyncio
    async def test_trojanergefahr_set_on_station_when_llm_enabled(self):
        """LLM enrichment returns trojanergefahr → Station gets the value."""
        from bawue.bawue_dok import EnrichmentResult

        scraper = _make_scraper()
        scraper._llm_enabled = True
        scraper._llm = AsyncMock()
        scraper._llm_model = "gpt-5-nano"
        scraper._llm_truncate_tokens = 12000

        mock_result = EnrichmentResult(
            dokument=_make_enriched_dok(),
            trojanergefahr=5,
        )

        detail = _make_detail()

        with patch("bawue.bawue_dok.enrich_dokument", new_callable=AsyncMock, return_value=mock_result):
            vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang.stationen[0].trojanergefahr == 5

    @pytest.mark.asyncio
    async def test_trojanergefahr_none_when_llm_disabled(self):
        """LLM disabled → Station.trojanergefahr is None."""
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang.stationen[0].trojanergefahr is None

    @pytest.mark.asyncio
    async def test_trojanergefahr_max_across_multiple_documents(self):
        """Multiple docs with different trojanergefahr → Station gets max."""
        from bawue.bawue_dok import EnrichmentResult

        scraper = _make_scraper()
        scraper._llm_enabled = True
        scraper._llm = AsyncMock()
        scraper._llm_model = "gpt-5-nano"
        scraper._llm_truncate_tokens = 12000

        results = [
            EnrichmentResult(dokument=_make_enriched_dok("https://example.com/a.pdf"), trojanergefahr=3),
            EnrichmentResult(dokument=_make_enriched_dok("https://example.com/b.pdf"), trojanergefahr=8),
        ]

        detail = _make_detail(
            pdf_links=[
                {"title": "Entwurf A (PDF)", "url": "https://example.com/a.pdf"},
                {"title": "Entwurf B (PDF)", "url": "https://example.com/b.pdf"},
            ]
        )

        with patch(
            "bawue.bawue_dok.enrich_dokument",
            new_callable=AsyncMock,
            side_effect=results,
        ):
            vorgang = await scraper._build_vorgang("test-slug", detail)

        assert vorgang.stationen[0].trojanergefahr == 8


class TestListingPageExtractor:
    @pytest.mark.asyncio
    async def test_returns_slugs(self):
        scraper = _make_scraper()
        processes = [
            _make_process(slug="entbuerokratisierung"),
            _make_process(slug="rettungsdienstplanverordnung"),
        ]

        with patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=processes):
            slugs = await scraper.listing_page_extractor("lp-17")

        assert slugs == ["entbuerokratisierung", "rettungsdienstplanverordnung"]

    @pytest.mark.asyncio
    async def test_populates_raw_cache(self):
        scraper = _make_scraper()
        processes = [_make_process(slug="test-slug")]

        with patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=processes):
            await scraper.listing_page_extractor("lp-17")

        assert "test-slug" in scraper._raw_cache


class TestItemExtractor:
    @pytest.mark.asyncio
    async def test_consumes_cache(self):
        scraper = _make_scraper()
        process = _make_process()
        scraper._raw_cache["entbuerokratisierung"] = process
        detail = _make_detail()

        with patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=detail.title):
            scraper._client.fetch_process_detail.return_value = "<html></html>"
            with patch("bawue.bawue_beteiligung_scraper.parse_process_detail", return_value=detail):
                vorgang = await scraper.item_extractor("entbuerokratisierung")

        assert vorgang is not None
        assert "entbuerokratisierung" not in scraper._raw_cache

    @pytest.mark.asyncio
    async def test_missing_cache_returns_none(self):
        scraper = _make_scraper()

        result = await scraper.item_extractor("missing-slug")

        assert result is None


class TestRunSummary:
    @pytest.mark.asyncio
    async def test_summary_printed_to_stdout(self, capsys):
        scraper = _make_scraper()

        with patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Beteiligung Run Summary ===" in captured.out

    @pytest.mark.asyncio
    async def test_summary_shows_published_count(self, capsys):
        scraper = _make_scraper()
        mock_vorgang = MagicMock()
        mock_config = MagicMock()
        mock_config.dry_run = False
        scraper.config = mock_config
        scraper.scraper_id = "test-scraper-id"

        with patch("bawue.upload_throttle.openapi_client") as mock_oapi:
            mock_oapi.ApiClient.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_oapi.ApiClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_oapi.api.collector_schnittstellen_api.CollectorSchnittstellenApi.return_value = MagicMock()
            await scraper.send_result(mock_vorgang)
            await scraper.send_result(mock_vorgang)

        with patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Published:" in captured.out
        assert scraper._published == 2

    @pytest.mark.asyncio
    async def test_summary_shows_skipped_count(self, capsys):
        scraper = _make_scraper()
        detail = _make_detail(pdf_links=[])
        scraper._raw_cache["test-slug"] = _make_process(slug="test-slug")

        with (
            patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value="<html></html>"),
            patch("bawue.bawue_beteiligung_scraper.parse_process_detail", return_value=detail),
        ):
            await scraper.item_extractor("test-slug")

        with patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Skipped:" in captured.out
        assert scraper._skipped == 1

    @pytest.mark.asyncio
    async def test_summary_shows_failed_count(self, capsys):
        import openapi_client as real_oapi

        scraper = _make_scraper()
        mock_config = MagicMock()
        mock_config.dry_run = False
        scraper.config = mock_config
        scraper.scraper_id = "test-scraper-id"

        with patch("bawue.upload_throttle.openapi_client") as mock_oapi:
            mock_oapi.ApiException = real_oapi.ApiException
            mock_oapi.ApiClient.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_oapi.ApiClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_api_instance = MagicMock()
            mock_api_instance.vorgang_put.side_effect = real_oapi.ApiException(
                status=500, reason="Internal Server Error"
            )
            mock_oapi.api.collector_schnittstellen_api.CollectorSchnittstellenApi.return_value = mock_api_instance
            await scraper.send_result(MagicMock())

        with patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Failed:" in captured.out
        assert scraper._failed == 1

    @pytest.mark.asyncio
    async def test_summary_still_printed_on_run_failure(self, capsys):
        scraper = _make_scraper()

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=mock_run),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Beteiligung Run Summary ===" in captured.out

    @pytest.mark.asyncio
    async def test_summary_duration_is_human_readable(self, capsys):
        scraper = _make_scraper()

        with patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        duration_line = next(line for line in captured.out.splitlines() if "Duration" in line)
        assert "Duration: 0m 00s" in duration_line

    @pytest.mark.asyncio
    async def test_summary_lists_failed_vorgaenge_with_reason(self, capsys):
        import openapi_client as real_oapi

        scraper = _make_scraper()
        mock_config = MagicMock()
        mock_config.dry_run = False
        scraper.config = mock_config
        scraper.scraper_id = "test-scraper-id"

        item = MagicMock()
        item.api_id = "deadbeef"
        item.kurztitel = "klima-slug"
        item.titel = "Klimaschutzgesetz"

        with patch("bawue.upload_throttle.openapi_client") as mock_oapi:
            mock_oapi.ApiException = real_oapi.ApiException
            mock_oapi.ApiClient.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_oapi.ApiClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_api_instance = MagicMock()
            mock_api_instance.vorgang_put.side_effect = real_oapi.ApiException(
                status=422, reason="Unprocessable Entity"
            )
            mock_oapi.api.collector_schnittstellen_api.CollectorSchnittstellenApi.return_value = mock_api_instance
            await scraper.send_result(item)

        with patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Failed Vorgänge" in captured.out
        failed_block = captured.out.split("Failed Vorgänge", 1)[1]
        assert "klima-slug" in failed_block
        assert "Klimaschutzgesetz" in failed_block
        assert "422" in failed_block


class TestRunDurationLog:
    @pytest.mark.asyncio
    async def test_logs_completed_in_on_success(self, caplog):
        scraper = _make_scraper()

        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()),
            caplog.at_level(logging.INFO, logger="bawue.bawue_beteiligung_scraper"),
        ):
            await scraper.run()

        assert any("Completed in" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_logs_completed_in_on_failure(self, caplog):
        scraper = _make_scraper()

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=mock_run),
            caplog.at_level(logging.INFO, logger="bawue.bawue_beteiligung_scraper"),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        assert any("Completed in" in msg for msg in caplog.messages)


class TestInit:
    def test_init_reads_wahlperiode_from_config(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text("[beteiligung]\nwahlperiode = 16\n")

        mock_config = MagicMock()
        mock_config.config_file = str(config_file)
        mock_config.collector_id = "00000000-0000-0000-0000-000000000001"
        mock_config.llm_provider_key = None
        mock_config.llm_provider_base_url = None

        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.__init__", return_value=None),
            patch("bawue.bawue_beteiligung_scraper.BeteiligungClient"),
        ):
            scraper = BawueBeteiligungScraper(mock_config, MagicMock())

        assert scraper._wahlperiode == 16

    def test_init_uses_default_wahlperiode(self):
        mock_config = MagicMock()
        mock_config.config_file = None
        mock_config.collector_id = "00000000-0000-0000-0000-000000000001"
        mock_config.llm_provider_key = None
        mock_config.llm_provider_base_url = None

        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.__init__", return_value=None),
            patch("bawue.bawue_beteiligung_scraper.BeteiligungClient"),
        ):
            scraper = BawueBeteiligungScraper(mock_config, MagicMock())

        assert scraper._wahlperiode == DEFAULT_WAHLPERIODE

    def test_load_toml_section_returns_empty_on_no_file(self):
        from bawue.config_loader import load_toml_section

        mock_config = MagicMock()
        mock_config.config_file = None

        assert load_toml_section(mock_config, "beteiligung") == {}

    def test_load_toml_section_returns_empty_on_bad_file(self, tmp_path, caplog):
        from bawue.config_loader import load_toml_section

        mock_config = MagicMock()
        mock_config.config_file = str(tmp_path / "nonexistent.toml")

        with caplog.at_level(logging.WARNING, logger="bawue.config_loader"):
            result = load_toml_section(mock_config, "beteiligung")

        assert result == {}
        assert any("Could not load" in msg for msg in caplog.messages)
