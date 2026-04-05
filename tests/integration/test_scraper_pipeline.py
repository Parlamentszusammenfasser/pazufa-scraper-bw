"""Integration tests: full scraper pipeline with mocked PARLIS + mocked PaZuFa backend.

Each test exercises: PARLIS HTTP fetch → HTML parse → enum mapping → Vorgang building → API submission.
External systems are mocked: PARLIS via ``responses``, PaZuFa backend via ``pytest-httpserver``.
"""

from datetime import date
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
import responses
from openapi_client.models.stationstyp import Stationstyp
from openapi_client.models.vorgangstyp import Vorgangstyp

from bawue.parlis_client import BASE_URL, BROWSE_URL, REPORT_URL

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_parlis_for_types(type_map: dict[str, tuple[dict, str | None]]):
    """Register PARLIS mocks for multiple Vorgangstypen.

    type_map: {vorgangstyp_string: (search_json, results_html_or_None)}

    For each type, the scraper calls:
      1. GET BASE_URL (session)
      2. POST BROWSE_URL (search)
      3. GET REPORT_URL (results) — only if item_count > 0
    """
    for _typ, (search_json, results_html) in type_map.items():
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(responses.POST, BROWSE_URL, json=search_json, status=200)
        item_count = int(search_json.get("item_count", 0) or 0)
        if item_count > 0 and results_html:
            responses.add(responses.GET, REPORT_URL, body=results_html, status=200)


# ===================================================================
# Gesetzgebung Tests
# ===================================================================


class TestGesetzgebungFullLifecycle:
    @responses.activate
    @pytest.mark.asyncio
    async def test_gesetzgebung_full_lifecycle(self, scraper, mock_backend, parlis_fixtures):
        """Full pipeline: 4 fundstellen → 4 stations → 1 Vorgang PUT to backend."""
        fx = parlis_fixtures("gesetzgebung")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        assert mock_backend.call_count == 1
        vg = mock_backend.vorgaenge[0]

        # Correct Vorgang metadata
        expected_api_id = str(uuid5(NAMESPACE_URL, "V-98001"))
        assert vg["api_id"] == expected_api_id
        assert vg["titel"] == "Gesetz zur Förderung erneuerbarer Energien"
        assert vg["typ"] == Vorgangstyp.GG_MINUS_LAND_MINUS_PARL.value
        assert vg["wahlperiode"] == 17
        assert vg["verfassungsaendernd"] is False

        # Initiatoren
        assert len(vg["initiatoren"]) == 1
        assert vg["initiatoren"][0]["organisation"] == "Fraktion GRÜNE, Fraktion CDU"

        # 4 stations from fundstellen
        assert len(vg["stationen"]) == 4

        # IDs
        assert any(i["id"] == "V-98001" and i["typ"] == "vorgnr" for i in vg["ids"])

    @responses.activate
    @pytest.mark.asyncio
    async def test_gesetzgebung_station_types_mapped_correctly(self, scraper, mock_backend, parlis_fixtures):
        """Each fundstelle maps to the correct Stationstyp enum."""
        fx = parlis_fixtures("gesetzgebung")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        stations = mock_backend.vorgaenge[0]["stationen"]
        station_types = [s["typ"] for s in stations]

        assert station_types[0] == Stationstyp.PARL_MINUS_INITIATIV.value  # Gesetzentwurf
        assert station_types[1] == Stationstyp.PARL_MINUS_VOLLVLSGN.value  # Erste Beratung
        assert station_types[2] == Stationstyp.PARL_MINUS_AUSSCHBER.value  # Beschlussempfehlung und Bericht
        assert station_types[3] == Stationstyp.PARL_MINUS_AKZEPTANZ.value  # Zustimmung

    @responses.activate
    @pytest.mark.asyncio
    async def test_gesetzgebung_landesregierung_initiative_maps_to_preparl(
        self, scraper, mock_backend, parlis_fixtures
    ):
        """Landesregierung initiator → PREPARL_REGENT station type."""
        fx = parlis_fixtures("gesetzgebung_landesregierung")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        vg = mock_backend.vorgaenge[0]
        assert vg["initiatoren"][0]["organisation"] == "Landesregierung"

        station = vg["stationen"][0]
        assert station["typ"] == Stationstyp.PREPARL_MINUS_REGBSL.value


# ===================================================================
# Kleine Anfrage Tests
# ===================================================================


