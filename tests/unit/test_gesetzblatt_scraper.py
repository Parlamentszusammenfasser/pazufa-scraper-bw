"""Tests for the BawueGesetzblattScraper."""

from unittest.mock import MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest

from bawue.bawue_gesetzblatt_scraper import (
    _GSBLT_ACCEPTED_TYPES,
    BawueGesetzblattScraper,
)
from bawue.gesetzblatt_parser import RawGesetzblattDetail
from bawue.types import Doktyp, Parlament, Stationstyp, Vorgangstyp


def _make_scraper(wahlperiode: int = 17) -> BawueGesetzblattScraper:
    """Create a minimal BawueGesetzblattScraper without full init."""
    from bawue.rate_limiter import AdaptiveRateLimiter

    scraper = object.__new__(BawueGesetzblattScraper)
    scraper._wahlperiode = wahlperiode
    scraper._start_year = 2024
    scraper._client = MagicMock()
    scraper._upload_limiter = AdaptiveRateLimiter(initial_delay=0.0, min_delay=0.0)
    scraper._published = 0
    scraper._failed = 0
    scraper._skipped = 0
    scraper.session = MagicMock()
    scraper.config = MagicMock()
    return scraper


def _make_detail(
    titel: str = "Gesetz zur Änderung des Landesdatenschutzgesetzes",
    jahr: int = 2026,
    nummer: int = 19,
    publikationsdatum: str = "27.02.2026",
    ausfertigungsdatum: str | None = "10.02.2026",
    typ: str = "Gesetz",
    federfuehrung: str | None = "Innenministerium (IM)",
    pdf_url: str | None = "https://www.baden-wuerttemberg.de/index.php?eID=dumpFile&t=r&r=1&fn=GBl._2026_Nr._19.pdf",
    pdf_filename: str | None = "GBl._2026_Nr._19.pdf",
) -> RawGesetzblattDetail:
    return RawGesetzblattDetail(
        titel=titel,
        jahr=jahr,
        nummer=nummer,
        publikationsdatum=publikationsdatum,
        ausfertigungsdatum=ausfertigungsdatum,
        typ=typ,
        federfuehrung=federfuehrung,
        pdf_url=pdf_url,
        pdf_filename=pdf_filename,
    )


