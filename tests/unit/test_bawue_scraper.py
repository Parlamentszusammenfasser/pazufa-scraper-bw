"""Tests for the BawueVorgaengeScraper item_extractor logic."""

import logging
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

from bawue.bawue_vorgaenge_scraper import (
    DEFAULT_ENABLED_VORGANGSTYPEN,
    DEFAULT_WAHLPERIODE,
    BawueVorgaengeScraper,
    _fallback_date_from_year,
    _parse_autoren,
    _parse_fundstelle_date,
)


def _make_raw_vorgang(
    vid: str,
    titel: str = "Test Gesetz",
    vorgangstyp: str = "Gesetzgebung",
    initiative: str = "Fraktion GRÜNE",
    fundstellen: list[dict] | None = None,
) -> dict:
    """Create a minimal raw Vorgang dict as returned by ParlisClient."""
    if fundstellen is None:
        fundstellen = [
            {
                "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                "datum": "04.02.2026",
                "drucksache": "17/10266",
                "station_typ": "Gesetzentwurf",
                "seiten": 13,
                "pdf_url": "https://www.landtag-bw.de/resource/blob/12345/doc.pdf",
            },
            {
                "raw": "Erste Beratung   Plenarprotokoll 17/141 05.02.2026",
                "datum": "05.02.2026",
                "plenarprotokoll": "17/141",
                "station_typ": "Erste Beratung",
                "pdf_url": "",
            },
        ]
    return {
        "titel": titel,
        "vorgangs_id": vid,
        "Vorgangstyp": vorgangstyp,
        "Initiative": initiative,
        "fundstellen_parsed": fundstellen,
    }


@pytest.fixture()
def scraper_build_vorgang():
    """Return the _build_vorgang method without needing full scraper initialization."""
    # We test _build_vorgang directly since it's the core domain conversion logic.
    # Creating the full scraper requires aiohttp session and CollectorConfiguration.
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = 17
    return scraper._build_vorgang


