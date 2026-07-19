"""Tests for the BawueVorgaengeScraper item_extractor logic."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bawue.bawue_dok import LLMMetrics
from bawue.bawue_vorgaenge_scraper import (
    DEFAULT_ENABLED_VORGANGSTYPEN,
    DEFAULT_WAHLPERIODE,
    BawueVorgaengeScraper,
    _assign_stable_station_ids,
    _construct_drucksache_pdf_url,
    _fallback_date_from_year,
    _initiativ_drucksnr_from_fundstellen,
    _parse_autoren,
    _parse_fundstelle_date,
    _reading_round,
    _same_round_label,
    _vorgang_kurztitel,
)
from bawue.parlis_parser import parse_fundstelle_text
from bawue.types import UNSET, Doktyp, Stationstyp, Vorgangstyp


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
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._filter_sonstig = True
    scraper.session = MagicMock()
    scraper._client = MagicMock()
    return scraper._build_vorgang


class TestBuildVorgang:
    @pytest.mark.asyncio
    async def test_builds_framework_vorgang(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-001", titel="Testgesetz")
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.titel == "Testgesetz"
        assert str(vorgang.api_id)  # UUID generated
        assert len(vorgang.stationen) == 2
        assert vorgang.ids is not None
        assert vorgang.ids[0].id == "V-001"
        assert vorgang.ids[0].typ == "vorgnr"
        # Issue #25: kurztitel is a semantic summary, not the vgnr. Without LLM
        # enrichment no kurztitel is available, so it is None (not "V-001").
        assert vorgang.kurztitel is None

    @pytest.mark.asyncio
    async def test_build_vorgang_forwards_parlis_backlink(self, scraper_build_vorgang):
        """Issue #31: the parsed PARLIS detail_url must reach Vorgang.links."""
        raw = _make_raw_vorgang("V-218907")
        raw["detail_url"] = "https://parlis.landtag-bw.de/parlis/vorgang/V-218907"
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.links == ["https://parlis.landtag-bw.de/parlis/vorgang/V-218907"]

    @pytest.mark.asyncio
    async def test_build_vorgang_links_unset_without_backlink(self, scraper_build_vorgang):
        """Issue #31: without a detail_url, links stays UNSET (not an empty list)."""
        raw = _make_raw_vorgang("V-001")
        raw.pop("detail_url", None)
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.links is UNSET

    @pytest.mark.asyncio
    async def test_kurztitel_from_initiative_document(self, scraper_build_vorgang):
        """Issue #25: the Vorgang Kurztitel reuses the initiating document's LLM kurztitel."""
        raw = _make_raw_vorgang("V-001")
        vorgang = await scraper_build_vorgang(raw)

        # Simulate the enriched initiative document carrying a plain-language title.
        vorgang.stationen[0].dokumente[0].kurztitel = "Klimaschutzgesetz"

        assert _vorgang_kurztitel(vorgang.stationen) == "Klimaschutzgesetz"

    @pytest.mark.asyncio
    async def test_build_vorgang_kurztitel_is_semantic_not_vgnr(self, monkeypatch):
        """Issue #25 regression: the *built* Vorgang exposes the initiating
        document's plain-language kurztitel, never the vgnr.

        This pins the exact seam that regressed the issue. With the original
        buggy assignment (``kurztitel=vorgang_id``) this Vorgang's kurztitel
        would have been ``"V-246999"``; it must now be the semantic title.
        """
        from bawue.bawue_dok import EnrichmentResult

        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._filter_sonstig = True
        scraper.session = MagicMock()
        scraper._client = MagicMock()
        scraper.config = MagicMock()
        scraper._llm_enabled = True
        scraper._llm = MagicMock()
        scraper._llm_model = "gpt-5-nano"
        scraper._llm_metrics = LLMMetrics()

        async def _fake_enrich(session, llm, dok, **kwargs):
            # The initiating Gesetzentwurf comes back with an LLM kurztitel.
            dok.kurztitel = "Ausgleich für Coronasoforthilfen"
            return EnrichmentResult(dokument=dok)

        monkeypatch.setattr("bawue.bawue_dok.enrich_dokument", _fake_enrich)

        vorgang = await scraper._build_vorgang(_make_raw_vorgang("V-246999"))

        assert vorgang.kurztitel == "Ausgleich für Coronasoforthilfen"
        assert vorgang.kurztitel != "V-246999"

    @pytest.mark.asyncio
    async def test_build_vorgang_marks_vorgang_with_failed_pdf_download(self, monkeypatch):
        """Issue #66 follow-up: when enrichment reports a failed PDF download
        (document not yet published), the Vorgang's vorgnr is remembered so
        the run does not cache it and the next cycle retries the download."""
        from bawue.bawue_dok import EnrichmentResult

        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 18
        scraper._filter_sonstig = True
        scraper.session = MagicMock()
        scraper._client = MagicMock()
        scraper.config = MagicMock()
        scraper._llm_enabled = True
        scraper._llm = MagicMock()
        scraper._llm_model = "gpt-5-nano"
        scraper._llm_metrics = LLMMetrics()
        scraper._pending_pdf_downloads = set()

        async def _fake_enrich(session, llm, dok, **kwargs):
            return EnrichmentResult(dokument=dok, download_failed=True)

        monkeypatch.setattr("bawue.bawue_dok.enrich_dokument", _fake_enrich)

        # item_extractor sets the vorgangs-id context before building (log_context).
        from bawue.log_context import reset_vorgangs_id, set_vorgangs_id

        token = set_vorgangs_id("V-246637")
        try:
            await scraper._build_vorgang(_make_raw_vorgang("V-246637"))
        finally:
            reset_vorgangs_id(token)

        assert "V-246637" in scraper._pending_pdf_downloads

    @pytest.mark.asyncio
    async def test_store_extracted_result_skips_caching_on_pending_pdf_download(self):
        """Issue #66 follow-up: a Vorgang with a not-yet-published PDF must not
        be cached — an abgelehnter Vorgang's PARLIS record never changes again,
        so a cached entry would keep the volltext missing forever."""
        from bawue.types import VgIdent

        scraper = object.__new__(BawueVorgaengeScraper)
        scraper.config = MagicMock()
        scraper._pending_pdf_downloads = {"V-246637"}

        pending = MagicMock(ids=[VgIdent(id="V-246637", typ="vorgnr")])
        await scraper.store_extracted_result("raw-key-1", pending)
        scraper.config.cache.store_raw.assert_not_called()
        assert "V-246637" not in scraper._pending_pdf_downloads  # consumed

        clean = MagicMock(ids=[VgIdent(id="V-247045", typ="vorgnr")])
        clean.to_dict.return_value = {"api_id": "x"}
        await scraper.store_extracted_result("raw-key-2", clean)
        scraper.config.cache.store_raw.assert_called_once()

    @pytest.mark.asyncio
    async def test_documentless_station_gets_stable_api_id(self, scraper_build_vorgang):
        """DD-028: a document-less station (e.g. Gesetzentwurf without PDF) gets a
        deterministic api_id so the backend links it across re-runs instead of
        duplicating it into an invalid 'II' track."""
        raw = _make_raw_vorgang(
            "V-246637",
            initiative="Fraktion der AfD",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion der AfD  03.06.2026 Drucksache 18/75   (4 S.)",
                    "datum": "03.06.2026",
                    "drucksache": "18/75",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",  # empty PDF URL -> no document on the station
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_INITIATIV
        assert station.dokumente == []
        assert station.api_id is not None

    @pytest.mark.asyncio
    async def test_stable_station_api_id_is_deterministic(self, scraper_build_vorgang):
        """The same Vorgang yields the same document-less station api_id on re-runs."""
        raw = _make_raw_vorgang(
            "V-246637",
            initiative="Fraktion der AfD",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion der AfD  03.06.2026 Drucksache 18/75   (4 S.)",
                    "datum": "03.06.2026",
                    "drucksache": "18/75",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                },
            ],
        )
        v1 = await scraper_build_vorgang(raw)
        v2 = await scraper_build_vorgang(raw)
        assert v1.stationen[0].api_id == v2.stationen[0].api_id

    @pytest.mark.asyncio
    async def test_station_api_id_stable_when_document_arrives_later(self, scraper_build_vorgang):
        """Issue #66: WP18 V-246637. A young PARLIS record carries the
        Gesetzentwurf fundstelle without a PDF link; the link appears on a later
        scrape. The station api_id must not change when the document arrives,
        otherwise the backend cannot match the re-uploaded station against the
        persisted row and keeps both — two parl-initiativ stations at the same
        zp_start fail track validation (HTTP 400) on every subsequent upload."""
        fundstelle = {
            "raw": "Gesetzentwurf    Fraktion der AfD  03.06.2026 Drucksache 18/75   (4 S.)",
            "datum": "03.06.2026",
            "drucksache": "18/75",
            "station_typ": "Gesetzentwurf",
            "pdf_url": "",
        }
        young = await scraper_build_vorgang(
            _make_raw_vorgang("V-246637", initiative="Fraktion der AfD", fundstellen=[dict(fundstelle)])
        )
        fundstelle["pdf_url"] = (
            "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP18/Drs/18%5F0075%5FD.pdf"
        )
        complete = await scraper_build_vorgang(
            _make_raw_vorgang("V-246637", initiative="Fraktion der AfD", fundstellen=[fundstelle])
        )

        assert young.stationen[0].dokumente == []
        assert complete.stationen[0].dokumente  # document has arrived
        assert young.stationen[0].api_id == complete.stationen[0].api_id

    @pytest.mark.asyncio
    async def test_budget_siblings_sharing_pdf_get_distinct_station_ids(self, scraper_build_vorgang):
        """Issue #47 regression: two Haushalt-Einzelplan Vorgänge that cite the
        *same* shared Staatshaushaltsgesetz PDF must produce distinct
        document-bearing station api_ids. Before the fix these stations had no
        api_id and the backend matched them by the shared document hash, merging
        unrelated Vorgänge and violating rel_station_dokument_pkey."""
        shared_fundstellen = [
            {
                "raw": "Gesetzentwurf    Landesregierung  01.11.2025 Drucksache 17/1000   (5 S.)",
                "datum": "01.11.2025",
                "drucksache": "17/1000",
                "station_typ": "Gesetzentwurf",
                "pdf_url": "https://www.landtag-bw.de/resource/blob/1/17_1000.pdf",
            },
        ]
        v1 = await scraper_build_vorgang(
            _make_raw_vorgang("V-100", initiative="Landesregierung", fundstellen=shared_fundstellen)
        )
        v2 = await scraper_build_vorgang(
            _make_raw_vorgang("V-101", initiative="Landesregierung", fundstellen=shared_fundstellen)
        )

        # Every station carries a stable api_id (no reliance on document hash).
        for vorgang in (v1, v2):
            for station in vorgang.stationen:
                assert station.api_id is not UNSET

        ids_v1 = {s.api_id for s in v1.stationen}
        ids_v2 = {s.api_id for s in v2.stationen}
        assert ids_v1.isdisjoint(ids_v2)

    @pytest.mark.asyncio
    async def test_synthetic_initiativ_deep_copies_regbsl_documents(self, scraper_build_vorgang):
        """Issue #47: the synthetic parl-initiativ inserted after preparl-regbsl
        must own deep-copied Dokument objects, not alias the regbsl's, so that
        per-station mutation (e.g. the stable api_id) does not leak between them."""
        raw = _make_raw_vorgang(
            "V-100",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  01.11.2025 Drucksache 17/1000   (5 S.)",
                    "datum": "01.11.2025",
                    "drucksache": "17/1000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/1/17_1000.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/150 15.12.2025",
                    "datum": "15.12.2025",
                    "plenarprotokoll": "17/150",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        by_typ = {s.typ: s for s in vorgang.stationen}
        regbsl = by_typ[Stationstyp.PREPARL_REGBSL]
        initiativ = by_typ[Stationstyp.PARL_INITIATIV]

        assert regbsl.dokumente and initiativ.dokumente
        # Distinct objects (deep copy) ...
        assert initiativ.dokumente[0] is not regbsl.dokumente[0]
        # ... with identical content ...
        assert initiativ.dokumente[0].link == regbsl.dokumente[0].link
        # ... but distinct, non-aliased station identities.
        assert initiativ.api_id != regbsl.api_id

    @pytest.mark.asyncio
    async def test_synthetic_initiativ_documents_serialize_to_json(self, scraper_build_vorgang):
        """Deep-copying the regbsl documents for the synthetic parl-initiativ
        (issue #47) must not break API serialization. ``copy.deepcopy`` mints a
        fresh ``Unset`` instance per optional field, and corelib's ``to_dict``
        guards those fields by identity (``is not UNSET``); a deep-copied sentinel
        slips past the guard and lands in the payload, so ``json.dumps`` raises
        'Object of type Unset is not JSON serializable' (72% of WP17 uploads
        failed on this). Serializing the whole Vorgang must succeed and the
        synthetic station's document autoren must carry the real UNSET singleton."""
        raw = _make_raw_vorgang(
            "V-100",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  01.11.2025 Drucksache 17/1000   (5 S.)",
                    "datum": "01.11.2025",
                    "drucksache": "17/1000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/1/17_1000.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/150 15.12.2025",
                    "datum": "15.12.2025",
                    "plenarprotokoll": "17/150",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        initiativ = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_INITIATIV)
        autor = initiativ.dokumente[0].autoren[0]
        # The deep-copied autor must hold the genuine singleton, not a clone, so
        # the identity-based to_dict guard omits it.
        assert autor.person is UNSET

        # Whole-Vorgang serialization (the exact path the API client takes).
        json.dumps(vorgang.to_dict())

    @pytest.mark.asyncio
    async def test_document_bearing_station_gets_stable_api_id(self, scraper_build_vorgang):
        """Issue #47: document-bearing stations also get a Vorgang-scoped api_id,
        so the backend no longer identifies them by a document hash that collides
        across Vorgänge (default fundstellen: Gesetzentwurf has a PDF)."""
        raw = _make_raw_vorgang("V-001")
        vorgang = await scraper_build_vorgang(raw)

        initiativ = vorgang.stationen[0]
        assert initiativ.typ == Stationstyp.PARL_INITIATIV
        assert initiativ.dokumente  # has a document
        assert initiativ.api_id is not UNSET

    def test_assign_stable_station_ids_skips_only_existing(self):
        """Issue #47: only a station that already carries an api_id is left
        untouched; both document-less and document-bearing stations get one."""
        from datetime import UTC, datetime

        doc_station = MagicMock(
            api_id=UNSET,
            dokumente=[MagicMock(link="https://example.com/a.pdf")],
            typ=Stationstyp.PARL_INITIATIV,
            zp_start=datetime(2026, 6, 3, tzinfo=UTC),
        )
        preset_station = MagicMock(api_id="preset", dokumente=[])
        bare_station = MagicMock(
            api_id=UNSET,
            dokumente=[],
            typ=Stationstyp.PARL_INITIATIV,
            zp_start=datetime(2026, 6, 3, tzinfo=UTC),
        )
        _assign_stable_station_ids([doc_station, preset_station, bare_station], "V-1")

        assert doc_station.api_id is not UNSET
        assert preset_station.api_id == "preset"
        assert bare_station.api_id is not UNSET

    def test_same_typ_same_date_stations_with_different_docs_get_distinct_ids(self):
        """Issue #47: two same-typ stations sharing a zp_start but carrying
        different documents must not collapse onto one api_id."""
        from datetime import UTC, datetime

        zp = datetime(2025, 12, 15, tzinfo=UTC)
        first = MagicMock(
            api_id=UNSET,
            dokumente=[MagicMock(link="https://example.com/erste.pdf")],
            typ=Stationstyp.PARL_VOLLVLSGN,
            zp_start=zp,
        )
        second = MagicMock(
            api_id=UNSET,
            dokumente=[MagicMock(link="https://example.com/zweite.pdf")],
            typ=Stationstyp.PARL_VOLLVLSGN,
            zp_start=zp,
        )
        _assign_stable_station_ids([first, second], "V-1")

        assert first.api_id != second.api_id

    def test_station_id_matches_persisted_docless_row_after_document_arrives(self):
        """Issue #66: the id of a station whose document arrives on a later
        scrape must keep matching the row persisted under the document-less
        DD-028 key (staging holds station c65767f1-… for exactly this key)."""
        from datetime import UTC, datetime
        from uuid import NAMESPACE_URL, uuid5

        station = MagicMock(
            api_id=UNSET,
            dokumente=[
                MagicMock(
                    link="https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP18/Drs/18%5F0075%5FD.pdf"
                )
            ],
            typ=Stationstyp.PARL_INITIATIV,
            zp_start=datetime(2026, 6, 3, tzinfo=UTC),
        )
        _assign_stable_station_ids([station], "V-246637")

        docless_key = "bawue-station-V-246637-parl-initiativ-2026-06-03T00:00:00+00:00"
        assert station.api_id == str(uuid5(NAMESPACE_URL, docless_key))

    def test_same_typ_same_date_tie_ids_stable_when_docs_change(self):
        """Issue #66 follow-up to the #47 distinctness rule: two same-typ
        stations sharing a zp_start stay distinct via their list position, so
        their ids survive later document changes on either station."""
        from datetime import UTC, datetime

        zp = datetime(2025, 12, 15, tzinfo=UTC)

        def _pair(first_link: str | None, second_link: str | None):
            first = MagicMock(
                api_id=UNSET,
                dokumente=[MagicMock(link=first_link)] if first_link else [],
                typ=Stationstyp.PARL_VOLLVLSGN,
                zp_start=zp,
            )
            second = MagicMock(
                api_id=UNSET,
                dokumente=[MagicMock(link=second_link)] if second_link else [],
                typ=Stationstyp.PARL_VOLLVLSGN,
                zp_start=zp,
            )
            _assign_stable_station_ids([first, second], "V-1")
            return first.api_id, second.api_id

        run1 = _pair(None, "https://example.com/zweite.pdf")
        run2 = _pair("https://example.com/erste.pdf", "https://example.com/zweite_v2.pdf")

        assert run1[0] != run1[1]  # tie stays distinct (issue #47)
        assert run1 == run2  # ids survive documents arriving/changing

    @pytest.mark.asyncio
    async def test_ids_omit_initiativdrucksache_by_default(self, scraper_build_vorgang):
        """DD-041: the Initiativdrucksache (Issue #26) is NOT emitted by default.

        Backend workaround: `vorgang_merge_candidates` treats any shared vg_ident
        as proof two Vorgänge are the same process, and the Initiativdrucksache is
        many-to-one (every Haushalt-Einzelplan cites the same Staatshaushaltsgesetz),
        so emitting it merges unrelated Vorgänge → HTTP 500 rel_station_dokument_pkey.
        With `emit-initdrucks-ident` off (default), only the 1:1 `vorgnr` is emitted.
        """
        raw = _make_raw_vorgang("V-001")  # default fundstellen: Gesetzentwurf Drucksache 17/10266
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.ids is not None
        idents = {(i.typ, i.id) for i in vorgang.ids}
        assert ("vorgnr", "V-001") in idents
        assert all(i.typ != "initdrucks" for i in vorgang.ids)

    @pytest.mark.asyncio
    async def test_ids_include_initiativdrucksache_when_enabled(self, scraper_build_vorgang):
        """DD-041: `emit-initdrucks-ident = true` restores the Issue #26 cross-reference."""
        scraper_build_vorgang.__self__._emit_initdrucks_ident = True
        raw = _make_raw_vorgang("V-001")  # default fundstellen: Gesetzentwurf Drucksache 17/10266
        vorgang = await scraper_build_vorgang(raw)

        idents = {(i.typ, i.id) for i in vorgang.ids}
        assert ("vorgnr", "V-001") in idents
        assert ("initdrucks", "17/10266") in idents

    @pytest.mark.asyncio
    async def test_ids_omit_initiativdrucksache_when_absent_though_enabled(self, scraper_build_vorgang):
        """Even with the toggle on, no initdrucks id when the initiative has no Drucksache."""
        scraper_build_vorgang.__self__._emit_initdrucks_ident = True
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
        vorgang = await scraper_build_vorgang(raw)

        assert all(i.typ != "initdrucks" for i in vorgang.ids)

    @pytest.mark.asyncio
    async def test_deterministic_api_id(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-001")
        v1 = await scraper_build_vorgang(raw)
        v2 = await scraper_build_vorgang(raw)
        assert v1.api_id == v2.api_id

    @pytest.mark.asyncio
    async def test_different_ids_produce_different_api_ids(self, scraper_build_vorgang):
        v1 = await scraper_build_vorgang(_make_raw_vorgang("V-001"))
        v2 = await scraper_build_vorgang(_make_raw_vorgang("V-002"))
        assert v1.api_id != v2.api_id

    @pytest.mark.asyncio
    async def test_gesetzentwurf_from_landesregierung(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.typ == Vorgangstyp.GG_LAND_PARL
        assert vorgang.initiatoren[0].organisation == "Landesregierung"

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PREPARL_REGBSL
        assert station.dokumente[0].typ == Doktyp.PREPARL_ENTWURF
        assert station.dokumente[0].drucksnr == "17/11000"

    @pytest.mark.asyncio
    async def test_plenarprotokoll_fundstelle_creates_plenum_gremium(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_VOLLVLSGN
        assert station.gremium.name == "plenum"
        assert station.dokumente == []

    @pytest.mark.asyncio
    async def test_plenarprotokoll_lesung_gets_redeprotokoll_doktyp(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_VOLLVLSGN
        assert station.dokumente[0].typ == Doktyp.REDEPROTOKOLL

    @pytest.mark.asyncio
    async def test_ausschuss_fundstelle_creates_committee_gremium(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_AUSSCHBER
        assert station.gremium.name == "Ausschuss für Wirtschaft"
        assert station.dokumente[0].typ == Doktyp.BESCHLUSSEMPF

    @pytest.mark.asyncio
    async def test_genitive_ausschuss_gremium_issue68(self, scraper_build_vorgang):
        """Issue #68: real Fundstelle (Drucksache 17/2586) fell back to `plenum`.

        Parses the raw text rather than a pre-built dict so the whole
        parse → gremium chain is pinned, not just the station builder.
        """
        fund = parse_fundstelle_text(
            "Beschlussempfehlung und Bericht    Ausschuss des Inneren, für Digitalisierung und Kommunen  "
            "18.05.2022 Drucksache 17/2586"
        )
        fund["pdf_url"] = "https://example.com/report.pdf"
        vorgang = await scraper_build_vorgang(_make_raw_vorgang("V-068", fundstellen=[fund]))

        station = vorgang.stationen[0]
        assert station.gremium.name == "Ausschuss des Inneren, für Digitalisierung und Kommunen"
        assert station.typ == Stationstyp.PARL_AUSSCHBER

    @pytest.mark.asyncio
    async def test_antraege_plural_document_typ_issue69(self, scraper_build_vorgang):
        """Issue #69: "Änderungsanträge" document got typ `sonstig` (Drs 17/4495).

        Uses a real PARLIS Fundstelle and parses it, so the station_typ that
        reaches map_dokumententyp is the one production actually produces.
        Per DD-001 an Änderungsantrag attaches to the preceding plenary station,
        so the Fundstelle needs that station to exist or it is discarded.
        """
        beratung = parse_fundstelle_text("Zweite Beratung   Plenarprotokoll 17/20 13.12.2021")
        beratung["pdf_url"] = "https://example.com/protokoll.pdf"
        antraege = parse_fundstelle_text("Änderungsanträge    Fraktion der AfD  13.12.2021 Drucksache 17/1203")
        antraege["pdf_url"] = "https://example.com/aenderungsantraege.pdf"
        vorgang = await scraper_build_vorgang(_make_raw_vorgang("V-069", fundstellen=[beratung, antraege]))

        typen = [d.typ for s in vorgang.stationen for d in s.dokumente]
        assert Doktyp.ANTRAG in typen, f"Änderungsanträge did not map to antrag: {typen}"
        assert Doktyp.SONSTIG not in typen

    @pytest.mark.asyncio
    async def test_empty_fundstellen_produces_no_stations(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-040", fundstellen=[])
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.stationen == []

    @pytest.mark.asyncio
    async def test_missing_initiative_falls_back_to_fundstelle_autor(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.initiatoren) == 1
        assert vorgang.initiatoren[0].organisation == "Landesregierung"

    @pytest.mark.asyncio
    async def test_missing_initiative_no_fundstellen_produces_empty_initiatoren(self, scraper_build_vorgang):
        raw = _make_raw_vorgang("V-051", initiative="", fundstellen=[])
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.initiatoren == []

    @pytest.mark.asyncio
    async def test_missing_initiative_fundstelle_without_autor_produces_empty(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.initiatoren == []

    @pytest.mark.asyncio
    async def test_empty_vorgang_titel_falls_back_to_todo_marker(self, scraper_build_vorgang):
        """Backend rejects empty strings on required fields — empty titel becomes TODO."""
        raw = _make_raw_vorgang("V-053", titel="")
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.titel == "TODO"

    @pytest.mark.asyncio
    async def test_dokument_volltext_and_hash_are_todo_when_llm_disabled(self, scraper_build_vorgang):
        """Without LLM enrichment, volltext + hash carry the TODO marker (never empty)."""
        raw = _make_raw_vorgang(
            "V-054",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/doc.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        dok = vorgang.stationen[0].dokumente[0]
        assert dok.volltext == "TODO"
        assert dok.hash_ == "TODO"

    @pytest.mark.asyncio
    async def test_empty_drucksnr_becomes_none(self, scraper_build_vorgang):
        """Optional drucksnr: blank value is dropped (None) rather than sent as empty string."""
        raw = _make_raw_vorgang(
            "V-055",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Erste Beratung",
                    "drucksache": "",
                    "pdf_url": "https://example.com/pp.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        dok = vorgang.stationen[0].dokumente[0]
        assert dok.drucksnr is None

    @pytest.mark.asyncio
    async def test_gremium_uses_parlament_bw(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.stationen[0].gremium.parlament.value == "BW"

    @pytest.mark.asyncio
    async def test_default_gremium_is_plenum(self, scraper_build_vorgang):
        """Fundstelle without Ausschuss and without Plenarprotokoll → `plenum`
        (DD-021: "plenum als default wenn etwas 'irgendwie passiert'")."""
        raw = _make_raw_vorgang(
            "V-061",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  01.01.2026 Drucksache 17/1000",
                    "datum": "01.01.2026",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert vorgang.stationen[0].gremium.name == "plenum"

    @pytest.mark.asyncio
    async def test_gsblt_station_uses_gesetzesblatt_gremium(self, scraper_build_vorgang):
        """postparl-gsblt Fundstellen must use the reserved `gesetzesblatt` name
        (DD-021; matches BY reference scraper)."""
        raw = _make_raw_vorgang(
            "V-062",
            fundstellen=[
                # Non-postparl station keeps the Vorgang from being skipped
                # by the "only post-parliamentary stations" rule.
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  01.01.2026 Drucksache 17/1000",
                    "datum": "01.01.2026",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                },
                {
                    "raw": "Gesetz  vom 10. Februar 2026 Gesetzblatt für Baden-Württemberg 2026 Nr. 22",
                    "datum": "10.02.2026",
                    "station_typ": "Gesetz",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        gsblt_stations = [s for s in vorgang.stationen if s.typ == Stationstyp.POSTPARL_GSBLT]
        assert len(gsblt_stations) == 1
        assert gsblt_stations[0].gremium.name == "gesetzesblatt"

    @pytest.mark.asyncio
    async def test_fallback_to_raw_text_when_station_typ_missing(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_AKZEPTANZ
        assert station.dokumente[0].typ == Doktyp.MITTEILUNG

    @pytest.mark.asyncio
    async def test_fallback_gesetz_maps_to_postparl(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.POSTPARL_GSBLT

    @pytest.mark.asyncio
    async def test_normal_station_typ_still_works(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_AKZEPTANZ
        assert station.dokumente[0].typ == Doktyp.MITTEILUNG


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
    scraper._failed_items = []
    scraper._parlis_errors = []
    scraper._upload_limiter = AdaptiveRateLimiter(
        initial_delay=0.2, min_delay=0.05, backoff_multiplier=10.0, recovery_factor=0.5
    )
    mock_config = MagicMock()
    mock_config.dry_run = False
    scraper.config = mock_config
    scraper.scraper_id = "test-scraper-id"
    scraper._llm_enabled = False
    scraper._llm = None
    scraper._llm_metrics = LLMMetrics()
    scraper._filter_sonstig = True
    scraper.session = MagicMock()
    scraper._client = MagicMock()
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
    async def test_skips_vorgang_with_no_stations(self):
        """Vorgänge where all Fundstellen have unparseable dates produce no stations and are skipped."""
        scraper = _make_scraper_with_mock_parlis()
        scraper._raw_cache["V-910"] = _make_raw_vorgang(
            "V-910",
            titel="Berichtigung des Gesetzes zur Regelung einer Landesgrundsteuer",
            fundstellen=[
                {
                    "raw": "Berichtigung des Gesetzes  Gesetzblatt 2022 Nr. 37  S. 595",
                    "datum": "",
                    "station_typ": "Berichtigung",
                    "pdf_url": "",
                },
            ],
        )

        result = await scraper.item_extractor("V-910")

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
    @pytest.mark.asyncio
    async def test_zero_day_month_falls_back_to_year_start(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        station = vorgang.stationen[0]
        assert station.zp_start.year == 2028
        assert station.zp_start.month == 1
        assert station.zp_start.day == 1
        assert station.zp_start.tzinfo is not None, "zp_start must be timezone-aware to avoid API 422 errors"

    @pytest.mark.asyncio
    async def test_zero_day_month_logs_warning(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = True
        scraper.session = MagicMock()

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
            await scraper._build_vorgang(raw)

        assert any("00.00.2028" in msg for msg in caplog.messages)


class TestDatetimesAreTimezoneAware:
    """All zp_start/zp_modifiziert/zp_referenz datetimes must be timezone-aware.

    The API rejects naive datetimes (serialized without +00:00 suffix) with a 422
    'premature end of input' error on the zp_start field.
    """

    @pytest.mark.asyncio
    async def test_normal_date_is_timezone_aware(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        station = vorgang.stationen[0]

        assert station.zp_start.tzinfo is not None
        assert station.zp_start == datetime(2025, 6, 15, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_missing_date_skips_station(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 0

    @pytest.mark.asyncio
    async def test_placeholder_date_fallback_is_timezone_aware(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        station = vorgang.stationen[0]

        assert station.zp_start.tzinfo is not None
        assert station.zp_start == datetime(2028, 1, 1, tzinfo=UTC)


class TestDatetimeFallbackWarning:
    @pytest.mark.asyncio
    async def test_missing_date_logs_error(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = True
        scraper.session = MagicMock()

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
            await scraper._build_vorgang(raw)

        assert any("No date found for Fundstelle" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_missing_date_logs_drucksache_number(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = True
        scraper.session = MagicMock()

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
            await scraper._build_vorgang(raw)

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

        # put_vorgang returns without raising → success.
        with patch("bawue.upload_throttle.put_vorgang"):
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
        from bawue.api import BawueApiError

        scraper = _make_scraper_with_mock_parlis()

        with patch(
            "bawue.upload_throttle.put_vorgang",
            side_effect=BawueApiError(500, b"Internal Server Error", "vorgang_put"),
        ):
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

    @pytest.mark.asyncio
    async def test_summary_duration_is_human_readable(self, capsys):
        scraper = _make_scraper_with_mock_parlis()

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        # no raw seconds-with-dot format ("23045.7s") in the summary
        duration_line = next(line for line in captured.out.splitlines() if "Duration" in line)
        assert "Duration: 0m 00s" in duration_line

    @pytest.mark.asyncio
    async def test_summary_lists_failed_vorgaenge_with_id_title_and_reason(self, capsys):
        from bawue.api import BawueApiError

        scraper = _make_scraper_with_mock_parlis()

        item = MagicMock()
        item.api_id = "aabbccdd"
        item.kurztitel = "V-500"
        item.titel = "Klimaschutzgesetz 2026"

        with patch(
            "bawue.upload_throttle.put_vorgang",
            side_effect=BawueApiError(422, b"Unprocessable Entity", "vorgang_put"),
        ):
            await scraper.send_result(item)

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode"),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Failed Vorgänge" in captured.out
        failed_block = captured.out.split("Failed Vorgänge", 1)[1]
        assert "V-500" in failed_block
        assert "Klimaschutzgesetz 2026" in failed_block
        assert "422" in failed_block


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
        scraper._failed_items = []
        scraper._parlis_errors = []
        scraper._llm_enabled = False
        scraper._llm_metrics = LLMMetrics()

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
        scraper._failed_items = []
        scraper._parlis_errors = []
        scraper._llm_enabled = False
        scraper._llm_metrics = LLMMetrics()

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

    def test_ministry_name_with_internal_comma(self):
        """Ministry names containing commas must not be split incorrectly."""
        result = _parse_autoren(
            "Ministerium für Umwelt, Klima und Energiewirtschaft, Ministerium für Landesentwicklung und Wohnen"
        )
        assert len(result) == 2
        assert result[0].organisation == "Ministerium für Umwelt, Klima und Energiewirtschaft"
        assert result[1].organisation == "Ministerium für Landesentwicklung und Wohnen"

    def test_single_ministry_with_internal_comma(self):
        result = _parse_autoren("Ministerium für Umwelt, Klima und Energiewirtschaft")
        assert len(result) == 1
        assert result[0].organisation == "Ministerium für Umwelt, Klima und Energiewirtschaft"

    def test_mixed_fraktionen_and_ministry(self):
        """Fraktionen and ministries in the same string."""
        result = _parse_autoren("Fraktion GRÜNE, Ministerium der Justiz und für Migration")
        assert len(result) == 2
        assert result[0].organisation == "Fraktion GRÜNE"
        assert result[1].organisation == "Ministerium der Justiz und für Migration"


class TestBuildStationAutoren:
    @pytest.mark.asyncio
    async def test_fundstelle_autor_text_used(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0]
        assert len(doc.autoren) == 1
        assert doc.autoren[0].organisation == "Fraktion GRÜNE"

    @pytest.mark.asyncio
    async def test_fallback_to_initiative(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0]
        assert len(doc.autoren) == 1
        assert doc.autoren[0].organisation == "SPD"

    @pytest.mark.asyncio
    async def test_no_autor_text_no_initiative(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0]
        assert doc.autoren == []

    @pytest.mark.asyncio
    async def test_ausschuss_is_document_autor_issue71(self, scraper_build_vorgang):
        """Issue #71: Beschlussempfehlung authored by the committee, not the initiator.

        Parses the real Fundstelle (Drucksache 17/2586) so the committee comes
        from the parser exactly as production produces it.
        """
        fund = parse_fundstelle_text(
            "Beschlussempfehlung und Bericht    Ausschuss des Inneren, für Digitalisierung und Kommunen  "
            "18.05.2022 Drucksache 17/2586"
        )
        fund["pdf_url"] = "https://example.com/report.pdf"
        raw = _make_raw_vorgang("V-710", initiative="Landesregierung", fundstellen=[fund])

        doc = (await scraper_build_vorgang(raw)).stationen[0].dokumente[0]
        # The comma inside the genitive name must not split it into two authors
        assert [a.organisation for a in doc.autoren] == ["Ausschuss des Inneren, für Digitalisierung und Kommunen"]

    @pytest.mark.asyncio
    async def test_initiative_fallback_kept_without_ausschuss_issue71(self, scraper_build_vorgang):
        """Issue #71: the initiator fallback stays where nothing better is derivable.

        A Gesetzesbeschluss names no acting body in its Fundstelle, so it keeps
        the Vorgang initiator — a deliberate scope decision, see DD-042.
        """
        fund = parse_fundstelle_text("Gesetzesbeschluss des Landtags     10.11.2021 Drucksache 17/1050")
        fund["pdf_url"] = "https://example.com/beschluss.pdf"
        raw = _make_raw_vorgang("V-711", initiative="Landesregierung", fundstellen=[fund])

        doc = (await scraper_build_vorgang(raw)).stationen[0].dokumente[0]
        assert [a.organisation for a in doc.autoren] == ["Landesregierung"]

    @pytest.mark.asyncio
    async def test_multiple_autoren_from_fundstelle(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        doc = vorgang.stationen[0].dokumente[0]
        assert len(doc.autoren) == 2
        assert doc.autoren[0].organisation == "Fraktion GRÜNE"
        assert doc.autoren[1].organisation == "Fraktion der CDU"


class TestStationMerging:
    """Tests for merging consecutive same-type stations."""

    @pytest.mark.asyncio
    async def test_consecutive_same_type_same_gremium_merged(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_AKZEPTANZ
        assert len(vorgang.stationen[0].dokumente) == 2

    @pytest.mark.asyncio
    async def test_consecutive_same_type_different_gremium_not_merged(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2

    @pytest.mark.asyncio
    async def test_ausschuss_merge_backwards_no_plenum_between(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER]
        assert len(ausschuss_stationen) == 1
        assert len(ausschuss_stationen[0].dokumente) == 2

    @pytest.mark.asyncio
    async def test_ausschuss_no_merge_across_plenum(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER]
        assert len(ausschuss_stationen) == 2

    @pytest.mark.asyncio
    async def test_ausschuss_no_merge_across_large_time_gap(self, scraper_build_vorgang):
        """Issue #54: two same-committee Beschlussempfehlungen months apart are
        distinct deliberations and must stay separate stations — merging them
        would drop the later date (Staatshaushaltsgesetz 2022, V-214597:
        Beschlussempfehlungen dated 2022-06-30 and 2023-02-09)."""
        raw = _make_raw_vorgang(
            "V-214597",
            titel="Staatshaushaltsgesetz 2022",
            fundstellen=[
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Finanzen  30.06.2022 Drucksache 17/2600",
                    "datum": "30.06.2022",
                    "drucksache": "17/2600",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/be1.pdf",
                },
                {
                    "raw": "Beschlussempfehlung   Ausschuss für Finanzen  09.02.2023 Drucksache 17/4200",
                    "datum": "09.02.2023",
                    "drucksache": "17/4200",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/be2.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER]
        assert len(ausschuss_stationen) == 2
        # No date loss: both Beschlussempfehlung dates survive as distinct records.
        dates = {s.zp_start.date() for s in ausschuss_stationen}
        assert date(2022, 6, 30) in dates
        assert date(2023, 2, 9) in dates

    @pytest.mark.asyncio
    async def test_stellungnahme_still_attaches_after_merge(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_AKZEPTANZ
        assert len(vorgang.stationen[0].dokumente) == 2
        assert vorgang.stationen[0].stellungnahmen is not None
        assert len(vorgang.stationen[0].stellungnahmen) == 1

    @pytest.mark.asyncio
    async def test_consecutive_vollvlsgn_not_merged_even_with_documents(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2
        assert vorgang.stationen[0].typ == Stationstyp.PARL_VOLLVLSGN
        assert vorgang.stationen[1].typ == Stationstyp.PARL_VOLLVLSGN

    @pytest.mark.asyncio
    async def test_consecutive_vollvlsgn_ueberweisung_not_merged(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2
        assert vorgang.stationen[0].typ == Stationstyp.PARL_VOLLVLSGN
        assert vorgang.stationen[1].typ == Stationstyp.PARL_VOLLVLSGN

    @pytest.mark.asyncio
    async def test_vollvlsgn_same_typ_text_different_days_merged(self, scraper_build_vorgang):
        """Two 'Erste Beratung' fundstellen on different days → 1 station spanning both (DD-024).

        Supersedes earlier DD-004 "never merge" rule for same-round fundstellen.
        Different reading labels (Erste vs Zweite vs Überweisung) stay separate — see
        other tests in this class. Same label = same reading round = merge.
        """
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
                    "pdf_url": "https://example.com/pp142.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_VOLLVLSGN
        assert station.zp_start == datetime(2026, 2, 5, tzinfo=UTC)
        assert station.zp_modifiziert == datetime(2026, 2, 6, tzinfo=UTC)
        assert len(station.dokumente) == 2

    @pytest.mark.asyncio
    async def test_vollvlsgn_same_typ_text_same_day_merged(self, scraper_build_vorgang):
        """Two 'Zweite Beratung' fundstellen on the same day (PARLIS duplicate) → 1 station."""
        raw = _make_raw_vorgang(
            "V-812",
            fundstellen=[
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/160 18.12.2024",
                    "datum": "18.12.2024",
                    "plenarprotokoll": "17/160",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp160a.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/160 18.12.2024",
                    "datum": "18.12.2024",
                    "plenarprotokoll": "17/160",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp160b.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PARL_VOLLVLSGN
        assert station.zp_start == datetime(2024, 12, 18, tzinfo=UTC)
        # Same-day merge: zp_modifiziert stays unset (no temporal extension)
        assert station.zp_modifiziert is UNSET or station.zp_modifiziert == station.zp_start
        assert len(station.dokumente) == 2

    @pytest.mark.asyncio
    async def test_vollvlsgn_three_same_typ_merged(self, scraper_build_vorgang):
        """Three 'Zweite Beratung' fundstellen on consecutive days (budget Einzelplan pattern) → 1 station."""
        raw = _make_raw_vorgang(
            "V-813",
            fundstellen=[
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/150 15.12.2021",
                    "datum": "15.12.2021",
                    "plenarprotokoll": "17/150",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp150.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/151 16.12.2021",
                    "datum": "16.12.2021",
                    "plenarprotokoll": "17/151",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp151.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/152 17.12.2021",
                    "datum": "17.12.2021",
                    "plenarprotokoll": "17/152",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp152.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        station = vorgang.stationen[0]
        assert station.zp_start == datetime(2021, 12, 15, tzinfo=UTC)
        assert station.zp_modifiziert == datetime(2021, 12, 17, tzinfo=UTC)
        assert len(station.dokumente) == 3

    @pytest.mark.asyncio
    async def test_vollvlsgn_empty_station_typ_not_merged(self, scraper_build_vorgang):
        """Two VOLLVLSGN fundstellen with empty station_typ → not merged (defensive guard).

        An empty label is not a reliable same-round signal; fall back to the safe default
        of keeping stations separate (DD-024).
        """
        raw = _make_raw_vorgang(
            "V-814",
            fundstellen=[
                {
                    "raw": "Beratung   Plenarprotokoll 17/141 05.02.2026",
                    "datum": "05.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "",
                    "pdf_url": "https://example.com/pp141.pdf",
                },
                {
                    "raw": "Beratung   Plenarprotokoll 17/142 06.02.2026",
                    "datum": "06.02.2026",
                    "plenarprotokoll": "17/142",
                    "station_typ": "",
                    "pdf_url": "https://example.com/pp142.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2
        assert all(s.typ == Stationstyp.PARL_VOLLVLSGN for s in vorgang.stationen)

    @pytest.mark.asyncio
    async def test_staatshaushaltsgesetz_consolidation_end_to_end(self, scraper_build_vorgang):
        """Mirror the staging-run Staatshaushaltsgesetz failure pattern: rounds consolidate to ≤3 V stations.

        Input: 2 Erste, 1 Ausschuss, 3 Zweite (Einzelplan days), 1 Dritte, Gesetzesbeschluss, Gesetzblatt.
        Expected: Erste (merged) → A → Zweite (merged) → Dritte → J → G — three V stations total,
        satisfying the gg-land-parl track regex which allows at most V A* V A* V.
        """
        raw = _make_raw_vorgang(
            "V-815",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/140 27.10.2021",
                    "datum": "27.10.2021",
                    "plenarprotokoll": "17/140",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "https://example.com/sthg_erste_1.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/142 10.11.2021",
                    "datum": "10.11.2021",
                    "plenarprotokoll": "17/142",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "https://example.com/sthg_erste_2.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Finanzen  03.12.2021 Drucksache 17/1500",
                    "datum": "03.12.2021",
                    "drucksache": "17/1500",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/sthg_ausschber.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/150 15.12.2021",
                    "datum": "15.12.2021",
                    "plenarprotokoll": "17/150",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/sthg_zweite_1.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/151 16.12.2021",
                    "datum": "16.12.2021",
                    "plenarprotokoll": "17/151",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/sthg_zweite_2.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/152 17.12.2021",
                    "datum": "17.12.2021",
                    "plenarprotokoll": "17/152",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/sthg_zweite_3.pdf",
                },
                {
                    "raw": "Dritte Beratung   Plenarprotokoll 17/153 22.12.2021",
                    "datum": "22.12.2021",
                    "plenarprotokoll": "17/153",
                    "station_typ": "Dritte Beratung",
                    "pdf_url": "https://example.com/sthg_dritte.pdf",
                },
                {
                    "raw": "Gesetzesbeschluss des Landtags      22.12.2021 Drucksache 17/1600",
                    "datum": "22.12.2021",
                    "drucksache": "17/1600",
                    "station_typ": "Gesetzesbeschluss",
                    "pdf_url": "https://example.com/sthg_beschluss.pdf",
                },
                {
                    "raw": "Gesetzblatt   Nr. 1  01.01.2022",
                    "datum": "01.01.2022",
                    "station_typ": "Gesetzblatt",
                    "pdf_url": "https://example.com/sthg_gbl.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        vollvlsgn_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN]
        assert len(vollvlsgn_stationen) == 3, (
            f"Expected exactly 3 plenary-reading stations (Erste/Zweite/Dritte merged per round), "
            f"got {len(vollvlsgn_stationen)}"
        )
        # Erste Beratung: merged across 27.10 and 10.11
        erste = vollvlsgn_stationen[0]
        assert erste.zp_start == datetime(2021, 10, 27, tzinfo=UTC)
        assert erste.zp_modifiziert == datetime(2021, 11, 10, tzinfo=UTC)
        assert len(erste.dokumente) == 2
        # Zweite Beratung: merged across 15.-17.12
        zweite = vollvlsgn_stationen[1]
        assert zweite.zp_start == datetime(2021, 12, 15, tzinfo=UTC)
        assert zweite.zp_modifiziert == datetime(2021, 12, 17, tzinfo=UTC)
        assert len(zweite.dokumente) == 3
        # Dritte Beratung: single day
        dritte = vollvlsgn_stationen[2]
        assert dritte.zp_start == datetime(2021, 12, 22, tzinfo=UTC)
        assert len(dritte.dokumente) == 1


class TestStellungnahmenAsChildren:
    @pytest.mark.asyncio
    async def test_stellungnahme_attaches_to_preceding_station(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].stellungnahmen is not None
        assert len(vorgang.stationen[0].stellungnahmen) == 1
        assert vorgang.stationen[0].stellungnahmen[0].typ == Doktyp.STELLUNGNAHME

    @pytest.mark.asyncio
    async def test_stellungnahme_without_preceding_station_discarded_with_warning(self, scraper_build_vorgang, caplog):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = True
        scraper.session = MagicMock()

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
            vorgang = await scraper._build_vorgang(raw)

        assert len(vorgang.stationen) == 0
        assert any("Stellungnahme" in msg and "V-701" in msg for msg in caplog.messages)


class TestKleineAnfrageHierarchy:
    """Tests for Kleine Anfrage + Stellungnahme pairing."""

    @pytest.mark.asyncio
    async def test_kleine_anfrage_station_type_is_parl_initiativ(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_INITIATIV
        assert vorgang.stationen[0].dokumente[0].typ == Doktyp.ANFRAGE

    @pytest.mark.asyncio
    async def test_kleine_anfrage_with_stellungnahme(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_INITIATIV
        assert vorgang.stationen[0].stellungnahmen is not None
        assert len(vorgang.stationen[0].stellungnahmen) == 1
        assert vorgang.stationen[0].stellungnahmen[0].typ == Doktyp.STELLUNGNAHME

    @pytest.mark.asyncio
    async def test_stellungnahme_without_pdf_still_attaches(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        # Should be 1 station (Kleine Anfrage), not 2 (with an empty Stellungnahme station)
        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_INITIATIV


class TestDedupDrucks:
    """Tests for per-station Drucksache deduplication."""

    @pytest.mark.asyncio
    async def test_duplicate_drucksache_removed(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        # Both fundstellen merge into 1 Ausschuss station; dedup removes the duplicate doc
        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER]
        assert len(ausschuss_stationen) == 1
        assert len(ausschuss_stationen[0].dokumente) == 1

    @pytest.mark.asyncio
    async def test_different_drucksache_kept(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        ausschuss_stationen = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER]
        assert len(ausschuss_stationen) == 1
        assert len(ausschuss_stationen[0].dokumente) == 2

    @pytest.mark.asyncio
    async def test_documents_without_drucksnr_always_kept(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

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
        scraper._parlis_errors = []
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

    @pytest.mark.asyncio
    async def test_aenderungsantrag_attaches_to_next_vollversammlung(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        # Should have 3 stations: preparl-regent + synthetic parl-initiativ + Zweite Beratung
        station_types = [s.typ for s in vorgang.stationen]
        assert station_types == [
            Stationstyp.PREPARL_REGBSL,
            Stationstyp.PARL_INITIATIV,
            Stationstyp.PARL_VOLLVLSGN,
        ]

        # The Zweite Beratung station should contain the Änderungsantrag document
        beratung = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN)
        drucksnrs = [d.drucksnr for d in beratung.dokumente]
        assert "17/1210" in drucksnrs, "Änderungsantrag document should be attached to Beratung station"

    @pytest.mark.asyncio
    async def test_aenderungsantrag_singular_form(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_VOLLVLSGN

    @pytest.mark.asyncio
    async def test_aenderungsantrag_attaches_to_preceding_vollversammlung_if_no_next(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_VOLLVLSGN
        drucksnrs = [d.drucksnr for d in vorgang.stationen[0].dokumente]
        assert "17/1210" in drucksnrs

    @pytest.mark.asyncio
    async def test_aenderungsantrag_without_vollversammlung_logs_warning(self, scraper_build_vorgang, caplog):
        """If no vollvlsgn station exists at all, discard with a warning."""
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = True
        scraper.session = MagicMock()

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
            vorgang = await scraper._build_vorgang(raw)

        assert len(vorgang.stationen) == 0
        assert any("Änderungsantr" in msg for msg in caplog.messages)


class TestEntschliessungsantragHandling:
    """Entschließungsanträge should be discarded entirely."""

    @pytest.mark.asyncio
    async def test_entschliessungsantrag_discarded(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_INITIATIV
        # Entschließungsantrag document should NOT appear anywhere
        all_drucksnrs = [d.drucksnr for s in vorgang.stationen for d in s.dokumente]
        assert "17/1215" not in all_drucksnrs


class TestAntragReclassification:
    """Issue DD-019: 'Antrag' after Ausschussbericht is an Änderungsantrag, not a new initiative."""

    @pytest.mark.asyncio
    async def test_antrag_after_ausschussbericht_treated_as_aenderungsantrag(self, scraper_build_vorgang):
        """An 'Antrag' appearing after a parl-ausschber should be reclassified as
        Änderungsantrag — its documents attached to the next parl-vollvlsgn station,
        no second parl-initiativ created."""
        raw = _make_raw_vorgang(
            "V-214623",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  26.10.2021 Drucksache 17/1077   (50 S.)",
                    "datum": "26.10.2021",
                    "drucksache": "17/1077",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 50,
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/20 11.11.2021",
                    "datum": "11.11.2021",
                    "plenarprotokoll": "17/20",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht    Ausschuss für Soziales  22.11.2021 Drucksache 17/1258",
                    "datum": "22.11.2021",
                    "drucksache": "17/1258",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Soziales",
                    "pdf_url": "https://example.com/bericht.pdf",
                },
                {
                    "raw": "Antrag    Fraktion GRÜNE  21.12.2021 Drucksache 17/1512",
                    "datum": "21.12.2021",
                    "drucksache": "17/1512",
                    "station_typ": "Antrag",
                    "pdf_url": "https://example.com/antrag.pdf",
                    "autor_text": "Fraktion GRÜNE",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/23 22.12.2021",
                    "datum": "22.12.2021",
                    "plenarprotokoll": "17/23",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        # Should NOT contain a second parl-initiativ
        assert station_types.count(Stationstyp.PARL_INITIATIV) == 1
        assert station_types == [
            Stationstyp.PREPARL_REGBSL,
            Stationstyp.PARL_INITIATIV,
            Stationstyp.PARL_VOLLVLSGN,
            Stationstyp.PARL_AUSSCHBER,
            Stationstyp.PARL_VOLLVLSGN,
        ]

        # The "Antrag" document (17/1512) should be attached to the Zweite Beratung
        zweite_beratung = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN][-1]
        drucksnrs = [d.drucksnr for d in zweite_beratung.dokumente]
        assert "17/1512" in drucksnrs, "Reclassified Antrag document should be on Zweite Beratung"

    @pytest.mark.asyncio
    async def test_antrag_before_ausschussbericht_remains_initiativ(self, scraper_build_vorgang):
        """An 'Antrag' at the start of a Vorgang (before any committee report) should
        remain mapped as parl-initiativ — the positional heuristic must not fire."""
        raw = _make_raw_vorgang(
            "V-900",
            fundstellen=[
                {
                    "raw": "Antrag    Fraktion der SPD  01.03.2023 Drucksache 17/4500   (5 S.)",
                    "datum": "01.03.2023",
                    "drucksache": "17/4500",
                    "station_typ": "Antrag",
                    "seiten": 5,
                    "pdf_url": "https://example.com/antrag.pdf",
                    "autor_text": "Fraktion der SPD",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/50 05.03.2023",
                    "datum": "05.03.2023",
                    "plenarprotokoll": "17/50",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        assert Stationstyp.PARL_INITIATIV in station_types, (
            "Antrag before any Ausschussbericht should remain parl-initiativ"
        )


class TestAktuellerStandAblehnung:
    """Tests for synthesizing a parl-ablehnung station from 'Aktueller Stand: Abgelehnt'."""

    @pytest.mark.asyncio
    async def test_abgelehnt_appends_ablehnung_station(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 3
        ablehnung = vorgang.stationen[-1]
        assert ablehnung.typ == Stationstyp.PARL_ABLEHNUNG
        # Same calendar day as the last station (Zweite Beratung), bumped 1h to
        # avoid sharing zp_start with a different Stationstyp (backend enforces
        # total ordering across types).
        assert ablehnung.zp_start == datetime(2022, 3, 23, 1, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_no_ablehnung_when_aktueller_stand_missing(self, scraper_build_vorgang):
        """No synthetic station when 'Aktueller Stand' is absent."""
        raw = _make_raw_vorgang("V-001")
        vorgang = await scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        assert Stationstyp.PARL_ABLEHNUNG not in station_types

    @pytest.mark.asyncio
    async def test_no_ablehnung_when_aktueller_stand_is_verkuendet(self, scraper_build_vorgang):
        """No synthetic station when 'Aktueller Stand' is not 'Abgelehnt'."""
        raw = _make_raw_vorgang("V-001")
        raw["Aktueller Stand"] = "Verkündet"
        vorgang = await scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        assert Stationstyp.PARL_ABLEHNUNG not in station_types

    @pytest.mark.asyncio
    async def test_ablehnung_not_duplicated_if_already_present(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        ablehnung_count = sum(1 for s in vorgang.stationen if s.typ == Stationstyp.PARL_ABLEHNUNG)
        assert ablehnung_count == 1

    @pytest.mark.asyncio
    async def test_duplicate_ablehnung_fundstellen_merged(self, scraper_build_vorgang):
        """Two Ablehnung Fundstellen without documents must be merged into one station.

        Regression: PARLIS occasionally delivers multiple Fundstellen that map to
        parl-ablehnung (e.g. 'Ablehnung' appearing twice). Without merging, both
        get appended as separate stations, causing the backend to reject the Vorgang
        for duplicate stations.  See staging run 2026-04-13 (12 failures).
        """
        raw = _make_raw_vorgang(
            "V-150",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    CDU  01.01.2026 Drucksache 17/10000",
                    "datum": "01.01.2026",
                    "drucksache": "17/10000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/doc.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/155 15.01.2026",
                    "datum": "15.01.2026",
                    "plenarprotokoll": "17/155",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Ablehnung   Plenarprotokoll 17/160 25.01.2026",
                    "datum": "25.01.2026",
                    "plenarprotokoll": "17/160",
                    "station_typ": "Ablehnung",
                    "pdf_url": "",
                },
                {
                    "raw": "Ablehnung   Plenarprotokoll 17/160 25.01.2026",
                    "datum": "25.01.2026",
                    "plenarprotokoll": "17/160",
                    "station_typ": "Ablehnung",
                    "pdf_url": "",
                },
            ],
        )
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = await scraper_build_vorgang(raw)

        ablehnung_count = sum(1 for s in vorgang.stationen if s.typ == Stationstyp.PARL_ABLEHNUNG)
        assert ablehnung_count == 1

    @pytest.mark.asyncio
    async def test_ablehnung_station_has_correct_gremium(self, scraper_build_vorgang):
        """Synthetic ablehnung station should use Landtag as gremium."""
        raw = _make_raw_vorgang("V-200")
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = await scraper_build_vorgang(raw)

        ablehnung = vorgang.stationen[-1]
        assert ablehnung.typ == Stationstyp.PARL_ABLEHNUNG
        assert ablehnung.gremium.name == "plenum"

    @pytest.mark.asyncio
    async def test_ablehnung_skipped_with_empty_stationen(self, scraper_build_vorgang):
        """When there are no fundstellen, don't synthesize a dateless ablehnung station."""
        raw = _make_raw_vorgang("V-300", fundstellen=[])
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 0

    @pytest.mark.asyncio
    async def test_synthesized_ablehnung_has_deterministic_api_id(self, scraper_build_vorgang):
        """Synthesized parl-ablehnung must carry a deterministic api_id so the backend
        merges re-runs into the same row instead of inserting a duplicate.

        Without this, the backend's station_merge_candidates query only matches by
        (api_id) or (vg, typ, gremium, shared-document-hash); a synthetic station has
        neither api_id nor documents, so every re-upload inserts a new ghost row.
        That broke 30 rejected-bill Vorgänge in the 2026-04-20 dev run.
        """
        raw = _make_raw_vorgang("V-222457")
        raw["Aktueller Stand"] = "Abgelehnt"

        v1 = await scraper_build_vorgang(raw)
        v2 = await scraper_build_vorgang(raw)

        a1 = v1.stationen[-1]
        a2 = v2.stationen[-1]
        assert a1.typ == Stationstyp.PARL_ABLEHNUNG
        assert a1.api_id is not None, "synthesized ablehnung must have a stable api_id"
        assert a1.api_id == a2.api_id, "api_id must be deterministic across runs"

    @pytest.mark.asyncio
    async def test_synthesized_ablehnung_api_id_differs_per_vorgang(self, scraper_build_vorgang):
        """Two different Vorgänge must get different synthesized-ablehnung api_ids."""
        raw_a = _make_raw_vorgang("V-111")
        raw_a["Aktueller Stand"] = "Abgelehnt"
        raw_b = _make_raw_vorgang("V-222")
        raw_b["Aktueller Stand"] = "Abgelehnt"

        va = await scraper_build_vorgang(raw_a)
        vb = await scraper_build_vorgang(raw_b)

        assert va.stationen[-1].api_id != vb.stationen[-1].api_id


class TestEnforceTotalOrdering:
    """Tests for the zp_start normalization that keeps different-typed stations
    from sharing a timestamp.

    The backend's track validation sorts stations by zp_start; two stations of
    different Stationstyp that share zp_start make the order ambiguous and the
    upload is rejected. PARLIS dates are date-only, so same-day collisions are
    common — we bump colliding stations forward by 1h.
    """

    @pytest.mark.asyncio
    async def test_lesung_and_ausschuss_same_day_get_separated(self, scraper_build_vorgang):
        """Erste Beratung followed by Ausschussbericht on the same day: ausschber bumps +1h."""
        raw = _make_raw_vorgang(
            "V-900",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/200 12.05.2026",
                    "datum": "12.05.2026",
                    "plenarprotokoll": "17/200",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Finanzen  12.05.2026 Drucksache 17/2000",
                    "datum": "12.05.2026",
                    "drucksache": "17/2000",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert [s.typ for s in vorgang.stationen] == [
            Stationstyp.PARL_VOLLVLSGN,
            Stationstyp.PARL_AUSSCHBER,
        ]
        assert vorgang.stationen[0].zp_start == datetime(2026, 5, 12, tzinfo=UTC)
        assert vorgang.stationen[1].zp_start == datetime(2026, 5, 12, 1, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_same_type_stations_keep_identical_zp_start(self, scraper_build_vorgang):
        """Two parl-vollvlsgn stations on the same day (Erste Beratung + Überweisung):
        both still PARL_VOLLVLSGN, so the rule allows them to share zp_start."""
        raw = _make_raw_vorgang(
            "V-901",
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
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 2
        assert vorgang.stationen[0].typ == vorgang.stationen[1].typ == Stationstyp.PARL_VOLLVLSGN
        assert vorgang.stationen[0].zp_start == vorgang.stationen[1].zp_start

    @pytest.mark.asyncio
    async def test_synthetic_initiativ_does_not_collide_with_next_station(self, scraper_build_vorgang):
        """preparl-regbsl + parl-initiativ (synthesized) + parl-vollvlsgn on the same Beratungstag:
        each different type gets its own zp_start slot."""
        raw = _make_raw_vorgang(
            "V-902",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  10.06.2026 Drucksache 17/12345",
                    "datum": "10.06.2026",
                    "drucksache": "17/12345",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/200 10.06.2026",
                    "datum": "10.06.2026",
                    "plenarprotokoll": "17/200",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        # Stations: regbsl(10.06 00:00), initiativ(10.06 00:00 → bumped to ?), vollvlsgn(10.06 00:00 → bumped)
        assert [s.typ for s in vorgang.stationen] == [
            Stationstyp.PREPARL_REGBSL,
            Stationstyp.PARL_INITIATIV,
            Stationstyp.PARL_VOLLVLSGN,
        ]
        zp_starts = [s.zp_start for s in vorgang.stationen]
        # All three types differ → no two may share a timestamp
        assert len(set(zp_starts)) == 3
        # Order preserved (collection order is the desired track order)
        assert zp_starts == sorted(zp_starts)
        # Same calendar day
        assert all(zp.date() == datetime(2026, 6, 10).date() for zp in zp_starts)

    @pytest.mark.asyncio
    async def test_three_distinct_types_same_day_cascade(self, scraper_build_vorgang):
        """Three different-typed stations on the same day cascade to +1h, +2h."""
        raw = _make_raw_vorgang(
            "V-903",
            fundstellen=[
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/300 20.07.2026",
                    "datum": "20.07.2026",
                    "plenarprotokoll": "17/300",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Finanzen  20.07.2026 Drucksache 17/3000",
                    "datum": "20.07.2026",
                    "drucksache": "17/3000",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
                {
                    "raw": "Schlussabstimmung   Plenarprotokoll 17/301 20.07.2026",
                    "datum": "20.07.2026",
                    "plenarprotokoll": "17/301",
                    "station_typ": "Schlussabstimmung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        zp_starts = [s.zp_start for s in vorgang.stationen]
        assert len(set(zp_starts)) == len(zp_starts), "all different-typed stations must have distinct zp_start"
        assert zp_starts == sorted(zp_starts), "collection order preserved"


class TestEnsureAusschberAfterVollvlsgn:
    """Tests for retiming parl-ausschber stations that the BW gg-land-parl track
    would reject because they precede the first parl-vollvlsgn.

    PARLIS dates the Bericht/Beschlussempfehlung Drucksache by its publication
    date. For Haushalt Einzelpläne the committee Drucksache is published in
    mid-November while the first plenary reading happens in December — leaving
    the ausschber chronologically before any vollvlsgn. The track validator
    rejects that ordering. The fix retimes the offending ausschber to one hour
    past the first vollvlsgn.
    """

    @pytest.mark.asyncio
    async def test_haushalt_einzelplan_ausschber_retimed_after_first_vollvlsgn(self, scraper_build_vorgang):
        """Haushalt Einzelplan pattern: regbsl, ausschber, vollvlsgn (x3).

        The PARLIS-dated ausschber falls before the first reading. After the fix,
        its zp_start lands one hour past the first vollvlsgn so the track resolves
        to: regbsl → initiativ → vollvlsgn → ausschber → vollvlsgn → vollvlsgn.
        """
        raw = _make_raw_vorgang(
            "V-215967",
            titel="Staatshaushaltsplan 2022 - Einzelplan 01: Landtag",
            vorgangstyp="Haushaltsgesetzgebung",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  26.10.2021 Drucksache 17/1000",
                    "datum": "26.10.2021",
                    "drucksache": "17/1000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Finanzen  18.11.2021 Drucksache 17/1100",
                    "datum": "18.11.2021",
                    "drucksache": "17/1100",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/30 16.12.2021",
                    "datum": "16.12.2021",
                    "plenarprotokoll": "17/30",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/31 17.12.2021",
                    "datum": "17.12.2021",
                    "plenarprotokoll": "17/31",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Schlussabstimmung   Plenarprotokoll 17/32 22.12.2021",
                    "datum": "22.12.2021",
                    "plenarprotokoll": "17/32",
                    "station_typ": "Schlussabstimmung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        # Locate the ausschber and the first vollvlsgn (in zp_start order).
        sorted_stations = sorted(vorgang.stationen, key=lambda s: s.zp_start)
        ausschber = next(s for s in sorted_stations if s.typ == Stationstyp.PARL_AUSSCHBER)
        first_vollvlsgn = next(s for s in sorted_stations if s.typ == Stationstyp.PARL_VOLLVLSGN)

        # Ausschber must come strictly after the first vollvlsgn.
        assert ausschber.zp_start > first_vollvlsgn.zp_start
        # Specifically: pinned to one hour past the first vollvlsgn.
        assert ausschber.zp_start == first_vollvlsgn.zp_start + timedelta(hours=1)
        # And there is at least one vollvlsgn before it in the chronological order.
        assert any(s.typ == Stationstyp.PARL_VOLLVLSGN and s.zp_start < ausschber.zp_start for s in sorted_stations)

    @pytest.mark.asyncio
    async def test_canonical_order_ausschber_after_vollvlsgn_unchanged(self, scraper_build_vorgang):
        """When ausschber is already after a vollvlsgn, its date is preserved."""
        raw = _make_raw_vorgang(
            "V-CAN",
            initiative="Fraktion GRÜNE",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  01.03.2026 Drucksache 17/2000",
                    "datum": "01.03.2026",
                    "drucksache": "17/2000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/200 10.03.2026",
                    "datum": "10.03.2026",
                    "plenarprotokoll": "17/200",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Recht  20.03.2026 Drucksache 17/2100",
                    "datum": "20.03.2026",
                    "drucksache": "17/2100",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Recht",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/201 25.03.2026",
                    "datum": "25.03.2026",
                    "plenarprotokoll": "17/201",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        ausschber = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER)
        # Original PARLIS date preserved (no retiming).
        assert ausschber.zp_start == datetime(2026, 3, 20, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_no_vollvlsgn_means_ausschber_left_alone(self, scraper_build_vorgang):
        """Without any vollvlsgn anchor, ausschber timestamp stays untouched."""
        raw = _make_raw_vorgang(
            "V-NO-LESUNG",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  01.04.2026 Drucksache 17/3000",
                    "datum": "01.04.2026",
                    "drucksache": "17/3000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Recht  10.04.2026 Drucksache 17/3100",
                    "datum": "10.04.2026",
                    "drucksache": "17/3100",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Recht",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        ausschber = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER)
        # Synthetic initiativ shares the date and gets bumped, but the ausschber's
        # original date holds because there is no vollvlsgn anchor.
        assert ausschber.zp_start.date() == datetime(2026, 4, 10).date()

    @pytest.mark.asyncio
    async def test_multiple_ausschber_before_vollvlsgn_all_retimed(self, scraper_build_vorgang):
        """Two committees both report before the first reading: both get retimed."""
        raw = _make_raw_vorgang(
            "V-MULTI",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  01.05.2026 Drucksache 17/4000",
                    "datum": "01.05.2026",
                    "drucksache": "17/4000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Finanzen  10.05.2026 Drucksache 17/4100",
                    "datum": "10.05.2026",
                    "drucksache": "17/4100",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/finanz.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Recht  12.05.2026 Drucksache 17/4101",
                    "datum": "12.05.2026",
                    "drucksache": "17/4101",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Recht",
                    "pdf_url": "https://example.com/recht.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/400 20.05.2026",
                    "datum": "20.05.2026",
                    "plenarprotokoll": "17/400",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        first_vollvlsgn = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN)
        ausschber_stations = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER]

        assert len(ausschber_stations) == 2
        for ausschber in ausschber_stations:
            assert ausschber.zp_start == first_vollvlsgn.zp_start + timedelta(hours=1), (
                "every ausschber preceding the first vollvlsgn should be pinned to the same anchor+1h"
            )

    @pytest.mark.asyncio
    async def test_retimed_ausschber_does_not_collide_with_other_types(self, scraper_build_vorgang):
        """After retiming, _enforce_total_ordering still produces unique slots
        for distinct Stationstypen."""
        raw = _make_raw_vorgang(
            "V-COLLISION",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  26.10.2021 Drucksache 17/5000",
                    "datum": "26.10.2021",
                    "drucksache": "17/5000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Finanzen  18.11.2021 Drucksache 17/5100",
                    "datum": "18.11.2021",
                    "drucksache": "17/5100",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/30 16.12.2021",
                    "datum": "16.12.2021",
                    "plenarprotokoll": "17/30",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        # Group by zp_start: each distinct timestamp must hold only one station type.
        slots: dict[datetime, set] = {}
        for s in vorgang.stationen:
            slots.setdefault(s.zp_start, set()).add(s.typ)
        for zp, types in slots.items():
            assert len(types) == 1, f"slot {zp} has multiple types {types}"

    def test_unit_retimes_ausschber_in_place(self):
        """Direct unit test on the static method, without the rest of _build_vorgang."""
        from bawue.types import Gremium, Parlament, Station

        def _station(typ: Stationstyp, zp_start: datetime, zp_modifiziert: datetime | None = None) -> Station:
            return Station(
                typ=typ,
                dokumente=[],
                zp_start=zp_start,
                zp_modifiziert=zp_modifiziert,
                gremium=Gremium(parlament=Parlament.BW, name="plenum", wahlperiode=17),
            )

        anchor = datetime(2021, 12, 16, tzinfo=UTC)
        ausschber_zp = datetime(2021, 11, 18, tzinfo=UTC)
        stationen = [
            _station(Stationstyp.PREPARL_REGBSL, datetime(2021, 10, 26, tzinfo=UTC)),
            _station(Stationstyp.PARL_INITIATIV, ausschber_zp),
            _station(Stationstyp.PARL_AUSSCHBER, ausschber_zp),
            _station(Stationstyp.PARL_VOLLVLSGN, anchor),
        ]

        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn(stationen)

        assert stationen[2].zp_start == anchor + timedelta(hours=1)
        # Other stations untouched.
        assert stationen[0].zp_start == datetime(2021, 10, 26, tzinfo=UTC)
        assert stationen[1].zp_start == ausschber_zp
        assert stationen[3].zp_start == anchor

    def test_unit_zp_modifiziert_invariant_maintained(self):
        """If the ausschber has zp_modifiziert set, retiming must keep it >= zp_start."""
        from bawue.types import Gremium, Parlament, Station

        anchor = datetime(2021, 12, 16, tzinfo=UTC)
        gremium = Gremium(parlament=Parlament.BW, name="plenum", wahlperiode=17)

        # zp_modifiziert was originally at the early ausschber date — it is now
        # before the bumped zp_start, so it must be advanced to match.
        early_zp = datetime(2021, 11, 18, tzinfo=UTC)
        ausschber = Station(
            typ=Stationstyp.PARL_AUSSCHBER,
            dokumente=[],
            zp_start=early_zp,
            zp_modifiziert=early_zp,
            gremium=gremium,
        )
        vollvlsgn = Station(
            typ=Stationstyp.PARL_VOLLVLSGN,
            dokumente=[],
            zp_start=anchor,
            gremium=gremium,
        )

        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn([ausschber, vollvlsgn])

        assert ausschber.zp_start == anchor + timedelta(hours=1)
        assert ausschber.zp_modifiziert == anchor + timedelta(hours=1)

    def test_unit_zp_modifiziert_only_advanced_when_below_new_start(self):
        """A zp_modifiziert that is already past the bump target must NOT be lowered."""
        from bawue.types import Gremium, Parlament, Station

        anchor = datetime(2021, 12, 16, tzinfo=UTC)
        gremium = Gremium(parlament=Parlament.BW, name="plenum", wahlperiode=17)

        # An ausschber spanning multiple weeks: modifier is past the future bump.
        ausschber = Station(
            typ=Stationstyp.PARL_AUSSCHBER,
            dokumente=[],
            zp_start=datetime(2021, 11, 18, tzinfo=UTC),
            zp_modifiziert=datetime(2022, 1, 5, tzinfo=UTC),
            gremium=gremium,
        )
        vollvlsgn = Station(
            typ=Stationstyp.PARL_VOLLVLSGN,
            dokumente=[],
            zp_start=anchor,
            gremium=gremium,
        )

        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn([ausschber, vollvlsgn])

        assert ausschber.zp_start == anchor + timedelta(hours=1)
        assert ausschber.zp_modifiziert == datetime(2022, 1, 5, tzinfo=UTC), "must not be reduced"

    def test_unit_no_op_when_no_vollvlsgn(self):
        """Without a vollvlsgn, ausschber stations are left untouched."""
        from bawue.types import Gremium, Parlament, Station

        gremium = Gremium(parlament=Parlament.BW, name="plenum", wahlperiode=17)
        ausschber_zp = datetime(2021, 11, 18, tzinfo=UTC)
        ausschber = Station(
            typ=Stationstyp.PARL_AUSSCHBER,
            dokumente=[],
            zp_start=ausschber_zp,
            gremium=gremium,
        )
        regbsl = Station(
            typ=Stationstyp.PREPARL_REGBSL,
            dokumente=[],
            zp_start=datetime(2021, 10, 26, tzinfo=UTC),
            gremium=gremium,
        )

        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn([regbsl, ausschber])

        assert ausschber.zp_start == ausschber_zp

    def test_unit_empty_list_returns_cleanly(self):
        """Empty stationen must not raise."""
        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn([])


# Authoritative station→letter mapping and BW track, copied verbatim from the
# backend's configured trackfile (see DD-036):
#   docs/specs/tracks.toml @ tag v0.2.3+v0.0.7, version 0.0.7
#   https://codeberg.org/PaZuFa/parlamentszusammenfasser/raw/tag/v0.2.3+v0.0.7/docs/specs/tracks.toml
# The backend maps each Station's ``typ`` to the [stations] letter, joins them in
# zp_start order, then checks the [tracks.BW] regex as a PREFIX match — a Vorgang
# is valid iff its letters are consumed as the *start* of an accepting word, so
# later stages (parl-akzeptanz J, postparl G/K, …) may legitimately be missing.
_TRACKS_TOML_STATIONS = {
    "preparl-regent": "R",
    "preparl-eckpup": "E",
    "preparl-regbsl": "S",
    "parl-initiativ": "I",
    "parl-vollvlsgn": "L",
    "parl-akzeptanz": "J",
    "parl-zurueckgz": "Z",
    "parl-ablehnung": "N",
    "parl-ausschber": "A",
    "postparl-gsblt": "G",
    "postparl-kraft": "K",
    "preparl-vbegde": "B",
    "parl-ggentwurf": "W",
    "postparl-vesja": "Y",
    "postparl-vesne": "X",
}
# Build the Stationstyp→letter map, keeping only the station strings that are
# valid Stationstyp members (mirrors the backend's station_mapping).
_GG_LAND_PARL_LETTER = {
    Stationstyp(value): letter
    for value, letter in _TRACKS_TOML_STATIONS.items()
    if value in {s.value for s in Stationstyp}
}

# [tracks.BW] gg-land-parl, verbatim.
_BW_GG_LAND_PARL_TRACK = "((E*R+)?S)?I((LA*(Z|LJGA*KA*|LN|LA*(Z|LJGA*KA*|LN)))|Z)"


def _passes_bw_track_validation(track: str) -> bool:
    """Mirror the backend's track validation for ``BW.gg-land-parl``.

    The backend accepts a Vorgang iff its station-letter string is fully consumed
    as a *prefix* of an accepting word (``matched && consumed == len``; see
    validate.rs / DD-036). Python's ``regex`` module reproduces exactly this with
    ``fullmatch(..., partial=True)``: a proper prefix returns a partial match.
    """
    regex = pytest.importorskip("regex")  # provided transitively via litellm→tiktoken
    return regex.fullmatch(_BW_GG_LAND_PARL_TRACK, track, partial=True) is not None


# The five processes from issue #48. Real dates from the local WP17 dump;
# Fundstellen are fed to the parametrized test in a deliberately non-chronological
# order to exercise the ordering helpers end-to-end.
# (vid, label, gesetz_drs, besch_drs, regbsl, ausschber, reading1, reading2)
_ISSUE_48_DOCUMENTS = [
    ("V-216013", "17/1000 Einzelplan 17", "17/1000", "17/1117", "26.10.2021", "19.11.2021", "17.12.2021", "22.12.2021"),
    ("V-216044", "17/1000 Einzelplan 16", "17/1000", "17/1118", "26.10.2021", "19.11.2021", "17.12.2021", "22.12.2021"),
    ("V-222757", "17/3500 Einzelplan 11", "17/3500", "17/3611", "25.10.2022", "17.11.2022", "14.12.2022", "21.12.2022"),
    ("V-222765", "17/3500 Einzelplan 16", "17/3500", "17/3616", "25.10.2022", "24.11.2022", "16.12.2022", "21.12.2022"),
    ("V-222767", "17/3500 Einzelplan 17", "17/3500", "17/3617", "25.10.2022", "18.11.2022", "16.12.2022", "21.12.2022"),
]


class TestIssue48OrderByZpStartNotListPosition:
    """Issue #48: ordering helpers must reason about ``zp_start``, not list index.

    PARLIS does not list Fundstellen in chronological order — a budget
    Beschlussempfehlung dated in mid-November can appear *after* the December
    first reading, and readings can be listed out of date order. The backend
    sorts stations by ``zp_start`` before validating the ``gg-land-parl`` track,
    so any helper keyed off list position leaves such stations out of canonical
    order → HTTP 400 "Track validation Failed".
    """

    @staticmethod
    def _station(typ: Stationstyp, zp: datetime) -> "object":
        from bawue.types import Gremium, Parlament, Station

        return Station(
            typ=typ,
            dokumente=[],
            zp_start=zp,
            gremium=Gremium(parlament=Parlament.BW, name="plenum", wahlperiode=17),
        )

    def test_ausschber_retimed_even_when_listed_after_vollvlsgn(self):
        """An ausschber dated before the reading but *listed after* it must still
        be retimed. The old ``stationen[:first_vollvlsgn_idx]`` slice never
        reached an ausschber positioned past the first reading in the list."""
        s = self._station
        stationen = [
            s(Stationstyp.PREPARL_REGBSL, datetime(2021, 10, 26, tzinfo=UTC)),
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 17, tzinfo=UTC)),
            s(Stationstyp.PARL_AUSSCHBER, datetime(2021, 11, 19, tzinfo=UTC)),
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 22, tzinfo=UTC)),
        ]
        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn(stationen)
        assert stationen[2].zp_start == datetime(2021, 12, 17, 1, tzinfo=UTC)

    def test_ausschber_anchor_is_earliest_vollvlsgn_by_date_not_first_in_list(self):
        """The anchor is the earliest vollvlsgn by ``zp_start``, even when a
        later-dated reading appears first in the list."""
        s = self._station
        stationen = [
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 22, tzinfo=UTC)),  # later reading, listed first
            s(Stationstyp.PARL_AUSSCHBER, datetime(2021, 11, 19, tzinfo=UTC)),
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 17, tzinfo=UTC)),  # earliest reading
        ]
        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn(stationen)
        assert stationen[1].zp_start == datetime(2021, 12, 17, 1, tzinfo=UTC)

    def test_ausschber_on_the_same_day_as_first_reading_is_still_bumped(self):
        """Boundary: an ausschber dated *exactly* on the earliest reading has an
        ambiguous order (same zp_start, different typ), so it must be pushed to
        anchor + 1h rather than left tied."""
        s = self._station
        stationen = [
            s(Stationstyp.PARL_AUSSCHBER, datetime(2021, 12, 17, tzinfo=UTC)),
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 17, tzinfo=UTC)),
        ]
        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn(stationen)
        assert stationen[0].zp_start == datetime(2021, 12, 17, 1, tzinfo=UTC)

    def test_ausschber_after_earliest_reading_is_left_untouched(self):
        """An ausschber that legitimately falls between two readings (dated after
        the earliest reading) keeps its date — only earlier-or-equal ones move."""
        s = self._station
        between = datetime(2021, 12, 18, tzinfo=UTC)
        stationen = [
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 17, tzinfo=UTC)),
            s(Stationstyp.PARL_AUSSCHBER, between),
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 22, tzinfo=UTC)),
        ]
        BawueVorgaengeScraper._ensure_ausschber_after_vollvlsgn(stationen)
        assert stationen[1].zp_start == between

    def test_synthetic_initiativ_dated_from_first_reading_not_list_next(self):
        """The synthetic parl-initiativ is dated from the earliest vollvlsgn, not
        the list-next station: when the station following the regbsl is a
        later-dated reading, the old code pushed the introduction *after* an
        earlier first reading."""
        s = self._station
        scraper = BawueVorgaengeScraper.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        stationen = [
            s(Stationstyp.PREPARL_REGBSL, datetime(2021, 10, 26, tzinfo=UTC)),
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 22, tzinfo=UTC)),  # later reading, list-next
            s(Stationstyp.PARL_VOLLVLSGN, datetime(2021, 12, 17, tzinfo=UTC)),  # earlier first reading
        ]
        scraper._ensure_initiativ_after_regbsl(stationen)
        initiativ = next(x for x in stationen if x.typ == Stationstyp.PARL_INITIATIV)
        assert initiativ.zp_start == datetime(2021, 12, 17, tzinfo=UTC)

    def test_synthetic_initiativ_falls_back_to_regbsl_date_when_nothing_follows(self):
        """Fallback: with no station following the regbsl (no reading to anchor
        on), the synthetic initiativ inherits the regbsl's date. _build_vorgang's
        later _enforce_total_ordering then separates the tie."""
        s = self._station
        scraper = BawueVorgaengeScraper.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        regbsl_zp = datetime(2021, 10, 26, tzinfo=UTC)
        stationen = [s(Stationstyp.PREPARL_REGBSL, regbsl_zp)]
        scraper._ensure_initiativ_after_regbsl(stationen)
        initiativ = next(x for x in stationen if x.typ == Stationstyp.PARL_INITIATIV)
        assert initiativ.zp_start == regbsl_zp

    @staticmethod
    def _track_string(stationen: list) -> str:
        """Whole track as the backend sees it: stations sorted by ``zp_start``,
        each mapped to its ``gg-land-parl`` letter. Fails loudly on a Stationstyp
        this test does not know how to score, so an unexpected type can't slip
        through as a silent pass."""
        letters = []
        for s in sorted(stationen, key=lambda s: s.zp_start):
            assert s.typ in _GG_LAND_PARL_LETTER, f"unmapped Stationstyp in track: {s.typ}"
            letters.append(_GG_LAND_PARL_LETTER[s.typ])
        return "".join(letters)

    @classmethod
    def _assert_valid_track(cls, stationen: list, expected: str) -> None:
        """Assert the *whole* zp_start-sorted track would pass backend validation.

        Unlike per-date assertions, this exercises the full ordering contract the
        backend enforces:

        * the letter string (stations mapped and sorted by ``zp_start``) equals
          ``expected`` (e.g. ``"SILAL"``);
        * that string passes the real ``BW.gg-land-parl`` prefix match — i.e. the
          backend would accept the Vorgang (DD-036);
        * no two *different-typed* stations share a ``zp_start`` — the backend
          sorts by ``zp_start``, so a tie between different types leaves the order
          ambiguous. ``_enforce_total_ordering`` guarantees this (issue #48 root).
        """
        track = cls._track_string(stationen)
        assert track == expected, f"track {track!r} != expected {expected!r}"
        assert _passes_bw_track_validation(track), f"track {track!r} rejected by BW.gg-land-parl validation"

        slots: dict = {}
        for s in stationen:
            slots.setdefault(s.zp_start, set()).add(s.typ)
        collisions = {zp: types for zp, types in slots.items() if len(types) > 1}
        assert not collisions, f"different-typed stations share a zp_start: {collisions}"

    @pytest.mark.parametrize(
        ("vid", "label", "gesetz_drs", "besch_drs", "regbsl", "ausschber", "reading1", "reading2"),
        _ISSUE_48_DOCUMENTS,
        ids=[c[0] for c in _ISSUE_48_DOCUMENTS],
    )
    @pytest.mark.asyncio
    async def test_affected_document_builds_valid_track(
        self, scraper_build_vorgang, vid, label, gesetz_drs, besch_drs, regbsl, ausschber, reading1, reading2
    ):
        """Each of the five issue-#48 processes must build a valid whole track.

        The committee report is dated ~a month before the first reading but the
        Fundstellen are listed out of order (later reading first, committee report
        last). The built Vorgang must still resolve, once sorted by ``zp_start``,
        to the canonical ``preparl-regbsl → parl-initiativ → parl-vollvlsgn →
        parl-ausschber → parl-vollvlsgn`` track (``SILAL``) with no tied slots.
        """
        raw = _make_raw_vorgang(
            vid,
            titel=f"Staatshaushaltsplan {label}",
            vorgangstyp="Haushaltsgesetzgebung",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": f"Gesetzentwurf Landesregierung {regbsl} Drucksache {gesetz_drs}",
                    "datum": regbsl,
                    "drucksache": gesetz_drs,
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/gesetz.pdf",
                },
                {
                    "raw": f"Dritte Beratung Plenarprotokoll 17/99 {reading2}",
                    "datum": reading2,
                    "plenarprotokoll": "17/99",
                    "station_typ": "Dritte Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": f"Zweite Beratung Plenarprotokoll 17/98 {reading1}",
                    "datum": reading1,
                    "plenarprotokoll": "17/98",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
                {
                    "raw": f"Beschlussempfehlung und Bericht {ausschber} Drucksache {besch_drs}",
                    "datum": ausschber,
                    "drucksache": besch_drs,
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/beschluss.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)
        self._assert_valid_track(vorgang.stationen, expected="SILAL")

    @pytest.mark.parametrize(
        ("track", "valid"),
        [
            # Valid prefixes of an accepting BW.gg-land-parl word:
            ("SILAL", True),  # the Haushalt Einzelplan shape the five issue-#48 cases resolve to
            ("SIL", True),  # in progress: through the first reading only
            ("ILAL", True),  # Fraktion bill (no preparl-regbsl)
            ("SILALJGK", True),  # fully completed process (…akzeptanz, Gesetzblatt, in Kraft)
            ("SILALN", True),  # rejected at the final reading (N = parl-ablehnung)
            # Invalid orderings — the ones issue #48 produced or guards against:
            ("SALIL", False),  # committee report before the first reading, initiativ after it
            ("SLIAL", False),  # reading before the parl-initiativ
            ("LIAL", False),  # reading before the parl-initiativ, no preparl
            ("SIALL", False),  # ausschber immediately after initiativ, before any reading
        ],
    )
    def test_bw_track_prefix_validation_matches_backend(self, track, valid):
        """Pin the prefix-match semantics and the station→letter mapping against
        the real ``BW.gg-land-parl`` track (DD-036): valid prefixes (including
        in-progress and completed processes) pass; the mis-orderings issue #48
        produced are rejected."""
        assert _passes_bw_track_validation(track) is valid


class TestAblehnungAnchorsAfterAusschberRetiming:
    """Regression for V-246637 (Gesetz zur Änderung des Abgeordnetengesetzes, WP18).

    PARLIS listed Fundstellen in the order Gesetzentwurf → Beschlussempfehlung
    und Bericht (Ausschuss) → Zweite Beratung, i.e. the ausschber's Fundstelle
    arrives, and is appended to ``stationen``, before the vollvlsgn's — even
    though the committee report is chronologically earlier than the reading.
    ``_ensure_ausschber_after_vollvlsgn`` retimes the ausschber to one hour past
    vollvlsgn, but previously ``_ensure_ablehnung_station`` ran first and
    anchored on ``stationen[-1]`` (list-position-last, i.e. vollvlsgn) instead
    of the true chronological last station. That produced the order
    initiativ → vollvlsgn → ausschber → ablehnung, which the BW gg-land-parl
    track rejects with HTTP 400 "Track validation Failed". The fix reorders
    the ensure-calls and anchors on the chronological max zp_start, producing
    initiativ → vollvlsgn → ausschber → ablehnung with ablehnung strictly last.
    """

    @pytest.mark.asyncio
    async def test_ablehnung_lands_after_retimed_ausschber(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-246637",
            titel="Gesetz zur Änderung des Abgeordnetengesetzes",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  03.06.2026 Drucksache 18/1000",
                    "datum": "03.06.2026",
                    "drucksache": "18/1000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ständiger Ausschuss  24.06.2026 Drucksache 18/1100",
                    "datum": "24.06.2026",
                    "drucksache": "18/1100",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ständiger Ausschuss",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 18/10 01.07.2026",
                    "datum": "01.07.2026",
                    "plenarprotokoll": "18/10",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = await scraper_build_vorgang(raw)

        ablehnung = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_ABLEHNUNG)
        vollvlsgn = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN)
        ausschber = next(s for s in vorgang.stationen if s.typ == Stationstyp.PARL_AUSSCHBER)

        assert vollvlsgn.zp_start < ausschber.zp_start < ablehnung.zp_start, (
            "gg-land-parl requires ablehnung strictly after the retimed ausschber, "
            f"got vollvlsgn={vollvlsgn.zp_start}, ausschber={ausschber.zp_start}, "
            f"ablehnung={ablehnung.zp_start}"
        )


class TestBeschlussDesLandtagsInBeratung:
    """Regression: 'Beschluss des Landtags in Zweiter/Dritter Beratung' must map to
    parl-vollvlsgn (reading vote), not parl-akzeptanz (acceptance).

    When PARLIS places a double-space between 'Landtags' and 'in', the parser
    truncates station_typ to 'Beschluss des Landtags'. The mapper must still
    use the full raw text to detect the 'in' qualifier.
    """

    @pytest.mark.asyncio
    async def test_beschluss_in_zweiter_beratung_with_truncated_station_typ(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        beschluss_station = vorgang.stationen[1]
        assert beschluss_station.typ == Stationstyp.PARL_VOLLVLSGN

    @pytest.mark.asyncio
    async def test_beschluss_in_dritter_beratung_with_truncated_station_typ(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        beschluss_station = vorgang.stationen[1]
        assert beschluss_station.typ == Stationstyp.PARL_VOLLVLSGN

    @pytest.mark.asyncio
    async def test_plain_beschluss_des_landtags_still_maps_to_akzeptanz(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)

        beschluss_station = vorgang.stationen[1]
        assert beschluss_station.typ == Stationstyp.PARL_AKZEPTANZ


class TestReadingRoundEquivalence:
    """Tests for DD-026: 'Beschluss des Landtags in <Ordinal>er Beratung' is the
    same reading round as '<Ordinal>e Beratung' and merges into one parl-vollvlsgn.

    Motivation: BW Haushalts-Einzelpläne emit four V-mapped Fundstellen per
    Vorgang (Zweite Beratung + Beschluss in Zweiter + Dritte Beratung + Beschluss
    in Dritter). DD-024's strict same-text merge keeps them as 4 V stations,
    overrunning the BW gg-land-parl track regex's 3-V budget.
    """

    @pytest.mark.asyncio
    async def test_zweite_and_beschluss_in_zweiter_merged(self, scraper_build_vorgang):
        """Two consecutive V Fundstellen, 'Zweite Beratung' + 'Beschluss des
        Landtags in Zweiter Beratung', merge into one V with both documents."""
        raw = _make_raw_vorgang(
            "V-DD026-1",
            fundstellen=[
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/55 14.12.2022",
                    "datum": "14.12.2022",
                    "plenarprotokoll": "17/55",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp55.pdf",
                },
                {
                    "raw": "Beschluss des Landtags in Zweiter Beratung   16.12.2022 Drucksache 17/3820",
                    "datum": "16.12.2022",
                    "drucksache": "17/3820",
                    "station_typ": "Beschluss des Landtags in Zweiter Beratung",
                    "pdf_url": "https://example.com/beschluss.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        v_stations = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN]
        assert len(v_stations) == 1
        assert v_stations[0].zp_start == datetime(2022, 12, 14, tzinfo=UTC)
        assert v_stations[0].zp_modifiziert == datetime(2022, 12, 16, tzinfo=UTC)
        assert len(v_stations[0].dokumente) == 2

    @pytest.mark.asyncio
    async def test_haushalt_einzelplan_collapses_to_two_v_stations(self, scraper_build_vorgang):
        """End-to-end V-222745 pattern: Gesetzentwurf, Beschlussempfehlung, four
        V Fundstellen (Zweite, Beschluss-in-Zweiter, Dritte, Beschluss-in-Dritter).

        After DD-026 merging, exactly 2 V stations remain — fits the BW
        gg-land-parl track's 3-V budget.
        """
        raw = _make_raw_vorgang(
            "V-222745",
            titel="Staatshaushaltsplan 2023/2024 - Einzelplan 07",
            vorgangstyp="Haushaltsgesetzgebung",
            initiative="Landesregierung",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Landesregierung  25.10.2022 Drucksache 17/3500",
                    "datum": "25.10.2022",
                    "drucksache": "17/3500",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ausschuss für Finanzen  17.11.2022 Drucksache 17/3707",
                    "datum": "17.11.2022",
                    "drucksache": "17/3707",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ausschuss für Finanzen",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 17/55 14.12.2022",
                    "datum": "14.12.2022",
                    "plenarprotokoll": "17/55",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "https://example.com/pp55.pdf",
                },
                {
                    "raw": "Beschluss des Landtags in Zweiter Beratung   16.12.2022 Drucksache 17/3807",
                    "datum": "16.12.2022",
                    "drucksache": "17/3807",
                    "station_typ": "Beschluss des Landtags in Zweiter Beratung",
                    "pdf_url": "https://example.com/beschluss2.pdf",
                },
                {
                    "raw": "Dritte Beratung   Plenarprotokoll 17/57 21.12.2022",
                    "datum": "21.12.2022",
                    "plenarprotokoll": "17/57",
                    "station_typ": "Dritte Beratung",
                    "pdf_url": "https://example.com/pp57.pdf",
                },
                {
                    "raw": "Beschluss des Landtags in Dritter Beratung   21.12.2022 Drucksache 17/3842",
                    "datum": "21.12.2022",
                    "drucksache": "17/3842",
                    "station_typ": "Beschluss des Landtags in Dritter Beratung",
                    "pdf_url": "https://example.com/beschluss3.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        v_stations = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN]
        assert len(v_stations) == 2, (
            f"Expected 2 V stations after DD-026 merging (Zweite+Beschluss-in-Zweiter and "
            f"Dritte+Beschluss-in-Dritter), got {len(v_stations)}"
        )
        # First merged V: Zweite + Beschluss in Zweiter, span 14.12 → 16.12, 2 docs.
        assert v_stations[0].zp_start == datetime(2022, 12, 14, tzinfo=UTC)
        assert v_stations[0].zp_modifiziert == datetime(2022, 12, 16, tzinfo=UTC)
        assert len(v_stations[0].dokumente) == 2
        # Second merged V: Dritte + Beschluss in Dritter, both 21.12, 2 docs.
        assert v_stations[1].zp_start == datetime(2022, 12, 21, tzinfo=UTC)
        assert len(v_stations[1].dokumente) == 2

        # Whole sequence must be S, I, V, A, V (sorted by zp_start) — fits regex.
        sorted_typen = [s.typ for s in sorted(vorgang.stationen, key=lambda s: s.zp_start)]
        assert sorted_typen == [
            Stationstyp.PREPARL_REGBSL,
            Stationstyp.PARL_INITIATIV,
            Stationstyp.PARL_VOLLVLSGN,
            Stationstyp.PARL_AUSSCHBER,
            Stationstyp.PARL_VOLLVLSGN,
        ]

    @pytest.mark.asyncio
    async def test_different_rounds_still_separate(self, scraper_build_vorgang):
        """Defensive: 'Erste Beratung' and 'Zweite Beratung' are different rounds → not merged.
        Mirrors the existing test_consecutive_vollvlsgn_not_merged_even_with_documents
        expectation, ensuring DD-026 didn't over-merge.
        """
        raw = _make_raw_vorgang(
            "V-DD026-2",
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
        vorgang = await scraper_build_vorgang(raw)

        v_stations = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN]
        assert len(v_stations) == 2

    @pytest.mark.asyncio
    async def test_ueberweisung_not_merged_with_erste_beratung(self, scraper_build_vorgang):
        """'Überweisung' has no 'Beratung' substring → falls back to exact-match → stays separate."""
        raw = _make_raw_vorgang(
            "V-DD026-3",
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
        vorgang = await scraper_build_vorgang(raw)

        v_stations = [s for s in vorgang.stationen if s.typ == Stationstyp.PARL_VOLLVLSGN]
        assert len(v_stations) == 2

    def test_unit_reading_round_extracts_from_plain_label(self):
        assert _reading_round("Erste Beratung") == 1
        assert _reading_round("Zweite Beratung") == 2
        assert _reading_round("Dritte Beratung") == 3

    def test_unit_reading_round_extracts_from_beschluss_in_label(self):
        assert _reading_round("Beschluss des Landtags in Erster Beratung") == 1
        assert _reading_round("Beschluss des Landtags in Zweiter Beratung") == 2
        assert _reading_round("Beschluss des Landtags in Dritter Beratung") == 3

    def test_unit_reading_round_returns_none_for_non_round_labels(self):
        # No 'Beratung' substring → not a round indicator.
        assert _reading_round("Überweisung") is None
        assert _reading_round("Schlussabstimmung") is None
        assert _reading_round("Gesetzesbeschluss des Landtags") is None
        assert _reading_round("") is None

    def test_unit_same_round_label_treats_zweite_and_beschluss_in_zweiter_equal(self):
        assert _same_round_label("Zweite Beratung", "Beschluss des Landtags in Zweiter Beratung")
        assert _same_round_label("Beschluss des Landtags in Dritter Beratung", "Dritte Beratung")

    def test_unit_same_round_label_distinguishes_different_rounds(self):
        assert not _same_round_label("Erste Beratung", "Zweite Beratung")
        assert not _same_round_label(
            "Beschluss des Landtags in Zweiter Beratung",
            "Beschluss des Landtags in Dritter Beratung",
        )

    def test_unit_same_round_label_empty_label_is_defensive_false(self):
        # Defensive default from DD-024 must be preserved even after DD-026.
        assert not _same_round_label("", "Zweite Beratung")
        assert not _same_round_label("Zweite Beratung", "")
        assert not _same_round_label("", "")


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

    @pytest.mark.asyncio
    async def test_station_skipped_when_date_empty(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 0

    @pytest.mark.asyncio
    async def test_station_skipped_when_date_unparseable(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 0

    @pytest.mark.asyncio
    async def test_all_stations_skipped_logs_error(self, scraper_build_vorgang, caplog):
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
        with caplog.at_level(logging.WARNING, logger="bawue.bawue_vorgaenge_scraper"):
            vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 0
        assert any("ALL stations were skipped" in msg for msg in caplog.messages)
        assert any("V-803" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_valid_station_kept_alongside_skipped(self, scraper_build_vorgang):
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
        vorgang = await scraper_build_vorgang(raw)
        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].zp_start == datetime(2025, 6, 15, tzinfo=UTC)


class TestTrojanergefahr:
    @pytest.mark.asyncio
    async def test_trojanergefahr_set_on_station_when_llm_enabled(self):
        """LLM returns trojanergefahr → Station gets the value."""
        from unittest.mock import AsyncMock

        from bawue.bawue_dok import EnrichmentResult
        from bawue.types import Autor, Dokument

        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = True
        scraper._llm = AsyncMock()
        scraper._llm_model = "gpt-5-nano"
        scraper._llm_metrics = LLMMetrics()
        scraper._filter_sonstig = True
        scraper.session = MagicMock()
        scraper.config = MagicMock()

        enriched_dok = Dokument(
            titel="Testgesetz",
            volltext="extracted text",
            hash_="abc123",
            typ=Doktyp.ENTWURF,
            zp_modifiziert=datetime(2026, 2, 4, tzinfo=UTC),
            zp_referenz=datetime(2026, 2, 4, tzinfo=UTC),
            link="https://www.landtag-bw.de/resource/blob/12345/doc.pdf",
            autoren=[Autor(person="Test", organisation="Fraktion GRÜNE")],
            drucksnr="17/10266",
        )
        mock_result = EnrichmentResult(
            dokument=enriched_dok,
            trojanergefahr=7,
        )

        raw = _make_raw_vorgang(
            "V-900",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 13,
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/12345/doc.pdf",
                },
            ],
        )

        with patch("bawue.bawue_dok.enrich_dokument", new_callable=AsyncMock, return_value=mock_result):
            vorgang = await scraper._build_vorgang(raw)

        assert vorgang.stationen[0].trojanergefahr == 7

    @pytest.mark.asyncio
    async def test_trojanergefahr_none_when_llm_disabled(self):
        """LLM disabled → Station.trojanergefahr is None."""
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = True
        scraper.session = MagicMock()

        raw = _make_raw_vorgang(
            "V-901",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 13,
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/12345/doc.pdf",
                },
            ],
        )
        vorgang = await scraper._build_vorgang(raw)

        assert vorgang.stationen[0].trojanergefahr is None


class TestFilterSonstigStations:
    """Tests for configurable sonstig station filtering."""

    @pytest.mark.asyncio
    async def test_sonstig_station_filtered_when_enabled(self, scraper_build_vorgang):
        """When filter-sonstig-stations=true, sonstig stations are removed."""
        raw = _make_raw_vorgang(
            "V-700",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 13,
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/12345/doc.pdf",
                },
                {
                    "raw": "Mitteilung   Plenarprotokoll 17/141  06.02.2026",
                    "datum": "06.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Mitteilung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_INITIATIV

    @pytest.mark.asyncio
    async def test_sonstig_station_kept_when_disabled(self):
        """When filter-sonstig-stations=false, sonstig stations pass through."""
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 17
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = False
        scraper.session = MagicMock()

        raw = _make_raw_vorgang(
            "V-701",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 13,
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/12345/doc.pdf",
                },
                {
                    "raw": "Mitteilung   Plenarprotokoll 17/141  06.02.2026",
                    "datum": "06.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Mitteilung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper._build_vorgang(raw)

        assert len(vorgang.stationen) == 2
        assert vorgang.stationen[1].typ == Stationstyp.SONSTIG

    @pytest.mark.asyncio
    async def test_sonstig_filtering_logs_debug_message(self, scraper_build_vorgang, caplog):
        """Filtered sonstig stations are logged at DEBUG level."""
        raw = _make_raw_vorgang(
            "V-702",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "seiten": 13,
                    "pdf_url": "https://www.landtag-bw.de/resource/blob/12345/doc.pdf",
                },
                {
                    "raw": "Mitteilung   Plenarprotokoll 17/141  06.02.2026",
                    "datum": "06.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Mitteilung",
                    "pdf_url": "",
                },
            ],
        )
        with caplog.at_level(logging.DEBUG, logger="bawue.bawue_vorgaenge_scraper"):
            await scraper_build_vorgang(raw)

        assert any("sonstig" in r.message.lower() for r in caplog.records)


class TestUnlabeledPlenarprotokollFallback:
    """Regression for V-246637 (Gesetz zur Änderung des Abgeordnetengesetzes, WP18).

    PARLIS occasionally omits the leading station-type label on a plenary
    reading's Fundstelle text, e.g. a bare "Plenarprotokoll 18/6 10.06.2026
    S. 94-97" with no "Erste Beratung" prefix. The parser's station_typ regex
    requires a double-space/tab-terminated leading label, so it leaves
    station_typ empty; map_stationstyp() then falls through to SONSTIG on the
    raw-text fallback (no "Plenarprotokoll" key in STATIONSTYP_MAP), and the
    default filter-sonstig-stations=true setting silently drops the station —
    losing the Vorgang's first plenary reading entirely.

    The fix: the first unlabeled Plenarprotokoll Fundstelle encountered before
    any properly-labeled parl-vollvlsgn is recovered as parl-vollvlsgn instead
    of being discarded.
    """

    @pytest.mark.asyncio
    async def test_unlabeled_erste_beratung_recovered_as_vollvlsgn(self, scraper_build_vorgang):
        raw = _make_raw_vorgang(
            "V-246637",
            titel="Gesetz zur Änderung des Abgeordnetengesetzes",
            initiative="Fraktion der AfD",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion der AfD  03.06.2026 Drucksache 18/1000",
                    "datum": "03.06.2026",
                    "drucksache": "18/1000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    # No "station_typ": PARLIS omitted the leading label; the
                    # regex-based extraction failed on this Fundstelle.
                    "raw": "Plenarprotokoll 18/6 10.06.2026 S. 94-97",
                    "datum": "10.06.2026",
                    "plenarprotokoll": "18/6",
                    "pdf_url": "https://example.com/plp-18-6.pdf",
                },
                {
                    "raw": "Beschlussempfehlung und Bericht   Ständiger Ausschuss  24.06.2026 Drucksache 18/1100",
                    "datum": "24.06.2026",
                    "drucksache": "18/1100",
                    "station_typ": "Beschlussempfehlung und Bericht",
                    "ausschuss": "Ständiger Ausschuss",
                    "pdf_url": "https://example.com/ausschber.pdf",
                },
                {
                    "raw": "Zweite Beratung   Plenarprotokoll 18/10 01.07.2026",
                    "datum": "01.07.2026",
                    "plenarprotokoll": "18/10",
                    "station_typ": "Zweite Beratung",
                    "pdf_url": "",
                },
            ],
        )
        raw["Aktueller Stand"] = "Abgelehnt"
        vorgang = await scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        assert station_types == [
            Stationstyp.PARL_INITIATIV,
            Stationstyp.PARL_VOLLVLSGN,  # recovered, was SONSTIG and would've been dropped
            Stationstyp.PARL_AUSSCHBER,
            Stationstyp.PARL_VOLLVLSGN,
            Stationstyp.PARL_ABLEHNUNG,
        ]

    @pytest.mark.asyncio
    async def test_unlabeled_plenarprotokoll_after_first_reading_not_reclassified(self, scraper_build_vorgang):
        """Only the *first* unlabeled Plenarprotokoll Fundstelle is recovered.

        A second, later bare Plenarprotokoll entry (once a reading has already
        been recorded) is left as SONSTIG and filtered — it is not assumed to
        be another reading.
        """
        raw = _make_raw_vorgang(
            "V-UNLABELED-2",
            initiative="Fraktion GRÜNE",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  01.03.2026 Drucksache 17/2000",
                    "datum": "01.03.2026",
                    "drucksache": "17/2000",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Erste Beratung   Plenarprotokoll 17/200 10.03.2026",
                    "datum": "10.03.2026",
                    "plenarprotokoll": "17/200",
                    "station_typ": "Erste Beratung",
                    "pdf_url": "",
                },
                {
                    # Unlabeled, but a vollvlsgn was already recorded above.
                    "raw": "Plenarprotokoll 17/210 20.03.2026 S. 12-14",
                    "datum": "20.03.2026",
                    "plenarprotokoll": "17/210",
                    "pdf_url": "https://example.com/plp-17-210.pdf",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        station_types = [s.typ for s in vorgang.stationen]
        assert station_types == [Stationstyp.PARL_INITIATIV, Stationstyp.PARL_VOLLVLSGN]

    @pytest.mark.asyncio
    async def test_labeled_sonstig_fundstelle_still_filtered(self, scraper_build_vorgang):
        """A Fundstelle with an explicit (non-reading) label, e.g. 'Mitteilung',
        is still filtered as SONSTIG even if it references a Plenarprotokoll —
        only *unlabeled* Fundstellen get the positional fallback."""
        raw = _make_raw_vorgang(
            "V-UNLABELED-3",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266",
                    "datum": "04.02.2026",
                    "drucksache": "17/10266",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "https://example.com/entwurf.pdf",
                },
                {
                    "raw": "Mitteilung   Plenarprotokoll 17/141  06.02.2026",
                    "datum": "06.02.2026",
                    "plenarprotokoll": "17/141",
                    "station_typ": "Mitteilung",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper_build_vorgang(raw)

        assert len(vorgang.stationen) == 1
        assert vorgang.stationen[0].typ == Stationstyp.PARL_INITIATIV


class TestConstructDrucksachePdfUrl:
    """Pure URL construction from a Drucksache number (PARLIS PDF-link fallback)."""

    @pytest.mark.parametrize(
        ("drucksache", "expected_suffix"),
        [
            ("18/75", "WP18/Drucksachen/0000/18_0075.pdf"),
            ("17/8587", "WP17/Drucksachen/8000/17_8587.pdf"),
            ("18/10266", "WP18/Drucksachen/10000/18_10266.pdf"),
            (" 18/75 ", "WP18/Drucksachen/0000/18_0075.pdf"),
        ],
    )
    def test_builds_predictable_url(self, drucksache, expected_suffix):
        url = _construct_drucksache_pdf_url(drucksache)
        assert url is not None
        assert url.endswith(expected_suffix)

    @pytest.mark.parametrize("drucksache", [None, "", "garbage", "18-75"])
    def test_returns_none_for_missing_or_malformed(self, drucksache):
        assert _construct_drucksache_pdf_url(drucksache) is None


def _head_session(status: int) -> MagicMock:
    """Build a MagicMock aiohttp session whose .head() yields a response with ``status``."""
    response = MagicMock()
    response.status = status
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.head = MagicMock(return_value=ctx)
    return session


@pytest.fixture()
def fallback_scraper():
    """A bare scraper instance exposing _fallback_pdf_url without full init."""
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper.session = _head_session(200)
    return scraper


class TestFallbackPdfUrl:
    """Reconstruct + verify the PDF URL when PARLIS leaves it empty."""

    @pytest.mark.asyncio
    async def test_returns_url_when_verification_succeeds(self, fallback_scraper):
        fallback_scraper.session = _head_session(200)
        url = await fallback_scraper._fallback_pdf_url("18/75")
        assert url is not None and url.endswith("WP18/Drucksachen/0000/18_0075.pdf")

    @pytest.mark.asyncio
    async def test_returns_none_when_url_does_not_resolve(self, fallback_scraper):
        fallback_scraper.session = _head_session(404)
        assert await fallback_scraper._fallback_pdf_url("18/75") is None

    @pytest.mark.asyncio
    async def test_returns_none_without_drucksache(self, fallback_scraper):
        assert await fallback_scraper._fallback_pdf_url(None) is None
        # No network call attempted for a missing Drucksache.
        fallback_scraper.session.head.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_request_exception(self, fallback_scraper):
        fallback_scraper.session.head = MagicMock(side_effect=RuntimeError("boom"))
        assert await fallback_scraper._fallback_pdf_url("18/75") is None


class TestBuildVorgangPdfFallback:
    """End-to-end: an empty pdf_url with a Drucksache yields a reconstructed link."""

    @pytest.mark.asyncio
    async def test_empty_pdf_url_reconstructed_from_drucksache(self):
        scraper = object.__new__(BawueVorgaengeScraper)
        scraper._wahlperiode = 18
        scraper._llm_enabled = False
        scraper._llm = None
        scraper._filter_sonstig = True
        scraper.session = _head_session(200)

        raw = _make_raw_vorgang(
            "V-246637",
            titel="Gesetz zur Änderung des Abgeordnetengesetzes",
            initiative="Fraktion der AfD",
            fundstellen=[
                {
                    "raw": "Gesetzentwurf    Fraktion der AfD  03.06.2026 Drucksache 18/75   (4 S.)",
                    "datum": "03.06.2026",
                    "drucksache": "18/75",
                    "station_typ": "Gesetzentwurf",
                    "pdf_url": "",
                },
            ],
        )
        vorgang = await scraper._build_vorgang(raw)

        docs = vorgang.stationen[0].dokumente
        assert len(docs) == 1
        assert docs[0].link.endswith("WP18/Drucksachen/0000/18_0075.pdf")


class TestInitiativDrucksnrFromFundstellen:
    """Issue #35: the initiating Drucksache must be derivable from the raw
    Fundstellen (before stations exist) so enrichment can anchor section
    extraction for shared plenary protocols on it."""

    def test_returns_gesetzentwurf_drucksache(self):
        fundstellen = [
            {
                "raw": "Gesetzentwurf  Fraktion GRÜNE  04.02.2026 Drucksache 17/529",
                "station_typ": "Gesetzentwurf",
                "drucksache": "17/529",
            },
            {
                "raw": "Erste Beratung  Plenarprotokoll 17/12 29.09.2021",
                "station_typ": "Erste Beratung",
                "plenarprotokoll": "17/12",
            },
        ]
        assert _initiativ_drucksnr_from_fundstellen(fundstellen, "Fraktion GRÜNE") == "17/529"

    def test_skips_non_initiative_stations(self):
        """A leading plenary reading (no initiating document) must not be picked."""
        fundstellen = [
            {
                "raw": "Erste Beratung  Plenarprotokoll 17/12 29.09.2021",
                "station_typ": "Erste Beratung",
                "plenarprotokoll": "17/12",
            },
            {
                "raw": "Gesetzentwurf  Landesregierung  01.01.2026 Drucksache 17/1000",
                "station_typ": "Gesetzentwurf",
                "drucksache": "17/1000",
            },
        ]
        assert _initiativ_drucksnr_from_fundstellen(fundstellen, "Landesregierung") == "17/1000"

    def test_returns_none_when_no_initiative_document(self):
        fundstellen = [
            {
                "raw": "Erste Beratung  Plenarprotokoll 17/12 29.09.2021",
                "station_typ": "Erste Beratung",
                "plenarprotokoll": "17/12",
            },
        ]
        assert _initiativ_drucksnr_from_fundstellen(fundstellen, "Fraktion GRÜNE") is None

    def test_ignores_initiative_station_without_drucksache(self):
        fundstellen = [
            {"raw": "Gesetzentwurf  Fraktion GRÜNE  04.02.2026", "station_typ": "Gesetzentwurf", "drucksache": ""},
            {
                "raw": "Gesetzentwurf  Fraktion GRÜNE  05.02.2026 Drucksache 17/530",
                "station_typ": "Gesetzentwurf",
                "drucksache": "17/530",
            },
        ]
        assert _initiativ_drucksnr_from_fundstellen(fundstellen, "Fraktion GRÜNE") == "17/530"
