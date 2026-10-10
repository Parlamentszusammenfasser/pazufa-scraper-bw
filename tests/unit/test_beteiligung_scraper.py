"""Tests for the BawueBeteiligungScraper."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_dok import LLMMetrics
from bawue.beteiligung_parser import RawBeteiligungDetail, RawBeteiligungProcess
from bawue.types import (
    UNSET,
    Doktyp,
    Parlament,
    Ressort,
    Stationstyp,
    Vorgangstyp,
    Zusammenfassungstupel,
    placeholder_hash,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "beteiligung"


def _make_scraper():
    """Create a minimal BawueBeteiligungScraper without full init."""
    from bawue.rate_limiter import AdaptiveRateLimiter

    scraper = object.__new__(BawueBeteiligungScraper)
    scraper._wahlperiode = 17
    scraper._raw_cache = {}
    scraper._client = MagicMock()
    scraper._api_client = MagicMock()
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
        assert vorgang.stationen[0].typ == Stationstyp.PREPARL_REGENT

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
        doc = station.dokumente[0]
        assert doc.typ == Doktyp.PREPARL_ENTWURF

    @pytest.mark.asyncio
    async def test_ministry_as_initiator(self):
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert len(vorgang.initiatoren) == 1
        assert vorgang.initiatoren[0].organisation == "Ministerium des Inneren, für Digitalisierung und Kommunen"

    @pytest.mark.asyncio
    async def test_empty_ministry_yields_no_initiatoren(self):
        """Empty ministry: drop the Autor instead of sending an empty organisation."""
        scraper = _make_scraper()
        detail = _make_detail(ministry="")
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang.initiatoren == []
        assert vorgang.stationen[0].dokumente[0].autoren == []

    @pytest.mark.asyncio
    async def test_dokument_placeholders_when_llm_disabled(self):
        """Without LLM enrichment, volltext carries the TODO marker (never empty)."""
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        dok = vorgang.stationen[0].dokumente[0]
        assert dok.volltext == "TODO"
        # hash_ is link-derived, not a shared marker (DD-048).
        assert dok.hash_ == placeholder_hash(dok.link)

    @pytest.mark.asyncio
    async def test_empty_detail_title_falls_back_to_todo_marker(self):
        """Empty detail title becomes TODO so the required Vorgang.titel is non-empty."""
        scraper = _make_scraper()
        detail = _make_detail(title="")
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang.titel == "TODO"

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
        assert vorgang.typ == Vorgangstyp.GG_LAND_PARL

    @pytest.mark.asyncio
    async def test_kurztitel_without_llm_is_titel_not_slug(self):
        """GitHub issue #32: the portal slug ("dienst-und-versorgungsbezuege") is not a title."""
        scraper = _make_scraper()
        detail = _make_detail(title="Dienst- und Versorgungsbezüge")
        vorgang = await scraper._build_vorgang("dienst-und-versorgungsbezuege", detail)
        assert vorgang.kurztitel == "Dienst- und Versorgungsbezüge"

    @pytest.mark.asyncio
    async def test_kurztitel_generated_from_titel_and_document_summary(self, monkeypatch):
        """GitHub issue #32: with LLM, the kurztitel is generated (DD-053)."""
        from bawue.bawue_dok import EnrichmentResult

        async def _fake_enrich(session, llm, dok, **kwargs):
            dok.zusammenfassung = [Zusammenfassungstupel(typ="full-llm", inhalt="Besoldung steigt 2026 bis 2028.")]
            return EnrichmentResult(dokument=dok)

        monkeypatch.setattr("bawue.bawue_dok.enrich_dokument", _fake_enrich)
        scraper = _make_scraper()
        scraper._llm_enabled = True
        scraper._llm = MagicMock()
        scraper._llm_model = "gpt-5-nano"
        scraper.config.cache.get_raw.return_value = None
        llm_reply = MagicMock()
        llm_reply.choices = [MagicMock()]
        llm_reply.choices[0].message.content = '{"kurztitel": "Höhere Beamtenbesoldung 2026 bis 2028"}'

        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, return_value=llm_reply) as acomp:
            vorgang = await scraper._build_vorgang(
                "dienst-und-versorgungsbezuege", _make_detail(title="Dienst- und Versorgungsbezüge")
            )

        assert vorgang.kurztitel == "Höhere Beamtenbesoldung 2026 bis 2028"
        # Plain text, not the tuple list: keeps the kurztitel cache key unchanged (issue #42).
        assert acomp.call_args.kwargs["messages"][-1]["content"].endswith(
            "Zusammenfassung: Besoldung steigt 2026 bis 2028."
        )

    @pytest.mark.asyncio
    async def test_links_contain_beteiligung_url(self):
        # Issue #24: the source URL is a backlink, not a Vorgangsnummer.
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)
        assert vorgang.links is not None
        assert len(vorgang.links) == 1
        assert "beteiligungsportal" in str(vorgang.links[0])

    @pytest.mark.asyncio
    async def test_url_not_set_as_vorgnr_ident(self):
        # Issue #24: pre-parliamentary drafts have no VNr; the URL must not be
        # emitted as a `vorgnr` ident (it overflowed the website's id field).
        scraper = _make_scraper()
        detail = _make_detail()
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)
        assert not vorgang.ids

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

        doc = vorgang.stationen[0].dokumente[0]
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
    from bawue.types import Autor, Dokument

    return Dokument(
        titel="Enriched",
        volltext="text",
        hash_="abc",
        typ=Doktyp.PREPARL_ENTWURF,
        zp_modifiziert=datetime(2025, 11, 13, tzinfo=UTC),
        zp_referenz=datetime(2025, 11, 13, tzinfo=UTC),
        link=url,
        autoren=[Autor(organisation="Test")],
    )


