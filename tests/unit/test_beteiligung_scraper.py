"""Tests for the BawueBeteiligungScraper."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.parlament import Parlament
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.beteiligung_parser import RawBeteiligungDetail, RawBeteiligungProcess

FIXTURES = Path(__file__).parent.parent / "fixtures" / "beteiligung"


def _make_scraper():
    """Create a minimal BawueBeteiligungScraper without full init."""
    scraper = object.__new__(BawueBeteiligungScraper)
    scraper._wahlperiode = 17
    scraper._raw_cache = {}
    scraper._client = MagicMock()
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
    def test_builds_vorgang_with_preparl_regent_station(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang is not None
        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PREPARL_MINUS_REGENT

    def test_deterministic_api_id(self):
        scraper = _make_scraper()
        detail = _make_detail()
        v1 = scraper._build_vorgang("entbuerokratisierung", detail)
        v2 = scraper._build_vorgang("entbuerokratisierung", detail)
        assert v1.api_id == v2.api_id

    def test_different_slugs_produce_different_api_ids(self):
        scraper = _make_scraper()
        v1 = scraper._build_vorgang("entbuerokratisierung", _make_detail(title="A"))
        v2 = scraper._build_vorgang("rettungsdienstplanverordnung", _make_detail(title="B"))
        assert v1.api_id != v2.api_id

    def test_api_id_uses_namespace_url(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)
        expected = str(uuid5(NAMESPACE_URL, "beteiligung-entbuerokratisierung"))
        assert str(vorgang.api_id) == expected

    def test_documents_have_preparl_entwurf_type(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)

        station = vorgang.stationen[0]
        assert len(station.dokumente) == 1
        doc = station.dokumente[0].actual_instance
        assert doc.typ == Doktyp.PREPARL_MINUS_ENTWURF

    def test_ministry_as_initiator(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)

        assert len(vorgang.initiatoren) == 1
        assert vorgang.initiatoren[0].organisation == "Ministerium des Inneren, für Digitalisierung und Kommunen"

    def test_gremium_is_landesregierung(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)

        gremium = vorgang.stationen[0].gremium
        assert gremium.parlament == Parlament.BW
        assert gremium.name == "Landesregierung"
        assert gremium.wahlperiode == 17

    def test_vorgangstyp_is_gg_land_parl(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)
        assert vorgang.typ == Vorgangstyp.GG_MINUS_LAND_MINUS_PARL

    def test_kurztitel_is_slug(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)
        assert vorgang.kurztitel == "entbuerokratisierung"

    def test_ids_contain_beteiligung_url(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = scraper._build_vorgang("entbuerokratisierung", detail)
        assert vorgang.ids is not None
        assert len(vorgang.ids) == 1
        assert "beteiligungsportal" in vorgang.ids[0].id

    def test_no_pdfs_returns_none(self):
        scraper = _make_scraper()
        detail = _make_detail(pdf_links=[])
        result = scraper._build_vorgang("klima-register", detail)
        assert result is None

    def test_multiple_pdfs(self):
        scraper = _make_scraper()
        detail = _make_detail(
            pdf_links=[
                {"title": "Entwurf A (PDF)", "url": "https://example.com/a.pdf"},
                {"title": "Entwurf B (PDF)", "url": "https://example.com/b.pdf"},
            ]
        )
        vorgang = scraper._build_vorgang("test-slug", detail)
        assert len(vorgang.stationen[0].dokumente) == 2


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
