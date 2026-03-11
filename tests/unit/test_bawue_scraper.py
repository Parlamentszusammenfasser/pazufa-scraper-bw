"""Tests for the BawueVorgaengeScraper item_extractor logic."""

import logging
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

from bawue.bawue_vorgaenge_scraper import DEFAULT_WAHLPERIODE, BawueVorgaengeScraper, _parse_autoren


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
        assert vorgang.ids[0].typ.value == "vorgnr"

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

    def test_missing_initiative_produces_empty_initiatoren(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-050", initiative="", fundstellen=[])
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


def _make_scraper_with_mock_parlis(search_return=None, wahlperiode_start=date(2021, 4, 26)):
    """Create a minimal BawueVorgaengeScraper without full init, with a mock ParlisClient."""
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper._wahlperiode = 17
    scraper._wahlperiode_start_date = wahlperiode_start
    scraper._raw_cache = {}
    scraper._parlis = MagicMock()
    scraper._parlis.search.return_value = search_return or []
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


class TestDatetimeFallbackWarning:
    def test_missing_date_logs_warning(self, scraper_build_vorgang, caplog):
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

        with caplog.at_level(logging.WARNING, logger="bawue.bawue_vorgaenge_scraper"):
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

        with caplog.at_level(logging.WARNING, logger="bawue.bawue_vorgaenge_scraper"):
            scraper._build_vorgang(raw)

        assert any("17/10266" in msg for msg in caplog.messages)


class TestRunDurationLog:
    @pytest.mark.asyncio
    async def test_logs_completed_in_on_success(self, caplog):
        from unittest.mock import AsyncMock, MagicMock

        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = DEFAULT_WAHLPERIODE

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