class TestBuildVorgang:
    def test_builds_framework_vorgang(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-001", titel="Testgesetz")
        vorgang = scraper_build_vorgang(raw)

        assert vorgang.titel == "Testgesetz"
        assert str(vorgang.api_id)  # UUID generated
        assert len(vorgang.stationen) == 2
        assert vorgang.ids is not None
        assert vorgang.ids[0].id == "V-001"
        assert vorgang.ids[0].typ == "vorgnr"

    def test_deterministic_api_id(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-001")
        v1 = scraper_build_vorgang(raw)
        v2 = scraper_build_vorgang(raw)
        assert v1.api_id == v2.api_id

    def test_different_ids_produce_different_api_ids(self, scraper_build_vorgang):
        v1 = scraper_build_vorgang(_make_raw_vorgang("V-001"))
        v2 = scraper_build_vorgang(_make_raw_vorgang("V-002"))
        assert v1.api_id != v2.api_id

    def test_gesetzentwurf_from_landesregierung(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-010",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  01.03.2026 Drucksache 17/11000   (5 S.)",
                    "datum": "01.03.2026",
                    "drucksache": "17/11000",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 5,
                    "pdf_url": "https://example.com/doc.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert vorgang.typ == Vorgangstyp.GG_MINUS_LAND_MINUS_PARL
        assert vorgang.initiatoren[0].organisation == "Landesregierung"

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PREPARL_MINUS_REGENT
        assert station.dokumente[0].actual_instance.typ == Doktyp.PREPARL_MINUS_ENTWURF
        assert station.dokumente[0].actual_instance.drucksnr == "17/11000"

    def test_plenarprotokoll_fundstelle_creates_plenum_gremium(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-020",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_MINUS_VOLLVLSGN
        assert station.gremium.name == "Plenum"
        assert station.dokumente == []

    def test_plenarprotokoll_lesung_gets_redeprotokoll_doktyp(self, scraper_build_vorgang):
        """Lesung stations with a Plenarprotokoll PDF should get Doktyp.REDEPROTOKOLL."""
        raw = _make_raw_vorgang(
            "V-021",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/12345/plenar.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_MINUS_VOLLVLSGN
        assert station.dokumente[0].actual_instance.typ == Doktyp.REDEPROTOKOLL

    def test_ausschuss_fundstelle_creates_committee_gremium(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-030",
            fundstellen=[
                {
                    "raw": "Beschlussempfehlung und Bericht    Ausschuss für Wirtschaft"
                    "  02.02.2026 Drucksache 17/10210",
                    "datum": "02.02.2026",
                    "drucksache": "17/10210",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_MINUS_AUSSCHBER
        assert station.gremium.name == "Ausschuss für Wirtschaft"
        assert station.dokumente[0].actual_instance.typ == Doktyp.BESCHLUSSEMPF

    def test_empty_fundstellen_produces_no_stations(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-040", fundstellen=[])
        vorgang = scraper_build_vorgang(raw)

        assert vorgang.stationen == []

    def test_missing_initiative_falls_back_to_fundstelle_autor(self, scraper_build_vorgang):
        """When PARLIS omits the Initiative field (e.g. Haushaltsgesetzgebung),
        infer initiatoren from the first Fundstelle's autor_text."""
        raw = _make_raw_vorgang(
            "V-050",
            initiative="",
            vorgangstyp="Haushaltsgesetzgebung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  25.11.2025 Drucksache 17/9919   (16 S.)",
                    "datum": "25.11.2025",
                    "drucksache": "17/9919",
                    "station_typ": "Gesetzentwurf",
                    "autor_text": "Landesregierung",
                    "seiten": 16,
                    "pdf_url": "https://example.com/doc.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.initiatoren) == 1
        assert vorgang.initiatoren[0].organisation == "Landesregierung"

    def test_missing_initiative_no_fundstellen_produces_empty_initiatoren(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-051", initiative="", fundstellen=[])
        vorgang = scraper_build_vorgang(raw)

        assert vorgang.initiatoren == []

    def test_missing_initiative_fundstelle_without_autor_produces_empty(self, scraper_build_vorgang):
        """When Initiative is missing AND Fundstellen have no autor_text, initiatoren stays empty."""
        raw = _make_raw_vorgang(
            "V-052",
            initiative="",
            fundstellen=[
                {
                    "raw": "Bekanntmachung  Gesetzblatt 2022 Nr. 37",
                    "datum": "01.01.2022",
                    "station_typ": "Bekanntmachung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert vorgang.initiatoren == []

    def test_gremium_uses_parlament_bw(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-060",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test  01.01.2026",
                    "datum": "01.01.2026",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert vorgang.stationen[0].gremium.parlament.value == "BW"

    def test_fallback_to_raw_text_when_station_typ_missing(self, scraper_build_vorgang):
        """When regex fails to extract station_typ, raw text is used for enum mapping."""
        raw = _make_raw_vorgang(
            "V-070",
            fundstellen=[
                {
                    "raw": "Gesetzesbeschluss des Landtags 04.02.2026 Drucksache 17/10254",
                    "datum": "04.02.2026",
                    "drucksache": "17/10254",
                    # station_typ intentionally missing (single-space regex failure)
                    "pdf_url": "https://example.com/beschluss.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_MINUS_AKZEPTANZ
        assert station.dokumente[0].actual_instance.typ == Doktyp.MITTEILUNG

    def test_fallback_gesetz_maps_to_postparl(self, scraper_build_vorgang):
        """Raw text fallback also works for Gesetzblatt entries."""
        raw = _make_raw_vorgang(
            "V-071",
            fundstellen=[
                {
                    "raw": "Gesetz Gesetzblatt für Baden-Württemberg 2026 Nr. 20  S. 1  10.02.2026",
                    "datum": "10.02.2026",
                    # station_typ intentionally missing
                    "pdf_url": "https://example.com/gesetz.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.POSTPARL_MINUS_GSBLT

    def test_normal_station_typ_still_works(self, scraper_build_vorgang):
        """Normal case: regex-extracted station_typ is used (no fallback)."""
        raw = _make_raw_vorgang(
            "V-072",
            fundstellen=[
                {
                    "raw": "Gesetzesbeschluss des Landtags  04.02.2026 Drucksache 17/10254",
                    "datum": "04.02.2026",
                    "drucksache": "17/10254",
                    "station_typ": "Gesetzesbeschluss des Landtags",
                    "pdf_url": "https://example.com/beschluss.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_MINUS_AKZEPTANZ
        assert station.dokumente[0].actual_instance.typ == Doktyp.MITTEILUNG


def _make_scraper_with_mock_parlis(search_return=None, wahlperiode_start=date(2021, 4, 26)):
    """Create a minimal BawueVorgaengeScraper without full init, with a mock ParlisClient."""
    from bawue.rate_limiter import AdaptiveRateLimiter

    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = 17
    scraper._wahlperiode_start_date = wahlperiode_start
    scraper._raw_cache = {}
    scraper._parlis = MagicMock()
    scraper._parlis.search.return_value = search_return or []
    scraper._enabled_vorgangstypen = frozenset(DEFAULT_ENABLED_VORGANGSTYPEN)
    scraper._published = 0
    scraper._failed = 0
    scraper._skipped = 0
    scraper._by_type = {}
    scraper._upload_limiter = AdaptiveRateLimiter(
        initial_delay=0.2, min_delay=0.05, backoff_multiplier=10.0, recovery_factor=0.5
    )
    mock_config = MagicMock()
    mock_config.dry_run = False
    scraper.config = mock_config
    scraper.scraper_id = "test-scraper-id"
    return scraper


class TestListingPageExtractor:
    @pytest.mark.asyncio
    async def test_search_runs_via_to_thread(self):
        scraper = _make_scraper_with_mock_parlis(search_return=[])

        with patch("bawue.bawue_vorgaenge_scraper.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = []
            await scraper.listing_page_extractor("Gesetzgebung")

        mock_to_thread.assert_called_once()
        args = mock_to_thread.call_args[0]
        assert args[0] is scraper._parlis.search
        assert args[1] == "Gesetzgebung"
        assert isinstance(args[2], date)
        assert isinstance(args[3], date)

    @pytest.mark.asyncio
    async def test_uses_wahlperiode_start_date_not_lookback(self):
        start = date(2021, 4, 26)
        scraper = _make_scraper_with_mock_parlis(search_return=[], wahlperiode_start=start)

        with patch("bawue.bawue_vorgaenge_scraper.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.return_value = []
            await scraper.listing_page_extractor("Gesetzgebung")

        args = mock_to_thread.call_args[0]
        date_from = args[2]
        assert date_from == start, f"Expected wahlperiode start {start}, got {date_from}"

    @pytest.mark.asyncio
    async def test_populates_raw_cache(self):
        raw = [_make_raw_vorgang("V-200"), _make_raw_vorgang("V-201")]
        scraper = _make_scraper_with_mock_parlis()

        with patch("bawue.bawue_vorgaenge_scraper.asyncio.to_thread", return_value=raw):
            ids = await scraper.listing_page_extractor("Gesetzgebung")

        assert ids == ["V-200", "V-201"]
        assert "V-200" in scraper._raw_cache
        assert "V-201" in scraper._raw_cache


class TestItemExtractor:
    @pytest.mark.asyncio
    async def test_consumes_cache_entry(self):
        scraper = _make_scraper_with_mock_parlis()
        scraper._raw_cache["V-300"] = _make_raw_vorgang("V-300")

        vorgang = await scraper.item_extractor("V-300")

        assert vorgang is not None
        assert vorgang.titel == "Test Gesetz"
        assert "V-300" not in scraper._raw_cache

    @pytest.mark.asyncio
    async def test_cache_empty_after_all_consumed(self):
        scraper = _make_scraper_with_mock_parlis()
        scraper._raw_cache["V-310"] = _make_raw_vorgang("V-310")
        scraper._raw_cache["V-311"] = _make_raw_vorgang("V-311")

        await scraper.item_extractor("V-310")
        await scraper.item_extractor("V-311")

        assert scraper._raw_cache == {}

    @pytest.mark.asyncio
    async def test_missing_cache_entry_returns_none(self):
        scraper = _make_scraper_with_mock_parlis()

        result = await scraper.item_extractor("V-MISSING")

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_postparl_only_vorgang(self):
        """Vorgänge with only post-parliamentary stations (e.g. Bekanntmachungen) are skipped."""
        scraper = _make_scraper_with_mock_parlis()
        scraper._raw_cache["V-900"] = _make_raw_vorgang(
            "V-900",
            titel="Bekanntmachung über das Inkrafttreten des Staatsvertrages",
            initiative="Staatsministerium",
            fundstellen=[
                {
                    "raw": "Bekanntmachung  Staatsministerium  12.05.2021 Gesetzblatt Nr. 15  S. 400",
                    "datum": "12.05.2021",
                    "station_typ": "Bekanntmachung",
                    "pdf_url": "",
                },
            ],
        )

        result = await scraper.item_extractor("V-900")

        assert result is None
        assert scraper._skipped == 1

    @pytest.mark.asyncio
    async def test_does_not_skip_vorgang_with_parliamentary_stations(self):
        """Normal Vorgänge with parliamentary stations are not skipped."""
        scraper = _make_scraper_with_mock_parlis()
        scraper._raw_cache["V-901"] = _make_raw_vorgang("V-901")

        result = await scraper.item_extractor("V-901")

        assert result is not None
        assert scraper._skipped == 0


class TestPlaceholderDate:
    def test_zero_day_month_falls_back_to_year_start(self, scraper_build_vorgang):
        """PARLIS uses 00.00.YYYY as a placeholder when only the year is known."""
        raw = _make_raw_vorgang(
            "V-600",
            fundstellen=[
                {
                    "raw": "Antrag    Fraktion GRÜNE  00.00.2028 Drucksache 17/99999",
                    "datum": "00.00.2028",
                    "drucksache": "17/99999",
                    "station_typ": "Antrag",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.zp_start.year == 2028
        assert station.zp_start.month == 1
        assert station.zp_start.day == 1
        assert station.zp_start.tzinfo is not None, "zp_start must be timezone-aware to avoid API 422 errors"

    def test_zero_day_month_logs_warning(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17

        raw = _make_raw_vorgang(
            "V-601",
            fundstellen=[
                {
                    "raw": "Antrag    Fraktion GRÜNE  00.00.2028 Drucksache 17/99999",
                    "datum": "00.00.2028",
                    "drucksache": "17/99999",
                    "station_typ": "Antrag",
                    "pdf_url": "",
                },
            ],
        )

        with caplog.at_level(logging.WARNING, logger="bawue.bawue_vorgaenge_scraper"):
            scraper._build_vorgang(raw)

        assert any("00.00.2028" in msg for msg in caplog.messages)


class TestDatetimesAreTimezoneAware:
    """All zp_start/zp_modifiziert/zp_referenz datetimes must be timezone-aware.

    The API rejects naive datetimes (serialized without +00:00 suffix) with a 422
    'premature end of input' error on the zp_start field.
    """

    def test_normal_date_is_timezone_aware(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-700",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  15.06.2025 Drucksache 17/12345",
                    "datum": "15.06.2025",
                    "drucksache": "17/12345",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        station = vorgang.stationen[0]

        assert station.zp_start.tzinfo is not None
        assert station.zp_start == datetime(2025, 6, 15, tzinfo=UTC)

    def test_missing_date_skips_station(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-701",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test",
                    "datum": "",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                    "drucksache": "17/10266",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 0

    def test_placeholder_date_fallback_is_timezone_aware(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-702",
            fundstellen=[
                {
                    "raw": "Antrag    Fraktion GRÜNE  00.00.2028 Drucksache 17/99999",
                    "datum": "00.00.2028",
                    "drucksache": "17/99999",
                    "station_typ": "Antrag",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        station = vorgang.stationen[0]

        assert station.zp_start.tzinfo is not None
        assert station.zp_start == datetime(2028, 1, 1, tzinfo=UTC)


class TestDatetimeFallbackWarning:
    def test_missing_date_logs_error(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17

        raw = _make_raw_vorgang(
            "V-400",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test",
                    "datum": "",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                    "drucksache": "17/10266",
                },
            ],
        )

        with caplog.at_level(logging.ERROR, logger="bawue.bawue_vorgaenge_scraper"):
            scraper._build_vorgang(raw)

        assert any("No date found for Fundstelle" in msg for msg in caplog.messages)

    def test_missing_date_logs_drucksache_number(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17

        raw = _make_raw_vorgang(
            "V-401",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test",
                    "datum": "",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                    "drucksache": "17/10266",
                },
            ],
        )

        with caplog.at_level(logging.ERROR, logger="bawue.bawue_vorgaenge_scraper"):
            scraper._build_vorgang(raw)

        assert any("17/10266" in msg for msg in caplog.messages)


class TestRunSummary:
    @pytest.mark.asyncio
    async def test_summary_printed_to_stdout(self, capsys):
        scraper = _make_scraper_with_mock_parlis()

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Vorgänge Run Summary ===" in captured.out

    @pytest.mark.asyncio
    async def test_summary_shows_published_count(self, capsys):
        scraper = _make_scraper_with_mock_parlis()
        mock_vorgang = MagicMock()

        with patch("bawue.upload_throttle.openapi_client") as mock_oapi:
            mock_api_instance = MagicMock()
            mock_oapi.ApiClient.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_oapi.ApiClient.return_value.__exit__ = MagicMock(return_value=False)
            mock_oapi.api.collector_schnittstellen_api.CollectorSchnittstellenApi.return_value = mock_api_instance
            await scraper.send_result(mock_vorgang)
            await scraper.send_result(mock_vorgang)

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Published:" in captured.out
        assert scraper._published == 2

    @pytest.mark.asyncio
    async def test_summary_shows_failed_count(self, capsys):
        import openapi_client as real_oapi

        scraper = _make_scraper_with_mock_parlis()

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

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Failed:" in captured.out
        assert scraper._failed == 1

    @pytest.mark.asyncio
    async def test_summary_shows_by_type(self, capsys):
        scraper = _make_scraper_with_mock_parlis()
        scraper._by_type = {"Kleine Anfrage": 3}

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Kleine Anfrage" in captured.out
        assert "3" in captured.out

    @pytest.mark.asyncio
    async def test_summary_still_printed_on_run_failure(self, capsys):
        scraper = _make_scraper_with_mock_parlis()

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=mock_run),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Vorgänge Run Summary ===" in captured.out


class TestRunDurationLog:
    @pytest.mark.asyncio
    async def test_logs_completed_in_on_success(self, caplog):
        from unittest.mock import AsyncMock, MagicMock

        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = DEFAULT_WAHLPERIODE
        scraper._published = 0
        scraper._failed = 0
        scraper._skipped = 0
        scraper._by_type = {}

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode", new=MagicMock()),
            caplog.at_level(logging.INFO, logger="bawue.bawue_vorgaenge_scraper"),
        ):
            await scraper.run()

        assert any("Completed in" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_logs_completed_in_on_failure(self, caplog):
        from unittest.mock import AsyncMock, MagicMock

        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = DEFAULT_WAHLPERIODE
        scraper._published = 0
        scraper._failed = 0
        scraper._skipped = 0
        scraper._by_type = {}

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=mock_run),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode", new=MagicMock()),
            caplog.at_level(logging.INFO, logger="bawue.bawue_vorgaenge_scraper"),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        assert any("Completed in" in msg for msg in caplog.messages)


class TestParseAutoren:
    def test_single_author(self):
        result = _parse_autoren("Fraktion GRÜNE")
        assert len(result) == 1
        assert result[0].organisation == "Fraktion GRÜNE"

    def test_comma_separated(self):
        result = _parse_autoren("Fraktion GRÜNE, Fraktion der CDU")
        assert len(result) == 2
        assert result[0].organisation == "Fraktion GRÜNE"
        assert result[1].organisation == "Fraktion der CDU"

    def test_empty_string(self):
        assert _parse_autoren("") == []

    def test_whitespace_only(self):
        assert _parse_autoren("   ") == []


class TestBuildStationAutoren:
    def test_fundstelle_autor_text_used(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-500",
            initiative="SPD",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "autor_text": "Fraktion GRÜNE",
                    "pdf_url": "https://example.com/doc.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0].actual_instance
        assert len(doc.autoren) == 1
        assert doc.autoren[0].organisation == "Fraktion GRÜNE"

    def test_fallback_to_initiative(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-501",
            initiative="SPD",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    04.02.2026 Drucksache 17/10266",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/doc.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0].actual_instance
        assert len(doc.autoren) == 1
        assert doc.autoren[0].organisation == "SPD"

    def test_no_autor_text_no_initiative(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-502",
            initiative="",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    04.02.2026 Drucksache 17/10266",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/doc.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0].actual_instance
        assert doc.autoren == []

    def test_multiple_autoren_from_fundstelle(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-503",
            initiative="SPD",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE, Fraktion der CDU  04.02.2026 Drucksache 17/10266",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "autor_text": "Fraktion GRÜNE, Fraktion der CDU",
                    "pdf_url": "https://example.com/doc.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0].actual_instance
        assert len(doc.autoren) == 2
        assert doc.autoren[0].organisation == "Fraktion GRÜNE"
        assert doc.autoren[1].organisation == "Fraktion der CDU"


class TestStationMerging:
    """Tests for merging consecutive same-type stations."""

    def test_consecutive_same_type_same_gremium_merged(self, scraper_build_vorgang):
        """Two consecutive AKZEPTANZ fundstellen with same gremium → 1 station with 2 documents."""
        raw = _make_raw_vorgang(
            "V-800",
            fundstellen=[
                {
                    "raw": "Gesetzesbeschluss des Landtags      05.02.2026 Drucksache 17/10267",
                    "datum": "05.02.2026",
                    "drucksache": "17/10267",
                    "station_typ": "Gesetzesbeschluss",
                    "pdf_url": "https://example.com/beschluss1.pdf",
                },
                {
                    "raw": "Gesetzesbeschluss des Landtags      06.02.2026 Drucksache 17/10268",
                    "datum": "06.02.2026",
                    "drucksache": "17/10268",
                    "station_typ": "Gesetzesbeschluss",
                    "pdf_url": "https://example.com/beschluss2.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_AKZEPTANZ
        assert len(vorgang.stationen[0].dokumente) == 2

    def test_consecutive_same_type_different_gremium_not_merged(self, scraper_build_vorgang):
        """Two AUSSCHBER fundstellen with different committee names → 2 stations."""
        raw = _make_raw_vorgang(
            "V-801",
            fundstellen=[
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Wirtschaft  01.02.2026 Drucksache 17/10210",
                    "datum": "01.02.2026",
                    "drucksache": "17/10210",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report1.pdf",
                },
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Umwelt  02.02.2026 Drucksache 17/10211",
                    "datum": "02.02.2026",
                    "drucksache": "17/10211",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Umwelt",
                    "pdf_url": "https://example.com/report2.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2

    def test_ausschuss_merge_backwards_no_plenum_between(self, scraper_build_vorgang):
        """Two Ausschuss fundstellen (same committee) separated by a non-plenary station → merged."""
        raw = _make_raw_vorgang(
            "V-802",
            fundstellen=[
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Wirtschaft  01.02.2026 Drucksache 17/10210",
                    "datum": "01.02.2026",
                    "drucksache": "17/10210",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report1.pdf",
                },
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  02.02.2026 Drucksache 17/10266",
                    "datum": "02.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Wirtschaft  03.02.2026 Drucksache 17/10212",
                    "datum": "03.02.2026",
                    "drucksache": "17/10212",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report2.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_MINUS_AUSSCHBER]
        assert len(ausschuss_stationen) == 1
        assert len(ausschuss_stationen[0].dokumente) == 2

    def test_ausschuss_no_merge_across_plenum(self, scraper_build_vorgang):
        """Two Ausschuss fundstellen (same committee) separated by plenary → 2 separate stations."""
        raw = _make_raw_vorgang(
            "V-803",
            fundstellen=[
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Wirtschaft  01.02.2026 Drucksache 17/10210",
                    "datum": "01.02.2026",
                    "drucksache": "17/10210",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report1.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/141 02.02.2026",
                    "datum": "02.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Wirtschaft  03.02.2026 Drucksache 17/10212",
                    "datum": "03.02.2026",
                    "drucksache": "17/10212",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report2.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_MINUS_AUSSCHBER]
        assert len(ausschuss_stationen) == 2

    def test_stellungnahme_still_attaches_after_merge(self, scraper_build_vorgang):
        """Merged station followed by Stellungnahme → Stellungnahme attaches to the merged station."""
        raw = _make_raw_vorgang(
            "V-804",
            fundstellen=[
                {
                    "raw": "Gesetzesbeschluss des Landtags      05.02.2026 Drucksache 17/10267",
                    "datum": "05.02.2026",
                    "drucksache": "17/10267",
                    "station_typ": "Gesetzesbeschluss",
                    "pdf_url": "https://example.com/beschluss1.pdf",
                },
                {
                    "raw": "Gesetzesbeschluss des Landtags      06.02.2026 Drucksache 17/10268",
                    "datum": "06.02.2026",
                    "drucksache": "17/10268",
                    "station_typ": "Gesetzesbeschluss",
                    "pdf_url": "https://example.com/beschluss2.pdf",
                },
                {
                    "raw": "Stellungnahme    Fraktion GRÜNE  10.02.2026 Drucksache 17/10300",
                    "datum": "10.02.2026",
                    "drucksache": "17/10300",
                    "station_typ": "Stellungnahme",
                    "pdf_url": "https://example.com/stellungnahme.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_AKZEPTANZ
        assert len(vorgang.stationen[0].dokumente) == 2
        assert vorgang.stationen[0].stellungnahmen is not None
        assert len(vorgang.stationen[0].stellungnahmen) == 1

    def test_consecutive_vollvlsgn_not_merged_even_with_documents(self, scraper_build_vorgang):
        """Two consecutive plenary readings (Erste + Zweite Beratung) with PDFs → 2 separate stations."""
        raw = _make_raw_vorgang(
            "V-810",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "https://example.com/pp141.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/145 12.02.2026",
                    "datum": "12.02.2026",
                    "plenarprotokoll": "17/145",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp145.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_VOLLVLSGN
        assert vorgang.stationen[1].typ == Stationstyp.PARL_MINUS_VOLLVLSGN

    def test_consecutive_vollvlsgn_ueberweisung_not_merged(self, scraper_build_vorgang):
        """'Erste Beratung' + 'Überweisung' both PARL_VOLLVLSGN → 2 separate stations."""
        raw = _make_raw_vorgang(
            "V-811",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "https://example.com/pp141.pdf",
                },
                {
                    "raw": "Überweisung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Überweisung",
                    "pdf_url": "https://example.com/pp141b.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_VOLLVLSGN
        assert vorgang.stationen[1].typ == Stationstyp.PARL_MINUS_VOLLVLSGN

    def test_no_merge_when_no_documents(self, scraper_build_vorgang):
        """Station without documents (no pdf_url) is not merged but kept as separate station."""
        raw = _make_raw_vorgang(
            "V-805",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "https://example.com/pp141.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/142 06.02.2026",
                    "datum": "06.02.2026",
                    "plenarprotokoll": "17/142",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2


class TestStellungnahmenAsChildren:
    def test_stellungnahme_attaches_to_preceding_station(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-700",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 13,
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Stellungnahme    Fraktion GRÜNE  10.02.2026 Drucksache 17/10300",
                    "datum": "10.02.2026",
                    "drucksache": "17/10300",
                    "station_typ": "Stellungnahme",
                    "pdf_url": "https://example.com/stellungnahme.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].stellungnahmen is not None
        assert len(vorgang.stationen[0].stellungnahmen) == 1
        assert vorgang.stationen[0].stellungnahmen[0].actual_instance.typ == Doktyp.STELLUNGNAHME

    def test_stellungnahme_without_preceding_station_discarded_with_warning(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17

        raw = _make_raw_vorgang(
            "V-701",
            fundstellen=[
                {
                    "raw": "Stellungnahme    Fraktion GRÜNE  10.02.2026 Drucksache 17/10300",
                    "datum": "10.02.2026",
                    "drucksache": "17/10300",
                    "station_typ": "Stellungnahme",
                    "pdf_url": "https://example.com/stellungnahme.pdf",
                },
            ],
        )

        with caplog.at_level(logging.WARNING, logger="bawue.bawue_vorgaenge_scraper"):
            vorgang = scraper._build_vorgang(raw)

        assert len(vorgang.stationen) == 0
        assert any("Stellungnahme" in msg and "V-701" in msg for msg in caplog.messages)


class TestKleineAnfrageHierarchy:
    """Tests for Kleine Anfrage + Stellungnahme pairing."""

    def test_kleine_anfrage_station_type_is_parl_initiativ(self, scraper_build_vorgang):
        """Kleine Anfrage should map to parl-initiativ, not sonstig."""
        raw = _make_raw_vorgang(
            "V-900",
            vorgangstyp="Kleine Anfrage",
            initiative="Dr. Schweickert (FDP/DVP)",
            fundstellen=[
                {
                    "raw": "Kleine Anfrage   Dr. Schweickert (FDP/DVP)  15.01.2026 Drucksache 17/10143  (4 S.)",
                    "datum": "15.01.2026",
                    "drucksache": "17/10143",
                    "station_typ": "Kleine Anfrage",
                    "seiten": 4,
                    "pdf_url": "https://example.com/anfrage.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_INITIATIV
        assert vorgang.stationen[0].dokumente[0].actual_instance.typ == Doktyp.ANFRAGE

    def test_kleine_anfrage_with_stellungnahme(self, scraper_build_vorgang):
        """Stellungnahme attaches as child of Kleine Anfrage station."""
        raw = _make_raw_vorgang(
            "V-901",
            vorgangstyp="Kleine Anfrage",
            initiative="Dr. Schweickert (FDP/DVP)",
            fundstellen=[
                {
                    "raw": "Kleine Anfrage   Dr. Schweickert (FDP/DVP)  15.01.2026 Drucksache 17/10143  (4 S.)",
                    "datum": "15.01.2026",
                    "drucksache": "17/10143",
                    "station_typ": "Kleine Anfrage",
                    "seiten": 4,
                    "pdf_url": "https://example.com/anfrage.pdf",
                },
                {
                    "raw": "Stellungnahme    Ministerium für Verkehr  19.02.2026 Drucksache 17/10240  (5 S.)",
                    "datum": "19.02.2026",
                    "drucksache": "17/10240",
                    "station_typ": "Stellungnahme",
                    "autor_text": "Ministerium für Verkehr",
                    "seiten": 5,
                    "pdf_url": "https://example.com/antwort.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_INITIATIV
        assert vorgang.stationen[0].stellungnahmen is not None
        assert len(vorgang.stationen[0].stellungnahmen) == 1
        assert vorgang.stationen[0].stellungnahmen[0].actual_instance.typ == Doktyp.STELLUNGNAHME

    def test_stellungnahme_without_pdf_still_attaches(self, scraper_build_vorgang):
        """Stellungnahme without PDF URL should still attach as child (empty docs)."""
        raw = _make_raw_vorgang(
            "V-902",
            vorgangstyp="Kleine Anfrage",
            initiative="Dr. Schweickert (FDP/DVP)",
            fundstellen=[
                {
                    "raw": "Kleine Anfrage   Dr. Schweickert (FDP/DVP)  15.01.2026 Drucksache 17/10143  (4 S.)",
                    "datum": "15.01.2026",
                    "drucksache": "17/10143",
                    "station_typ": "Kleine Anfrage",
                    "seiten": 4,
                    "pdf_url": "https://example.com/anfrage.pdf",
                },
                {
                    "raw": "Stellungnahme    Ministerium für Verkehr  19.02.2026 Drucksache 17/10240  (5 S.)",
                    "datum": "19.02.2026",
                    "drucksache": "17/10240",
                    "station_typ": "Stellungnahme",
                    "autor_text": "Ministerium für Verkehr",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        # Should be 1 station (Kleine Anfrage), not 2 (with an empty Stellungnahme station)
        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_INITIATIV


class TestDedupDrucks:
    """Tests for per-station Drucksache deduplication."""

    def test_duplicate_drucksache_removed(self, scraper_build_vorgang):
        """Same Drucksache appearing twice in a station → deduplicated to 1."""
        raw = _make_raw_vorgang(
            "V-910",
            fundstellen=[
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Wirtschaft  01.02.2026 Drucksache 17/10210",
                    "datum": "01.02.2026",
                    "drucksache": "17/10210",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report1.pdf",
                },
                {
                    "raw": "Ausschussberatung   Ausschuss für Wirtschaft  05.02.2026 Drucksache 17/10210",
                    "datum": "05.02.2026",
                    "drucksache": "17/10210",
                    "station_typ": "Ausschussberatung",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report1.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        # Both fundstellen merge into 1 Ausschuss station; dedup removes the duplicate doc
        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_MINUS_AUSSCHBER]
        assert len(ausschuss_stationen) == 1
        assert len(ausschuss_stationen[0].dokumente) == 1

    def test_different_drucksache_kept(self, scraper_build_vorgang):
        """Different Drucksache numbers in same station → both kept."""
        raw = _make_raw_vorgang(
            "V-911",
            fundstellen=[
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Wirtschaft  01.02.2026 Drucksache 17/10210",
                    "datum": "01.02.2026",
                    "drucksache": "17/10210",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report1.pdf",
                },
                {
                    "raw": "Ausschussberatung   Ausschuss für Wirtschaft  05.02.2026 Drucksache 17/10211",
                    "datum": "05.02.2026",
                    "drucksache": "17/10211",
                    "station_typ": "Ausschussberatung",
                    "ausschuss": "Ausschuss für Wirtschaft",
                    "pdf_url": "https://example.com/report2.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_MINUS_AUSSCHBER]
        assert len(ausschuss_stationen) == 1
        assert len(ausschuss_stationen[0].dokumente) == 2

    def test_documents_without_drucksnr_always_kept(self, scraper_build_vorgang):
        """Documents without drucksnr are never deduplicated."""
        raw = _make_raw_vorgang(
            "V-912",
            fundstellen=[
                {
                    "raw": "Gesetzesbeschluss des Landtags      05.02.2026",
                    "datum": "05.02.2026",
                    "station_typ": "Gesetzesbeschluss",
                    "pdf_url": "https://example.com/beschluss1.pdf",
                },
                {
                    "raw": "Gesetzesbeschluss des Landtags      05.02.2026",
                    "datum": "05.02.2026",
                    "station_typ": "Gesetzesbeschluss",
                    "pdf_url": "https://example.com/beschluss2.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        # Both merged into 1 station; no drucksnr → both kept (no dedup key)
        assert len(vorgang.stationen) == 1
        assert len(vorgang.stationen[0].dokumente) == 2


class TestEnabledVorgangstypen:
    def test_default_enabled_vorgangstypen_are_supported_types_only(self):
        assert DEFAULT_ENABLED_VORGANGSTYPEN == [
            "Gesetzgebung",
            "Haushaltsgesetzgebung",
            "Volksantrag",
        ]

    def test_load_toml_section_reads_enabled_vorgangstypen(self, tmp_path):
        from bawue.config_loader import load_toml_section

        config_file = tmp_path / "config.toml"
        config_file.write_text('[bawue]\nenabled-vorgangstypen = ["Gesetzgebung", "Volksantrag"]\n')
        mock_config = MagicMock()
        mock_config.config_file = str(config_file)

        result = load_toml_section(mock_config, "bawue")

        assert result["enabled-vorgangstypen"] == ["Gesetzgebung", "Volksantrag"]

    def test_load_toml_section_returns_empty_when_no_bawue_section(self, tmp_path):
        from bawue.config_loader import load_toml_section

        config_file = tmp_path / "config.toml"
        config_file.write_text("[main]\ncollector-uuid = 'test'\n")
        mock_config = MagicMock()
        mock_config.config_file = str(config_file)

        result = load_toml_section(mock_config, "bawue")

        assert result.get("enabled-vorgangstypen", DEFAULT_ENABLED_VORGANGSTYPEN) == DEFAULT_ENABLED_VORGANGSTYPEN

    @pytest.mark.asyncio
    async def test_listing_page_extractor_drops_unsupported_vorgangstypen(self):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode_start_date = date(2021, 4, 26)
        scraper._enabled_vorgangstypen = frozenset(["Gesetzgebung"])
        scraper._raw_cache = {}
        scraper._by_type = {}
        scraper._skipped = 0
        scraper._parlis = MagicMock()
        scraper._parlis.search.return_value = [
            {"vorgangs_id": "V-001", "Vorgangstyp": "Gesetzgebung"},
            {"vorgangs_id": "V-002", "Vorgangstyp": "Petition"},
            {"vorgangs_id": "V-003", "Vorgangstyp": "Gesetzgebung"},
            {"vorgangs_id": "V-004", "Vorgangstyp": "UnknownType"},
        ]

        result = await scraper.listing_page_extractor("Gesetzgebung")

        assert result == ["V-001", "V-003"]
        assert "V-001" in scraper._raw_cache
        assert "V-003" in scraper._raw_cache
        assert "V-002" not in scraper._raw_cache
        assert "V-004" not in scraper._raw_cache
        assert scraper._skipped == 2


class TestAenderungsantragHandling:
    """Änderungsanträge should be attached as documents to the parl-vollvlsgn station
    where they were discussed, not created as separate stations."""

    def test_aenderungsantrag_attaches_to_next_vollversammlung(self, scraper_build_vorgang):
        """Änderungsantrag before a Beratung should attach its document to that Beratung station."""
        raw = _make_raw_vorgang(
            "V-800",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  26.10.2021 Drucksache 17/1000   (50 S.)",
                    "datum": "26.10.2021",
                    "drucksache": "17/1000",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 50,
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Änderungsanträge    Fraktion der SPD  14.12.2021 Drucksache 17/1210",
                    "datum": "14.12.2021",
                    "drucksache": "17/1210",
                    "station_typ": "Änderungsanträge",
                    "pdf_url": "https://example.com/aenderung.pdf",
                    "autor_text": "Fraktion der SPD",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/23 16.12.2021",
                    "datum": "16.12.2021",
                    "plenarprotokoll": "17/23",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/plenar.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        # Should have 3 stations: preparl-regent + synthetic parl-initiativ + Zweite Beratung
        station_types = [s.typ for s in vorgang.stationen]
        assert station_types == [
            Stationstyp.PREPARL_MINUS_REGENT,
            Stationstyp.PARL_MINUS_INITIATIV,
            Stationstyp.PARL_MINUS_VOLLVLSGN,
        ]

        # The Zweite Beratung station should contain the Änderungsantrag document
        beratung = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_MINUS_VOLLVLSGN)
        drucksnrs = [d.actual_instance.drucksnr for d in beratung.dokumente]
        assert "17/1210" in drucksnrs, "Änderungsantrag document should be attached to Beratung station"

    def test_aenderungsantrag_singular_form(self, scraper_build_vorgang):
        """Singular 'Änderungsantrag' should also be handled."""
        raw = _make_raw_vorgang(
            "V-801",
            fundstellen=[
                {
                    "raw": "Änderungsantrag    Fraktion der SPD  14.12.2021 Drucksache 17/1210",
                    "datum": "14.12.2021",
                    "drucksache": "17/1210",
                    "station_typ": "Änderungsantrag",
                    "pdf_url": "https://example.com/aenderung.pdf",
                    "autor_text": "Fraktion der SPD",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/23 16.12.2021",
                    "datum": "16.12.2021",
                    "plenarprotokoll": "17/23",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_VOLLVLSGN

    def test_aenderungsantrag_attaches_to_preceding_vollversammlung_if_no_next(self, scraper_build_vorgang):
        """If no subsequent vollvlsgn exists, attach to the preceding one."""
        raw = _make_raw_vorgang(
            "V-802",
            fundstellen=[
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/23 16.12.2021",
                    "datum": "16.12.2021",
                    "plenarprotokoll": "17/23",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Änderungsanträge    Fraktion der SPD  16.12.2021 Drucksache 17/1210",
                    "datum": "16.12.2021",
                    "drucksache": "17/1210",
                    "station_typ": "Änderungsanträge",
                    "pdf_url": "https://example.com/aenderung.pdf",
                    "autor_text": "Fraktion der SPD",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_VOLLVLSGN
        drucksnrs = [d.actual_instance.drucksnr for d in vorgang.stationen[0].dokumente]
        assert "17/1210" in drucksnrs

    def test_aenderungsantrag_without_vollversammlung_logs_warning(self, scraper_build_vorgang, caplog):
        """If no vollvlsgn station exists at all, discard with a warning."""
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17

        raw = _make_raw_vorgang(
            "V-803",
            fundstellen=[
                {
                    "raw": "Änderungsanträge    Fraktion der SPD  14.12.2021 Drucksache 17/1210",
                    "datum": "14.12.2021",
                    "drucksache": "17/1210",
                    "station_typ": "Änderungsanträge",
                    "pdf_url": "https://example.com/aenderung.pdf",
                    "autor_text": "Fraktion der SPD",
                },
            ],
        )

        with caplog.at_level(logging.WARNING, logger="bawue.bawue_vorgaenge_scraper"):
            vorgang = scraper._build_vorgang(raw)

        assert len(vorgang.stationen) == 0
        assert any("Änderungsantr" in msg for msg in caplog.messages)


class TestEntschliessungsantragHandling:
    """Entschließungsanträge should be discarded entirely."""

    def test_entschliessungsantrag_discarded(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-810",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 13,
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Entschließungsantrag    Fraktion der FDP/DVP  16.12.2021 Drucksache 17/1215",
                    "datum": "16.12.2021",
                    "drucksache": "17/1215",
                    "station_typ": "Entschließungsantrag",
                    "pdf_url": "https://example.com/entschliessung.pdf",
                    "autor_text": "Fraktion der FDP/DVP",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_MINUS_INITIATIV
        # Entschließungsantrag document should NOT appear anywhere
        all_drucksnrs = [d.actual_instance.drucksnr for s in vorgang.stationen for d in s.dokumente]
        assert "17/1215" not in all_drucksnrs


class TestAktuellerStandAblehnung:
    """Tests for synthesizing a parl-ablehnung station from 'Aktueller Stand: Abgelehnt'."""

    def test_abgelehnt_appends_ablehnung_station(self, scraper_build_vorgang):
        """When Aktueller Stand is 'Abgelehnt', a parl-ablehnung station is appended."""
        raw = _make_raw_vorgang(
            "V-215352",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion der AfD  01.12.2021 Drucksache 17/1352",
                    "datum": "01.12.2021",
                    "drucksache": "17/1352",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/33 23.03.2022",
                    "datum": "23.03.2022",
                    "plenarprotokoll": "17/33",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 3
        ablehnung = vorgang.stationen[-1]
        assert ablehnung.typ == Stationstyp.PARL_MINUS_ABLEHNUNG
        # Uses the date of the last station (Zweite Beratung)
        assert ablehnung.zp_start == datetime(2022, 3, 23, tzinfo=UTC)

    def test_no_ablehnung_when_aktueller_stand_missing(self, scraper_build_vorgang):
        """No synthetic station when 'Aktueller Stand' is absent."""
        raw = _make_raw_vorgang("V-001")
        vorgang = scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        assert Stationstyp.PARL_MINUS_ABLEHNUNG not in station_types

    def test_no_ablehnung_when_aktueller_stand_is_verkuendet(self, scraper_build_vorgang):
        """No synthetic station when 'Aktueller Stand' is not 'Abgelehnt'."""
        raw = _make_raw_vorgang("V-001")
        raw["Aktueller Stand"] = "Verkündet"
        vorgang = scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        assert Stationstyp.PARL_MINUS_ABLEHNUNG not in station_types

    def test_ablehnung_not_duplicated_if_already_present(self, scraper_build_vorgang):
        """If fundstellen already contain an Ablehnung, don't add another."""
        raw = _make_raw_vorgang(
            "V-100",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    CDU  01.01.2026 Drucksache 17/10000",
                    "datum": "01.01.2026",
                    "drucksache": "17/10000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/doc.pdf",
                },
                {
                    "raw": "Ablehnung   Plenarprotokoll 17/155 25.01.2026",
                    "datum": "25.01.2026",
                    "plenarprotokoll": "17/155",
                    "station_typ": "Ablehnung",
                    "pdf_url": "",
                },
            ],
        )
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = scraper_build_vorgang(raw)

        ablehnung_count = sum(1 for s in vorgang.stationen if s.typ == Stationstyp.PARL_MINUS_ABLEHNUNG)
        assert ablehnung_count == 1

    def test_ablehnung_station_has_correct_gremium(self, scraper_build_vorgang):
        """Synthetic ablehnung station should use Landtag as gremium."""
        raw = _make_raw_vorgang("V-200")
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = scraper_build_vorgang(raw)

        ablehnung = vorgang.stationen[-1]
        assert ablehnung.typ == Stationstyp.PARL_MINUS_ABLEHNUNG
        assert ablehnung.gremium.name == "Landtag"

    def test_ablehnung_skipped_with_empty_stationen(self, scraper_build_vorgang):
        """When there are no fundstellen, don't synthesize a dateless ablehnung station."""
        raw = _make_raw_vorgang("V-300", fundstellen=[])
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 0


class TestBeschlussDesLandtagsInBeratung:
    """Regression: 'Beschluss des Landtags in Zweiter/Dritter Beratung' must map to
    parl-vollvlsgn (reading vote), not parl-akzeptanz (acceptance).

    When PARLIS places a double-space between 'Landtags' and 'in', the parser
    truncates station_typ to 'Beschluss des Landtags'. The mapper must still
    use the full raw text to detect the 'in' qualifier.
    """

    def test_beschluss_in_zweiter_beratung_with_truncated_station_typ(self, scraper_build_vorgang):
        """station_typ truncated to 'Beschluss des Landtags' but raw has 'in Zweiter Beratung'."""
        raw = _make_raw_vorgang(
            "V-400",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf  Landesregierung  22.10.2024 Drucksache 17/8000  (50 S.)",
                    "datum": "22.10.2024",
                    "drucksache": "17/8000",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 50,
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschluss des Landtags  in Zweiter Beratung  16.12.2022 Drucksache 17/3820",
                    "datum": "16.12.2022",
                    "drucksache": "17/3820",
                    "station_typ": "Beschluss des Landtags",
                    "pdf_url": "https://example.com/beschluss.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        beschluss_station = vorgang.stationen[1]
        assert beschluss_station.typ == Stationstyp.PARL_MINUS_VOLLVLSGN

    def test_beschluss_in_dritter_beratung_with_truncated_station_typ(self, scraper_build_vorgang):
        """station_typ truncated to 'Beschluss des Landtags' but raw has 'in Dritter Beratung'."""
        raw = _make_raw_vorgang(
            "V-401",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf  Landesregierung  22.10.2024 Drucksache 17/8000  (50 S.)",
                    "datum": "22.10.2024",
                    "drucksache": "17/8000",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 50,
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschluss des Landtags  in Dritter Beratung  21.12.2022 Drucksache 17/3842",
                    "datum": "21.12.2022",
                    "drucksache": "17/3842",
                    "station_typ": "Beschluss des Landtags",
                    "pdf_url": "https://example.com/beschluss3.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        beschluss_station = vorgang.stationen[1]
        assert beschluss_station.typ == Stationstyp.PARL_MINUS_VOLLVLSGN

    def test_plain_beschluss_des_landtags_still_maps_to_akzeptanz(self, scraper_build_vorgang):
        """When raw text also says 'Beschluss des Landtags' (no 'in'), it IS akzeptanz."""
        raw = _make_raw_vorgang(
            "V-402",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf  Landesregierung  22.10.2024 Drucksache 17/8000  (50 S.)",
                    "datum": "22.10.2024",
                    "drucksache": "17/8000",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 50,
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschluss des Landtags      16.12.2022 Drucksache 17/3820",
                    "datum": "16.12.2022",
                    "drucksache": "17/3820",
                    "station_typ": "Beschluss des Landtags",
                    "pdf_url": "https://example.com/beschluss.pdf",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)

        beschluss_station = vorgang.stationen[1]
        assert beschluss_station.typ == Stationstyp.PARL_MINUS_AKZEPTANZ


class TestParseFundstelleDateNone:
    """_parse_fundstelle_date returns None for missing/unparseable dates (DoD: no bogus defaults)."""

    def test_returns_none_for_empty_datum(self):
        result = _parse_fundstelle_date({"datum": "", "raw": "test", "drucksache": "17/1"})
        assert result is None

    def test_returns_none_for_missing_datum_key(self):
        result = _parse_fundstelle_date({"raw": "test", "drucksache": "17/1"})
        assert result is None

    def test_returns_none_for_unparseable_datum_no_year(self):
        result = _parse_fundstelle_date({"datum": "xyz", "raw": "test", "drucksache": "17/1"})
        assert result is None

    def test_parses_valid_date(self):
        result = _parse_fundstelle_date({"datum": "04.02.2026", "raw": "test", "drucksache": "17/1"})
        assert result == datetime(2026, 2, 4, tzinfo=UTC)

    def test_year_only_placeholder_returns_jan1(self):
        result = _parse_fundstelle_date({"datum": "00.00.2028", "raw": "test", "drucksache": "17/1"})
        assert result == datetime(2028, 1, 1, tzinfo=UTC)


class TestFallbackDateFromYearNone:
    """_fallback_date_from_year returns None when no year is extractable."""

    def test_returns_none_for_no_year(self):
        result = _fallback_date_from_year("xyz", {"raw": "test", "drucksache": "17/1"})
        assert result is None

    def test_extracts_year_when_present(self):
        result = _fallback_date_from_year("00.00.2028", {"raw": "test", "drucksache": "17/1"})
        assert result == datetime(2028, 1, 1, tzinfo=UTC)


class TestStationSkippedForUnparseableDate:
    """Stations with unfillable zp_start are omitted entirely (DoD requirement)."""

    def test_station_skipped_when_date_empty(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-800",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test",
                    "datum": "",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                    "drucksache": "17/10266",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 0

    def test_station_skipped_when_date_unparseable(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-801",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test",
                    "datum": "not-a-date",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                    "drucksache": "17/10266",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 0

    def test_all_stations_skipped_logs_error(self, scraper_build_vorgang, caplog):
        raw = _make_raw_vorgang(
            "V-803",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test",
                    "datum": "",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                    "drucksache": "17/10266",
                },
                {
                    "raw": "Beschlussempfehlung    Test",
                    "datum": "garbage",
                    "station_typ": "Beschlussempfehlung",
                    "pdf_url": "",
                    "drucksache": "17/10267",
                },
            ],
        )
        with caplog.at_level(logging.ERROR, logger="bawue.bawue_vorgaenge_scraper"):
            vorgang = scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 0
        assert any("ALL stations were skipped" in msg for msg in caplog.messages)
        assert any("V-803" in msg for msg in caplog.messages)

    def test_valid_station_kept_alongside_skipped(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-802",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Test  15.06.2025 Drucksache 17/12345",
                    "datum": "15.06.2025",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                    "drucksache": "17/12345",
                },
                {
                    "raw": "Beschlussempfehlung    Test",
                    "datum": "",
                    "station_typ": "Beschlussempfehlung",
                    "pdf_url": "",
                    "drucksache": "17/10266",
                },
            ],
        )
        vorgang = scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].zp_start == datetime(2025, 6, 15, tzinfo=UTC)