class TestIssue45LinkListMarkup:
    @pytest.mark.asyncio
    async def test_effizienzgesetz_builds_vorgang(self):
        scraper = _make_scraper()
        scraper._raw_cache["effizienzgesetz"] = _make_process(slug="effizienzgesetz")
        page = (FIXTURES / "effizienzgesetz_detail.html").read_text()

        with patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=page):
            vorgang = await scraper.item_extractor("effizienzgesetz")

        assert vorgang is not None
        station = vorgang.stationen[0]
        assert station.typ == Stationstyp.PREPARL_REGENT
        assert len(station.dokumente) == 1
        assert station.dokumente[0].typ == Doktyp.PREPARL_ENTWURF

    @pytest.mark.asyncio
    async def test_esf_skipped_without_error_log(self, caplog):
        scraper = _make_scraper()
        scraper._raw_cache["esf-foerderperiode-2028-2034"] = _make_process(slug="esf-foerderperiode-2028-2034")
        page = (FIXTURES / "esf_foerderperiode_detail.html").read_text()

        with (
            patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=page),
            caplog.at_level(logging.INFO, logger="bawue.bawue_beteiligung_scraper"),
        ):
            vorgang = await scraper.item_extractor("esf-foerderperiode-2028-2034")

        assert vorgang is None
        assert scraper._skipped == 1
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestIssue71Verordnungen:
    """Issue #71: a Rechtsverordnung never reaches the Landtag, so it is no gg-land-parl Vorgang."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slug", ["mietpreisbegrenzung", "rettungsdienstplanverordnung"])
    async def test_verordnung_page_is_skipped(self, slug, caplog):
        scraper = _make_scraper()
        scraper._raw_cache[slug] = _make_process(slug=slug)
        page = (FIXTURES / f"{slug}_detail.html").read_text()

        with (
            patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=page),
            caplog.at_level(logging.INFO, logger="bawue.bawue_beteiligung_scraper"),
        ):
            vorgang = await scraper.item_extractor(slug)

        assert vorgang is None
        assert scraper._skipped == 1
        assert any("Verordnung" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slug", ["effizienzgesetz", "entbuerokratisierung"])
    async def test_gesetz_page_is_built(self, slug):
        scraper = _make_scraper()
        scraper._raw_cache[slug] = _make_process(slug=slug)
        page = (FIXTURES / f"{slug}_detail.html").read_text()

        with patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=page):
            vorgang = await scraper.item_extractor(slug)

        assert vorgang is not None
        assert vorgang.typ == Vorgangstyp.GG_LAND_PARL

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("last_phase", "skipped"),
        [
            ("Beschluss der geltenden Verordnungen", True),
            ("Beschluss der geltenden Verordnung", True),
            ("Geltendes Gesetz", False),
        ],
    )
    async def test_phase_timeline_decides_not_pdf_titles(self, last_phase, skipped):
        """The PDF title is no signal: only the portal's phases tell Verordnung from Gesetz."""
        scraper = _make_scraper()
        pdf_links = [
            {"title": "Verordnungsentwurf (PDF)", "url": "https://beteiligungsportal.baden-wuerttemberg.de/a.pdf"}
        ]
        detail = _make_detail(pdf_links=pdf_links, phases=["Online-Kommentierung", last_phase])

        vorgang = await scraper._build_vorgang("x", detail)

        assert (vorgang is None) is skipped
        assert scraper._skipped == int(skipped)


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
        assert scraper._skipped == 1  # Found counts it, so the summary must too (issue #83)


