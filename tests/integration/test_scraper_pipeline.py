"""Integration tests: full scraper pipeline with mocked PARLIS + mocked PaZuFa backend.

Each test exercises: PARLIS HTTP fetch → HTML parse → enum mapping → Vorgang building → API submission.
External systems are mocked: PARLIS via ``responses``, PaZuFa backend via ``pytest-httpserver``.
"""

from datetime import date
from unittest.mock import AsyncMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest
import responses

from bawue.parlis_client import BASE_URL, BROWSE_URL, REPORT_URL
from bawue.types import Stationstyp, Vorgangstyp

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
        assert vg["typ"] == Vorgangstyp.GG_LAND_PARL.value
        assert vg["wahlperiode"] == 17
        assert vg["verfassungsaendernd"] is False

        # Initiatoren — a comma-separated Fraktion list is parsed into separate authors
        assert len(vg["initiatoren"]) == 2
        assert vg["initiatoren"][0]["organisation"] == "Fraktion GRÜNE"
        assert vg["initiatoren"][1]["organisation"] == "Fraktion CDU"

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

        assert station_types[0] == Stationstyp.PARL_INITIATIV.value  # Gesetzentwurf
        assert station_types[1] == Stationstyp.PARL_VOLLVLSGN.value  # Erste Beratung
        assert station_types[2] == Stationstyp.PARL_AUSSCHBER.value  # Beschlussempfehlung und Bericht
        assert station_types[3] == Stationstyp.PARL_AKZEPTANZ.value  # Zustimmung

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
        assert station["typ"] == Stationstyp.PREPARL_REGBSL.value


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
        """Anfrage + Antwort fundstellen produce 2 stations with documents.

        The Antwort maps to a SONSTIG station, which the default
        filter-sonstig-stations would drop; disable it here so this test can
        verify that both fundstellen are extracted into stations.
        """
        fx = parlis_fixtures("kleine_anfrage")
        _mock_parlis_for_types({"Kleine Anfrage": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Kleine Anfrage"], filter_sonstig=False)
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
        assert ausschuss_station["typ"] == Stationstyp.PARL_AUSSCHBER.value
        assert "Ausschuss" in ausschuss_station["gremium"]["name"]


# ===================================================================
# Pipeline Behavior Tests
# ===================================================================


class TestUnlabeledPlenarprotokollFundstelle:
    """Regression for V-246637 (Gesetz zur Änderung des Abgeordnetengesetzes, WP18).

    PARLIS occasionally omits the leading station-type label on a plenary
    reading's Fundstelle, leaving a bare "Plenarprotokoll WP/Nr DD.MM.YYYY
    S. X-Y" entry with no double-space-separated prefix for the station_typ
    regex to capture. map_stationstyp() then falls through to SONSTIG and the
    default filter-sonstig-stations=true setting silently discards it — losing
    the Vorgang's first plenary reading entirely.
    """

    @responses.activate
    @pytest.mark.asyncio
    async def test_unlabeled_erste_beratung_recovered_as_vollvlsgn(self, scraper, mock_backend, parlis_fixtures):
        """The unlabeled Plenarprotokoll Fundstelle is recovered as parl-vollvlsgn
        instead of being dropped, and the Vorgang ends up with 4 stations."""
        fx = parlis_fixtures("gesetzgebung_unlabeled_erste_beratung")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 7, 8)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        assert mock_backend.call_count == 1
        vg = mock_backend.vorgaenge[0]
        station_types = [st["typ"] for st in vg["stationen"]]

        # Gesetzentwurf, unlabeled Plenarprotokoll (recovered), Beschlussempfehlung,
        # Zweite Beratung, plus the synthesized parl-ablehnung (Aktueller Stand:
        # Abgelehnt) — the unlabeled reading must not have been silently dropped.
        assert station_types == [
            Stationstyp.PARL_INITIATIV.value,
            Stationstyp.PARL_VOLLVLSGN.value,
            Stationstyp.PARL_AUSSCHBER.value,
            Stationstyp.PARL_VOLLVLSGN.value,
            Stationstyp.PARL_ABLEHNUNG.value,
        ]

        # gg-land-parl track: with the recovered Erste Beratung in place, the
        # natural chronology already satisfies the track — Ausschussbericht
        # (24.06) falls between the two readings (10.06, 01.07), and ablehnung
        # is anchored strictly after the last (Zweite Beratung) reading. No
        # retiming is needed for *this* Vorgang; that only kicks in for the
        # Haushalt-Einzelplan pattern covered by TestEnsureAusschberAfterVollvlsgn.
        by_typ = {}
        for st in vg["stationen"]:
            by_typ.setdefault(st["typ"], []).append(st["zp_start"])
        ablehnung_zp = by_typ[Stationstyp.PARL_ABLEHNUNG.value][0]
        ausschber_zp = by_typ[Stationstyp.PARL_AUSSCHBER.value][0]
        erste_beratung_zp, zweite_beratung_zp = sorted(by_typ[Stationstyp.PARL_VOLLVLSGN.value])
        assert erste_beratung_zp < ausschber_zp < zweite_beratung_zp < ablehnung_zp


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
        assert typen.count(Vorgangstyp.GG_LAND_PARL.value) == 1
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

        # Station 1: Erste Beratung — carries a Redeprotokoll placeholder document
        # (the fundstelle references a Plenarprotokoll; volltext/hash are filled by
        # later LLM enrichment, which is disabled in this test).
        erste_beratung = stations[1]
        assert len(erste_beratung["dokumente"]) == 1
        assert erste_beratung["dokumente"][0]["typ"] == "redeprotokoll"

    @responses.activate
    @pytest.mark.asyncio
    async def test_vorgang_carries_parlis_backlink(self, scraper, mock_backend, parlis_fixtures):
        """Issue #31: the Vorgang-level PARLIS backlink reaches the backend payload."""
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

        vg = mock_backend.vorgaenge[0]
        assert vg["links"] == ["https://parlis.landtag-bw.de/parlis/vorgang/V-98001"]


# ===================================================================
# Real-data regression (Issue #31)
#
# Fixtures gesetzgebung_backlink_real_* are a single, unmodified PARLIS
# record captured live from https://parlis.landtag-bw.de for Vorgang
# V-218907 ("Gesetz zur Änderung des baden-württembergischen
# Ausführungsgesetzes zum Bundesmeldegesetz", WP17, Landesregierung).
# The issue references exactly this Vorgang as an example of a missing
# backlink, so it doubles as an end-to-end reproduction with real HTML.
# ===================================================================


class TestParlisBacklinkRealData:
    @responses.activate
    @pytest.mark.asyncio
    async def test_real_vorgang_carries_parlis_backlink(self, scraper, mock_backend, parlis_fixtures):
        """Issue #31: real V-218907 record yields its PARLIS backlink end-to-end."""
        fx = parlis_fixtures("gesetzgebung_backlink_real")
        _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
        s = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2022, 12, 31)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s.run()
        finally:
            await s.session.close()

        assert mock_backend.call_count == 1
        vg = mock_backend.vorgaenge[0]
        assert vg["titel"] == (
            "Gesetz zur Änderung des baden-württembergischen Ausführungsgesetzes zum Bundesmeldegesetz"
        )
        assert any(i["id"] == "V-218907" and i["typ"] == "vorgnr" for i in vg["ids"])
        assert vg["links"] == ["https://parlis.landtag-bw.de/parlis/vorgang/V-218907"]