class TestKleineAnfrage:
    @responses.activate
    @pytest.mark.asyncio
    async def test_kleine_anfrage_produces_sonstig_typ(self, scraper, mock_backend, parlis_fixtures):
        """Kleine Anfrage maps to SONSTIG Vorgangstyp."""
        fx = parlis_fixtures("kleine_anfrage")
        _mock_parlis_for_types({"Kleine Anfrage": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Kleine Anfrage"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        assert mock_backend.call_count == 1
        vg = mock_backend.vorgaenge[0]
        assert vg["typ"] == Vorgangstyp.SONSTIG.value
        assert vg["titel"] == "Zustand der Brücken an der B27"

    @responses.activate
    @pytest.mark.asyncio
    async def test_kleine_anfrage_with_answer(self, scraper, mock_backend, parlis_fixtures):
        """Anfrage + Antwort fundstellen produce 2 stations with documents."""
        fx = parlis_fixtures("kleine_anfrage")
        _mock_parlis_for_types({"Kleine Anfrage": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Kleine Anfrage"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        stations = mock_backend.vorgaenge[0]["stationen"]
        assert len(stations) == 2

        # Both stations should have documents (both have pdf_url)
        for station in stations:
            assert len(station["dokumente"]) == 1
            doc = station["dokumente"][0]
            assert doc["link"] != ""


# ===================================================================
# Antrag Tests
# ===================================================================


class TestAntrag:
    @responses.activate
    @pytest.mark.asyncio
    async def test_antrag_with_committee_processing(self, scraper, mock_backend, parlis_fixtures):
        """Ausschuss fundstelle produces AUSSCHBER station with committee gremium."""
        fx = parlis_fixtures("antrag")
        _mock_parlis_for_types({"Antrag": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Antrag"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        assert mock_backend.call_count == 1
        vg = mock_backend.vorgaenge[0]
        assert vg["typ"] == Vorgangstyp.SONSTIG.value

        # Second station should be Ausschuss
        ausschuss_station = vg["stationen"][1]
        assert ausschuss_station["typ"] == Stationstyp.PARL_MINUS_AUSSCHBER.value
        assert "Ausschuss" in ausschuss_station["gremium"]["name"]


# ===================================================================
# Pipeline Behavior Tests
# ===================================================================


class TestPipelineBehavior:
    @responses.activate
    @pytest.mark.asyncio
    async def test_empty_search_results_no_api_calls(self, scraper, mock_backend, parlis_fixtures):
        """Zero PARLIS results → zero PUT /api/v2/vorgang calls."""
        fx = parlis_fixtures("empty")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], None)})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        assert mock_backend.call_count == 0

    @responses.activate
    @pytest.mark.asyncio
    async def test_multiple_vorgangstypen_in_single_run(self, scraper, mock_backend, parlis_fixtures):
        """All types processed in one run() call."""
        gg = parlis_fixtures("gesetzgebung")
        ka = parlis_fixtures("kleine_anfrage")
        an = parlis_fixtures("antrag")
        _mock_parlis_for_types(
            {
                "Gesetzgebung": (gg["search_json"], gg["results_html"]),
                "Kleine Anfrage": (ka["search_json"], ka["results_html"]),
                "Antrag": (an["search_json"], an["results_html"]),
            }
        )
        s = await scraper(["Gesetzgebung", "Kleine Anfrage", "Antrag"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        # 3 Vorgänge: one per type
        assert mock_backend.call_count == 3

        typen = sorted(vg["typ"] for vg in mock_backend.vorgaenge)
        assert typen.count(Vorgangstyp.GG_MINUS_LAND_MINUS_PARL.value) == 1
        assert typen.count(Vorgangstyp.SONSTIG.value) == 2  # Kleine Anfrage + Antrag

    @responses.activate
    @pytest.mark.asyncio
    async def test_parlis_session_and_search_called(self, scraper, mock_backend, parlis_fixtures):
        """Verify PARLIS was called correctly: session GET, search POST, report GET."""
        fx = parlis_fixtures("gesetzgebung")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        # Verify PARLIS HTTP calls
        assert len(responses.calls) == 3
        assert responses.calls[0].request.method == "GET"  # session
        assert BASE_URL in responses.calls[0].request.url
        assert responses.calls[1].request.method == "POST"  # search
        assert BROWSE_URL in responses.calls[1].request.url
        assert responses.calls[2].request.method == "GET"  # report
        assert REPORT_URL in responses.calls[2].request.url

    @responses.activate
    @pytest.mark.asyncio
    async def test_vorgang_has_deterministic_api_id(self, scraper, mock_backend, parlis_fixtures):
        """api_id is UUID5 derived from the vorgang_id, deterministic across runs."""
        fx = parlis_fixtures("antrag")
        _mock_parlis_for_types({"Antrag": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Antrag"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        vg = mock_backend.vorgaenge[0]
        expected = str(uuid5(NAMESPACE_URL, "V-98003"))
        assert vg["api_id"] == expected

    @responses.activate
    @pytest.mark.asyncio
    async def test_documents_have_drucksnr_and_link(self, scraper, mock_backend, parlis_fixtures):
        """Stations with PDF links produce documents with drucksnr and link."""
        fx = parlis_fixtures("gesetzgebung")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 1, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        stations = mock_backend.vorgaenge[0]["stationen"]

        # Station 0: Gesetzentwurf — has PDF
        gesetzentwurf = stations[0]
        assert len(gesetzentwurf["dokumente"]) == 1
        doc = gesetzentwurf["dokumente"][0]
        assert doc["drucksnr"] == "17/12001"
        assert doc["link"] == "https://www.landtag-bw.de/files/gg1-entwurf.pdf"

        # Station 1: Erste Beratung — no PDF (empty href)
        erste_beratung = stations[1]
        assert len(erste_beratung["dokumente"]) == 0