class TestIssue52RunReport:
    """Issue #52: Found counts cached processes too, and the summary goes into the
    cycle's single Mattermost message instead of its own."""

    @pytest.mark.asyncio
    async def test_found_includes_cached_processes(self):
        scraper = _make_scraper()
        scraper.cached_count = 3
        scraper.item_count = 1

        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=AsyncMock()),
            patch("bawue.notifications.load_toml_section", return_value={"mattermost-hook": "https://hook.example"}),
            patch("bawue.notifications.requests.post") as post,
        ):
            await scraper.run()

        post.assert_not_called()
        title, lines = scraper.summary
        assert title == "Beteiligung"
        assert "Found:       4  (new or retried 1, cached 3)" in lines


class TestIssue83SummaryAddsUp:
    """Issue #83: Found = cached + Published + Skipped + Failed, also when a detail fetch crashes."""

    @pytest.mark.asyncio
    async def test_crashed_detail_fetch_counts_as_failed(self):
        from bawue.upload_throttle import UploadOutcome

        scraper = _make_scraper()
        scraper.listing_urls = ["lp-18"]
        scraper.config.linearize = True
        scraper.config.max_concurrency = 1
        scraper.config.api_obj_log = None
        scraper.config.cache.get_raw.return_value = None
        scraper.items_done = 0
        scraper._client.fetch_process_list.return_value = [
            _make_process(slug="effizienzgesetz"),
            _make_process(slug="timeout", title="Timeout-Gesetz"),
        ]
        page = (FIXTURES / "effizienzgesetz_detail.html").read_text()

        def _detail(url):
            if url.endswith("/timeout"):
                raise TimeoutError("portal timeout")
            return page

        scraper._client.fetch_process_detail.side_effect = _detail

        async def _to_thread(fn, *args):
            return fn(*args)

        with (
            patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", side_effect=_to_thread),
            patch("bawue.bawue_beteiligung_scraper.upload_vorgang", side_effect=lambda *a, **_: UploadOutcome(a[3])),
            patch("bawue.notifications.load_toml_section", return_value={}),
        ):
            await scraper.run()

        lines = scraper.summary[1]
        assert "Found:       2  (new or retried 2, cached 0)" in lines
        assert "Published:   1" in lines
        assert "Failed:      1" in lines
        assert "  - timeout | Timeout-Gesetz | TimeoutError: portal timeout" in lines


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

        with patch("bawue.upload_throttle.put_vorgang"):
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
        from bawue.api import BawueApiError

        scraper = _make_scraper()
        mock_config = MagicMock()
        mock_config.dry_run = False
        scraper.config = mock_config
        scraper.scraper_id = "test-scraper-id"

        with patch(
            "bawue.upload_throttle.put_vorgang",
            side_effect=BawueApiError(500, b"Internal Server Error", "vorgang_put"),
        ):
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
        assert scraper.summary[0] == "Beteiligung"  # issue #52: the cycle report still gets it

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
        from bawue.api import BawueApiError

        scraper = _make_scraper()
        mock_config = MagicMock()
        mock_config.dry_run = False
        scraper.config = mock_config
        scraper.scraper_id = "test-scraper-id"

        item = MagicMock()
        item.api_id = "deadbeef"
        item.kurztitel = "klima-slug"
        item.titel = "Klimaschutzgesetz"

        with patch(
            "bawue.upload_throttle.put_vorgang",
            side_effect=BawueApiError(422, b"Unprocessable Entity", "vorgang_put"),
        ):
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
    @pytest.mark.parametrize(
        ("toml", "wp", "warns"),
        [
            (None, 18, False),
            ("[bawue]\nwahlperiode = 16\n", 16, False),
            ("[bawue]\nwahlperiode = 16\n\n[beteiligung]\nwahlperiode = 17\n", 16, True),
        ],
    )
    def test_issue6_wahlperiode_comes_from_bawue_section(self, tmp_path, caplog, toml, wp, warns):
        """One Wahlperiode for all scrapers; a leftover [beteiligung] key is ignored, loudly."""
        mock_config = MagicMock()
        mock_config.config_file = None
        mock_config.collector_id = "00000000-0000-0000-0000-000000000001"
        mock_config.llm_provider_key = None
        if toml is not None:
            config_file = tmp_path / "config.toml"
            config_file.write_text(toml)
            mock_config.config_file = str(config_file)

        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.__init__", return_value=None),
            patch("bawue.bawue_beteiligung_scraper.BeteiligungClient"),
            caplog.at_level(logging.WARNING, logger="bawue.bawue_beteiligung_scraper"),
        ):
            scraper = BawueBeteiligungScraper(mock_config, MagicMock())

        assert scraper._wahlperiode == wp
        assert ("[beteiligung] wahlperiode is ignored" in caplog.text) is warns


