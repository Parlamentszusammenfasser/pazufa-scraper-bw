"""Tests for the BawueVorgaengeScraper item_extractor logic."""

import pytest
from openapi_client.models.doktyp import Doktyp
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper


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