# ===================================================================
# Station-id stability across re-scrapes (Issue #66)
# ===================================================================


class TestStationIdStabilityAcrossRescrapes:
    @responses.activate
    @pytest.mark.asyncio
    async def test_station_ids_stable_when_documents_arrive_later(self, scraper, mock_backend, parlis_fixtures):
        """Issue #66 (WP18 V-246637): a young PARLIS record lists the
        Gesetzentwurf Fundstelle before its PDF exists; the link and further
        Fundstellen appear on a later scrape. The parl-initiativ station must
        keep its api_id across the two uploads, otherwise the backend cannot
        match it against the persisted row and keeps both — an invalid ``II``
        sequence that fails track validation on every subsequent upload."""
        young = parlis_fixtures("gesetzgebung_wp18_young")
        complete = parlis_fixtures("gesetzgebung_wp18_complete")

        _mock_parlis_for_types({"Gesetzgebung": (young["search_json"], young["results_html"])})
        s1 = await scraper(["Gesetzgebung"])
        try:
            with (
                patch("bawue.bawue_vorgaenge_scraper.date") as mock_date,
                # The Drucksache PDF is not on landtag-bw.de yet either, so the
                # reconstructed fallback URL (DD-style HEAD probe) resolves to None.
                patch.object(type(s1), "_fallback_pdf_url", new=AsyncMock(return_value=None)),
            ):
                mock_date.today.return_value = date(2026, 7, 12)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s1.run()
        finally:
            await s1.session.close()

        _mock_parlis_for_types({"Gesetzgebung": (complete["search_json"], complete["results_html"])})
        s2 = await scraper(["Gesetzgebung"])
        try:
            with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
                mock_date.today.return_value = date(2026, 7, 18)
                mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
                await s2.run()
        finally:
            await s2.session.close()

        assert mock_backend.call_count == 2
        first, second = mock_backend.vorgaenge

        # Run 1: the young record yields a single document-less initiativ station.
        assert len(first["stationen"]) == 1
        young_initiativ = first["stationen"][0]
        assert young_initiativ["typ"] == Stationstyp.PARL_INITIATIV.value
        assert not young_initiativ.get("dokumente")

        # Run 2: the full record yields all four stations, exactly one initiativ.
        assert len(second["stationen"]) == 4
        initiativ_stations = [st for st in second["stationen"] if st["typ"] == Stationstyp.PARL_INITIATIV.value]
        assert len(initiativ_stations) == 1
        assert initiativ_stations[0]["dokumente"]  # the PDF has arrived

        # The station id survives the document arriving (issue #66).
        assert initiativ_stations[0]["api_id"] == young_initiativ["api_id"]