class TestIssue39Ressort:
    """GitHub issue #39 (DD-055): `ressort` is the LLM's classification of the subject
    matter, not a lookup of the federführende ministry."""

    @pytest.mark.asyncio
    async def test_no_llm_leaves_ressort_unset(self):
        scraper = _make_scraper()

        vorgang = await scraper._build_vorgang("ohne-llm", _make_detail())

        assert vorgang.ressort is UNSET
        assert "ressort" not in vorgang.to_dict()

    @pytest.mark.asyncio
    async def test_classified_ressort_reaches_the_vorgang(self, monkeypatch):
        """The classifier gets the page title and the draft's summary — not the slug —
        and both per-Vorgang calls count into the run's metrics (issue #56)."""
        from bawue.bawue_dok import EnrichmentResult

        async def _fake_enrich(session, llm, dok, **kwargs):
            dok.zusammenfassung = [Zusammenfassungstupel(typ="full-llm", inhalt="Gerichte werden digital.")]
            return EnrichmentResult(dokument=dok)

        monkeypatch.setattr("bawue.bawue_dok.enrich_dokument", _fake_enrich)
        ressort = AsyncMock(return_value=Ressort.JUSTIZ)
        kurztitel = AsyncMock(return_value="Kurz")
        monkeypatch.setattr("bawue.bawue_beteiligung_scraper.vorgang_ressort", ressort)
        monkeypatch.setattr("bawue.bawue_beteiligung_scraper.vorgang_kurztitel", kurztitel)
        scraper = _make_scraper()
        scraper._llm_enabled = True
        scraper._llm = MagicMock()
        scraper._llm_model = "gpt-5-nano"

        vorgang = await scraper._build_vorgang("justizgesetz", _make_detail(title="Justizdigitalisierungsgesetz"))

        assert vorgang.ressort == Ressort.JUSTIZ
        assert vorgang.to_dict()["ressort"] == "Justiz"
        ressort.assert_awaited_once_with(
            scraper._llm,
            "Justizdigitalisierungsgesetz",
            "Gerichte werden digital.",
            model="gpt-5-nano",
            cache=scraper.config.cache,
            metrics=scraper._llm_metrics,
        )
        assert kurztitel.call_args.kwargs["metrics"] is scraper._llm_metrics

    @pytest.mark.asyncio
    async def test_unclassified_vorgang_omits_the_field(self, monkeypatch):
        """LLM on, but nothing fits: the key must be absent, not `null`."""
        scraper = _make_scraper()
        scraper._llm_enabled = True
        scraper._llm = MagicMock()
        scraper._llm_model = "gpt-5-nano"
        monkeypatch.setattr(
            "bawue.bawue_beteiligung_scraper.vorgang_ressort",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "bawue.bawue_beteiligung_scraper.vorgang_kurztitel",
            AsyncMock(return_value="Kurz"),
        )

        vorgang = await scraper._build_vorgang("ohne-ressort", _make_detail())

        assert vorgang.ressort is UNSET
        assert "ressort" not in vorgang.to_dict()