class TestBuildVorgang:
    @pytest.mark.asyncio
    async def test_builds_vorgang_with_postparl_gsblt_station(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail())

        assert vorgang is not None
        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.POSTPARL_GSBLT

    @pytest.mark.asyncio
    async def test_titel_passed_through(self):
        scraper = _make_scraper()
        detail = _make_detail(titel="Mein Gesetz")
        vorgang = await scraper._build_vorgang(detail)
        assert vorgang.titel == "Mein Gesetz"

    @pytest.mark.asyncio
    async def test_kurztitel_uses_gsblt_slug(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(jahr=2026, nummer=19))
        assert vorgang.kurztitel == "gsblt-2026-19"

    @pytest.mark.asyncio
    async def test_api_id_deterministic(self):
        scraper = _make_scraper()
        v1 = await scraper._build_vorgang(_make_detail())
        v2 = await scraper._build_vorgang(_make_detail())
        assert v1.api_id == v2.api_id

    @pytest.mark.asyncio
    async def test_api_id_uses_namespace_url(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(jahr=2026, nummer=19))
        expected = str(uuid5(NAMESPACE_URL, "gsblt-2026-19"))
        assert str(vorgang.api_id) == expected

    @pytest.mark.asyncio
    async def test_vorgangstyp_is_gg_land_parl(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail())
        assert vorgang.typ == Vorgangstyp.GG_LAND_PARL

    @pytest.mark.asyncio
    async def test_wahlperiode_from_scraper(self):
        scraper = _make_scraper(wahlperiode=18)
        vorgang = await scraper._build_vorgang(_make_detail())
        assert vorgang.wahlperiode == 18

    @pytest.mark.asyncio
    async def test_verfassungsaendernd_false(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail())
        assert vorgang.verfassungsaendernd is False

    @pytest.mark.asyncio
    async def test_initiatoren_from_federfuehrung(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(federfuehrung="Innenministerium (IM)"))
        assert len(vorgang.initiatoren) == 1
        assert vorgang.initiatoren[0].organisation == "Innenministerium (IM)"

    @pytest.mark.asyncio
    async def test_initiatoren_fallback_when_federfuehrung_missing(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(federfuehrung=None))
        assert vorgang.initiatoren[0].organisation == "Landesregierung"

    @pytest.mark.asyncio
    async def test_station_zp_start_is_publikationsdatum(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(publikationsdatum="27.02.2026"))
        assert vorgang.stationen[0].zp_start.year == 2026
        assert vorgang.stationen[0].zp_start.month == 2
        assert vorgang.stationen[0].zp_start.day == 27

    @pytest.mark.asyncio
    async def test_station_gremium(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail())
        gremium = vorgang.stationen[0].gremium
        assert gremium.parlament == Parlament.BW
        assert gremium.name == "gesetzesblatt"

    @pytest.mark.asyncio
    async def test_dokument_link_is_pdf_url(self):
        scraper = _make_scraper()
        url = "https://www.baden-wuerttemberg.de/index.php?eID=dumpFile&t=r&r=1&fn=test.pdf"
        vorgang = await scraper._build_vorgang(_make_detail(pdf_url=url))
        dok = vorgang.stationen[0].dokumente[0]
        assert dok.link == url

    @pytest.mark.asyncio
    async def test_dokument_typ_is_mitteilung(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail())
        dok = vorgang.stationen[0].dokumente[0]
        assert dok.typ == Doktyp.MITTEILUNG

    @pytest.mark.asyncio
    async def test_dokument_zp_referenz_is_ausfertigungsdatum(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(
            _make_detail(publikationsdatum="27.02.2026", ausfertigungsdatum="10.02.2026")
        )
        dok = vorgang.stationen[0].dokumente[0]
        assert dok.zp_referenz.day == 10
        assert dok.zp_referenz.month == 2

    @pytest.mark.asyncio
    async def test_issue_9_station_dated_with_publication_not_gesetzesbeschluss(self):
        """Regression for issue #9 (Betreuungsgesetz, GBl 2022 Nr. 41).

        PARLIS only carries the Gesetzesbeschluss/Ausfertigung date (21.12.2022) in
        its Fundstelle, so the PARLIS-built postparl-gsblt station is dated wrong.
        The Gesetzblatt source knows the true Ausgabedatum (29.12.2022): the station's
        zp_start must be the publication date, and the document's zp_referenz the
        (earlier) Ausfertigungsdatum — never the other way round.
        """
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(
            _make_detail(
                jahr=2022,
                nummer=41,
                publikationsdatum="29.12.2022",
                ausfertigungsdatum="21.12.2022",
            )
        )
        station = vorgang.stationen[0]
        dok = station.dokumente[0]
        assert (station.zp_start.day, station.zp_start.month) == (29, 12)
        assert (dok.zp_referenz.day, dok.zp_referenz.month) == (21, 12)
        assert station.zp_start > dok.zp_referenz

    @pytest.mark.asyncio
    async def test_skips_when_pdf_url_missing(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(pdf_url=None))
        assert vorgang is None

    @pytest.mark.asyncio
    async def test_skips_verordnung_type(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(typ="Verordnung"))
        assert vorgang is None

    @pytest.mark.asyncio
    async def test_skips_bekanntmachung_type(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(typ="Bekanntmachung"))
        assert vorgang is None

    @pytest.mark.asyncio
    async def test_accepts_only_gesetz_type_by_default(self):
        assert frozenset({"Gesetz"}) == _GSBLT_ACCEPTED_TYPES


class TestSkipsVorgangWithUnparseableDate:
    @pytest.mark.asyncio
    async def test_returns_none_for_garbage_date(self):
        scraper = _make_scraper()
        vorgang = await scraper._build_vorgang(_make_detail(publikationsdatum="garbage"))
        assert vorgang is None


class TestListingPageExtractor:
    @pytest.mark.asyncio
    async def test_returns_keys_for_each_entry_in_year(self):
        scraper = _make_scraper()
        scraper._start_year = 2026
        scraper._client.find_max_number = MagicMock(return_value=3)

        with patch("bawue.bawue_gesetzblatt_scraper._current_year", return_value=2026):
            keys = await scraper.listing_page_extractor("gsblt-default")

        assert keys == ["2026-1", "2026-2", "2026-3"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_year_has_no_entries(self):
        scraper = _make_scraper()
        scraper._start_year = 2026
        scraper._client.find_max_number = MagicMock(return_value=0)

        with patch("bawue.bawue_gesetzblatt_scraper._current_year", return_value=2026):
            keys = await scraper.listing_page_extractor("gsblt-default")

        assert keys == []

    @pytest.mark.asyncio
    async def test_iterates_multiple_years(self):
        scraper = _make_scraper()
        scraper._start_year = 2025
        find_max_mock = MagicMock(side_effect=lambda year: {2025: 2, 2026: 1}.get(year, 0))
        scraper._client.find_max_number = find_max_mock

        with patch("bawue.bawue_gesetzblatt_scraper._current_year", return_value=2026):
            keys = await scraper.listing_page_extractor("gsblt-default")

        assert keys == ["2025-1", "2025-2", "2026-1"]
        assert find_max_mock.call_count == 2


class TestItemExtractor:
    @pytest.mark.asyncio
    async def test_fetches_detail_on_demand(self):
        scraper = _make_scraper()
        detail = _make_detail(typ="Gesetz")
        scraper._client.fetch_detail = MagicMock(return_value="<html/>")

        with patch("bawue.bawue_gesetzblatt_scraper.parse_detail", return_value=detail):
            vorgang = await scraper.item_extractor("2026-19")

        expected_url = (
            "https://www.baden-wuerttemberg.de/de/service/gesetze-und-verordnungen/gesetzblatt/detail/2026-19"
        )
        scraper._client.fetch_detail.assert_called_once_with(expected_url)
        assert vorgang is not None

    @pytest.mark.asyncio
    async def test_constructs_correct_url_from_key(self):
        scraper = _make_scraper()
        scraper._client.fetch_detail = MagicMock(return_value="<html/>")

        with patch("bawue.bawue_gesetzblatt_scraper.parse_detail", return_value=_make_detail(jahr=2025, nummer=7)):
            await scraper.item_extractor("2025-7")

        expected_url = "https://www.baden-wuerttemberg.de/de/service/gesetze-und-verordnungen/gesetzblatt/detail/2025-7"
        scraper._client.fetch_detail.assert_called_once_with(expected_url)
