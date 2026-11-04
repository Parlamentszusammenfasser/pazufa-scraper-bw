"""Tests for the BawueBeteiligungScraper."""

import asyncio
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_dok import LLMMetrics, _hash_cache
from bawue.beteiligung_client import BASE_URL
from bawue.beteiligung_parser import RawBeteiligungDetail, RawBeteiligungProcess, parse_process_detail
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
from tests.factories import make_scraper
from tests.unit.test_bawue_dok import SAMPLE_HASH, SAMPLE_PDF_HASHES

FIXTURES = Path(__file__).parent.parent / "fixtures" / "beteiligung"


def _make_scraper():
    """Create a minimal BawueBeteiligungScraper without full init."""
    from bawue.rate_limiter import AdaptiveRateLimiter

    scraper = make_scraper(BawueBeteiligungScraper)
    scraper._wahlperiode = 17
    scraper._raw_cache = {}
    scraper._details = {}
    scraper._fingerprints = {}
    scraper._fristen = {}
    scraper._client = MagicMock()
    scraper._client.fetch_last_modified.return_value = date(2025, 10, 16)
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
    phases: list[str] | None = None,
    comment_deadline: date | None = None,
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
        phases=phases,
        comment_deadline=comment_deadline,
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
        vorgang = await _make_scraper()._build_vorgang("entbuerokratisierung", _make_detail())

        assert vorgang.typ == Vorgangstyp.GG_LAND_PARL
        # Deterministic and per slug.
        assert str(vorgang.api_id) == str(uuid5(NAMESPACE_URL, "beteiligung-entbuerokratisierung"))
        assert [a.organisation for a in vorgang.initiatoren] == [
            "Ministerium des Inneren, für Digitalisierung und Kommunen"
        ]
        [station] = vorgang.stationen
        assert station.typ == Stationstyp.PREPARL_REGENT
        assert station.gremium.parlament == Parlament.BW
        assert station.gremium.name == "regierung"
        assert station.gremium.wahlperiode == 17
        assert [d.typ for d in station.dokumente] == [Doktyp.PREPARL_ENTWURF]

    @pytest.mark.asyncio
    async def test_issue24_url_is_a_backlink_not_a_vorgnr(self):
        """Pre-parliamentary drafts have no VNr; the URL as a `vorgnr` ident overflowed the website's id field."""
        vorgang = await _make_scraper()._build_vorgang("entbuerokratisierung", _make_detail())

        assert len(vorgang.links) == 1
        assert "beteiligungsportal" in str(vorgang.links[0])
        assert not vorgang.ids

    @pytest.mark.asyncio
    async def test_dates_are_timezone_aware(self):
        """Naive datetimes cause API 422 'premature end of input' errors."""
        vorgang = await _make_scraper()._build_vorgang("entbuerokratisierung", _make_detail())

        assert vorgang.stationen[0].zp_start == datetime(2025, 10, 15, tzinfo=UTC)
        doc = vorgang.stationen[0].dokumente[0]
        assert doc.zp_modifiziert.tzinfo is not None
        assert doc.zp_referenz.tzinfo is not None

    @pytest.mark.asyncio
    async def test_dokument_placeholders_when_llm_disabled(self):
        """Without LLM enrichment, volltext carries the TODO marker (never empty);
        hash_ is link-derived, not a shared marker (DD-048)."""
        vorgang = await _make_scraper()._build_vorgang("entbuerokratisierung", _make_detail())

        doc = vorgang.stationen[0].dokumente[0]
        assert doc.volltext == "TODO"
        assert doc.hash_ == placeholder_hash(doc.link)

    @pytest.mark.asyncio
    async def test_empty_ministry_yields_no_initiatoren(self):
        """Empty ministry: drop the Autor instead of sending an empty organisation."""
        scraper = _make_scraper()
        detail = _make_detail(ministry="")
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang.initiatoren == []
        assert vorgang.stationen[0].dokumente[0].autoren == []

    @pytest.mark.asyncio
    async def test_empty_detail_title_falls_back_to_todo_marker(self):
        """Empty detail title becomes TODO so the required Vorgang.titel is non-empty."""
        scraper = _make_scraper()
        detail = _make_detail(title="")
        vorgang = await scraper._build_vorgang("entbuerokratisierung", detail)

        assert vorgang.titel == "TODO"

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
    async def test_no_pdfs_returns_none(self):
        scraper = _make_scraper()
        detail = _make_detail(pdf_links=[])
        result = await scraper._build_vorgang("klima-register", detail)
        assert result is None
        assert scraper._skipped == 1


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
        scraper._client.fetch_process_detail.return_value = (FIXTURES / "effizienzgesetz_detail.html").read_text()

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
    async def test_gesetz_page_is_built(self):
        """effizienzgesetz is built in TestIssue45LinkListMarkup."""
        slug = "entbuerokratisierung"
        scraper = _make_scraper()
        scraper._raw_cache[slug] = _make_process(slug=slug)
        scraper._client.fetch_process_detail.return_value = (FIXTURES / f"{slug}_detail.html").read_text()

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


class TestIssue123ReleaseDate:
    """Issue #123 (DD-007, DD-074): dated by the draft's publication, not the comment deadline."""

    PDF_A = "https://beteiligungsportal.baden-wuerttemberg.de/fileadmin/260922_Entwurf.pdf"
    PDF_B = "https://beteiligungsportal.baden-wuerttemberg.de/fileadmin/Begruendung.pdf"

    @pytest.mark.asyncio
    async def test_haushaltsbegleitgesetz_2027_is_published(self):
        """A draft without a comment phase used to be skipped in every run."""
        html = (FIXTURES / "haushaltsbegleitgesetz_2027_detail.html").read_text()
        scraper = _make_scraper()
        scraper._wahlperiode = 18
        scraper._client.fetch_last_modified.return_value = date(2026, 10, 12)

        vorgang = await scraper._build_vorgang("haushaltsbegleitgesetz-2027", parse_process_detail(html, BASE_URL))

        [station] = vorgang.stationen
        assert station.typ == Stationstyp.PREPARL_REGENT
        assert station.zp_start == datetime(2026, 10, 12, tzinfo=UTC)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("last_modified", "expected"),
        [(date(2026, 6, 17), date(2026, 6, 17)), (None, date(2026, 6, 17)), (date(2026, 8, 3), date(2026, 6, 17))],
        ids=["upload-day", "no-header", "re-uploaded"],
    )
    async def test_long_page_full_of_dates(self, last_modified, expected):
        """A full, untrimmed page: 40+ dates in its text (deadline 29.07.2026, comments,
        effective dates) and the whole portal markup. It must yield the one draft PDF,
        dated by the PDF alone; page text never dates a draft (DD-074)."""
        html = (FIXTURES / "dienst_und_versorgungsbezuege_full.html").read_text()
        scraper = _make_scraper()
        scraper._wahlperiode = 18
        scraper._client.fetch_last_modified.return_value = last_modified

        vorgang = await scraper._build_vorgang("dienst-und-versorgungsbezuege", parse_process_detail(html, BASE_URL))

        [station] = vorgang.stationen
        assert station.zp_start == datetime.combine(expected, datetime.min.time(), UTC)
        assert [d.link for d in station.dokumente] == [
            f"{BASE_URL}/fileadmin/redaktion/beteiligungsportal/FM/260617_Anpassung-Dienst--und-Versorgungsbezuege.pdf"
        ]

    @pytest.mark.asyncio
    async def test_each_document_keeps_its_own_date(self):
        scraper = _make_scraper()
        scraper._client.fetch_last_modified.side_effect = {
            self.PDF_A: date(2026, 9, 22),
            self.PDF_B: date(2026, 9, 25),
        }.get
        detail = _make_detail(pdf_links=[{"title": "A", "url": self.PDF_A}, {"title": "B", "url": self.PDF_B}])

        vorgang = await scraper._build_vorgang("mietpreisbegrenzung", detail)

        [station] = vorgang.stationen
        assert station.zp_start == datetime(2026, 9, 22, tzinfo=UTC)
        assert [d.zp_referenz.date() for d in station.dokumente] == [date(2026, 9, 22), date(2026, 9, 25)]
        assert [d.zp_modifiziert for d in station.dokumente] == [d.zp_referenz for d in station.dokumente]

    @pytest.mark.asyncio
    async def test_page_without_draft_phase_is_skipped(self, caplog):
        """Strategy pages and Bürgerforen carry PDFs too, but no draft phase (DD-007)."""
        scraper = _make_scraper()
        with caplog.at_level(logging.INFO, logger="bawue.bawue_beteiligung_scraper"):
            result = await scraper._build_vorgang("datenstrategie", _make_detail(phases=[]))
        assert result is None
        assert scraper._skipped == 1
        assert [r.levelno for r in caplog.records] == [logging.INFO]
        scraper._client.fetch_last_modified.assert_not_called()

    @pytest.mark.asyncio
    async def test_pdf_without_any_date_skips_the_vorgang(self, caplog):
        scraper = _make_scraper()
        scraper._client.fetch_last_modified.return_value = None
        detail = _make_detail(pdf_links=[{"title": "B", "url": self.PDF_B}])
        with caplog.at_level(logging.WARNING, logger="bawue.bawue_beteiligung_scraper"):
            result = await scraper._build_vorgang("ohne-datum", detail)
        assert result is None
        assert scraper._skipped == 1
        assert "no publication date" in caplog.text


class TestIssue148Beteiligungsfrist:
    """Issue #148 (DD-075): the comment deadline is its own summary part, never text in an AI summary."""

    FRIST = Zusammenfassungstupel(typ="beteiligungsfrist-extern", inhalt="Beteiligungsfrist: 09.09.2026")
    KI: ClassVar[list[Zusammenfassungstupel]] = [
        Zusammenfassungstupel(typ="full-llm", inhalt="Berichtspflichten entfallen."),
        Zusammenfassungstupel(typ="kosten-llm", inhalt="Keine."),
    ]
    A, B = "https://example.com/a.pdf", "https://example.com/b.pdf"

    @staticmethod
    def _llm_scraper(monkeypatch, summaries: dict):
        """LLM enabled; enrichment gives the documents in *summaries* their (shared) summary list."""
        from bawue.bawue_dok import EnrichmentResult

        async def _fake_enrich(session, llm, dok, **kwargs):
            if dok.link in summaries:
                dok.zusammenfassung = summaries[dok.link]
            return EnrichmentResult(dokument=dok)

        monkeypatch.setattr("bawue.bawue_dok.enrich_dokument", _fake_enrich)
        monkeypatch.setattr("bawue.bawue_beteiligung_scraper.vorgang_kurztitel", AsyncMock(return_value="K"))
        monkeypatch.setattr("bawue.bawue_beteiligung_scraper.vorgang_ressort", AsyncMock(return_value=None))
        scraper = _make_scraper()
        scraper._llm_enabled = True
        scraper._llm = MagicMock()
        scraper._llm_model = "gpt-5-nano"
        return scraper

    @pytest.mark.asyncio
    async def test_without_llm_the_deadline_is_the_only_part(self):
        detail = _make_detail(comment_deadline=date(2026, 9, 9))
        vorgang = await _make_scraper()._build_vorgang("effizienzgesetz", detail)
        [dok] = vorgang.stationen[0].dokumente
        assert dok.zusammenfassung == [self.FRIST]

    @pytest.mark.asyncio
    async def test_switched_off_sends_no_deadline(self):
        """Kill switch `[bawue] beteiligungsfristen` (DD-076)."""
        scraper = _make_scraper()
        scraper._beteiligungsfristen = False
        vorgang = await scraper._build_vorgang("effizienzgesetz", _make_detail(comment_deadline=date(2026, 9, 9)))
        assert vorgang.stationen[0].dokumente[0].zusammenfassung is UNSET

    @pytest.mark.asyncio
    async def test_no_deadline_leaves_the_summary_alone(self):
        vorgang = await _make_scraper()._build_vorgang("haushaltsbegleitgesetz-2027", _make_detail())
        assert vorgang.stationen[0].dokumente[0].zusammenfassung is UNSET

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("summaries", "expected"),
        [
            # No AI summary anywhere: one document is enough, the website skips those without parts.
            # A deadline-only part on more would never be removed and could later hide a summary.
            ({}, [[FRIST], UNSET]),
            # Only documents with a summary: a deadline-only part would hide B's summary.
            ({B: KI}, [UNSET, [*KI, FRIST]]),
            ({A: KI, B: KI}, [[*KI, FRIST], [*KI, FRIST]]),
        ],
        ids=["no-summary", "second-has-summary", "both-have-summary"],
    )
    async def test_goes_with_the_summary_the_website_shows(self, monkeypatch, summaries, expected):
        """The website shows the first document with a summary in the backend's order, which
        sorts by link, not by portal order (review of PR #163): so every summary gets it."""
        scraper = self._llm_scraper(monkeypatch, summaries)
        detail = _make_detail(
            pdf_links=[{"title": "A (PDF)", "url": self.A}, {"title": "B (PDF)", "url": self.B}],
            comment_deadline=date(2026, 9, 9),
        )
        vorgang = await scraper._build_vorgang("effizienzgesetz", detail)
        assert [d.zusammenfassung for d in vorgang.stationen[0].dokumente] == expected
        assert len(self.KI) == 2  # the enrichment's list is not changed in place


class TestIssue148FristenRegister:
    """Issue #148 (DD-076): after an upload the deadline goes to the register the calendar reads."""

    @staticmethod
    async def _upload(scraper, slug: str, deadline: date | None, read: bool = False) -> dict:
        """Build as item_extractor does, store as the pipeline does after a successful upload."""
        scraper._raw_cache[slug] = _make_process(slug=slug)
        scraper._details[slug] = _make_detail(comment_deadline=deadline)
        vorgang = await scraper.item_extractor(slug)
        if read:
            dok = vorgang.stationen[0].dokumente[0]
            dok.volltext = "Volltext"
            # A Beteiligung PDF has no #page anchor, so a read one carries both digests (issue #41).
            dok.hash_ = SAMPLE_PDF_HASHES
        await scraper.store_extracted_result(slug, vorgang)
        return json.loads(scraper.config.cache.get_raw("beteiligungsfristen"))

    @staticmethod
    def _scraper():
        from tests.unit.test_bawue_scraper import _InMemoryCache

        scraper = _make_scraper()
        scraper.config.cache = _InMemoryCache()
        return scraper

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("read", "hash_"), [(True, SAMPLE_HASH), (False, None)], ids=["read", "unread"])
    async def test_registers_the_deadline_with_the_uploaded_document(self, read, hash_):
        register = await self._upload(self._scraper(), "entbuerokratisierung", date(2025, 11, 13), read)

        assert register == {
            "entbuerokratisierung": {
                "frist": "2025-11-13",
                "titel": "Entbürokratisierung",
                "portal": f"{BASE_URL}/de/mitmachen/lp-17/entbuerokratisierung",
                "pdf": _make_detail().pdf_links[0]["url"],
                # Only a read document: a placeholder row is replaced once the PDF is read (as DD-070).
                "hash": hash_,
            }
        }

    @pytest.mark.asyncio
    async def test_a_removed_deadline_leaves_the_register_and_the_others_stay(self):
        scraper = self._scraper()
        await self._upload(scraper, "a", date(2099, 12, 1))
        await self._upload(scraper, "b", date(2099, 12, 8))

        assert list(await self._upload(scraper, "a", None)) == ["b"]

    @pytest.mark.asyncio
    async def test_failed_upload_registers_nothing(self):
        """Only an uploaded draft has a row a TOP can link to (review of PR #164)."""
        from bawue.upload_throttle import UploadOutcome

        scraper = self._scraper()
        scraper.config.api_obj_log = None
        scraper.items_done, scraper.item_count = 0, 1
        scraper._fingerprints["a"] = "fingerprint"
        scraper._raw_cache["a"] = _make_process(slug="a")
        scraper._details["a"] = _make_detail(comment_deadline=date(2099, 12, 1))

        failed = UploadOutcome(None, "HTTP 500")
        with patch("bawue.bawue_beteiligung_scraper.upload_vorgang", return_value=failed) as upload:
            assert await scraper.helper_extract_send_item("a", asyncio.Semaphore(1)) is None

        upload.assert_called_once()
        assert scraper.config.cache.get_raw("beteiligungsfristen") is None
        assert scraper.config.cache.get_raw("vg2:a") is None

    @pytest.mark.asyncio
    async def test_failed_register_write_leaves_the_vorgang_uncached(self):
        """A cache hit would never write the register again (review of PR #164)."""
        scraper = self._scraper()
        cache = scraper.config.cache
        store_raw = cache.store_raw
        cache.store_raw = lambda key, value, typehint="": key != "beteiligungsfristen" and store_raw(key, value)
        scraper._fingerprints["a"] = "fingerprint"
        scraper._raw_cache["a"] = _make_process(slug="a")
        scraper._details["a"] = _make_detail(comment_deadline=date(2099, 12, 1))
        await scraper.store_extracted_result("a", await scraper.item_extractor("a"))

        assert cache.get_raw("vg2:a") is None


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
        assert set(scraper._raw_cache) == set(slugs)

    @pytest.mark.asyncio
    async def test_issue101_keeps_shared_hash_cache(self):
        """The PARLIS scraper may already have cached semantics this cycle; main() clears it."""
        _hash_cache["parlis-doc"] = {}
        try:
            with patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", return_value=[]):
                await _make_scraper().listing_page_extractor("lp-18")
            assert "parlis-doc" in _hash_cache
        finally:
            _hash_cache.clear()


class TestItemExtractor:
    @pytest.mark.asyncio
    async def test_consumes_cache(self):
        scraper = _make_scraper()
        process = _make_process()
        scraper._raw_cache["entbuerokratisierung"] = process
        detail = _make_detail()

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
        ):
            await scraper.run()

        lines = scraper.summary[1]
        assert "Found:       2  (new or retried 2, cached 0)" in lines
        assert "Published:   1" in lines
        assert "Skipped:     0" in lines
        assert "Failed:      1" in lines
        assert "  - timeout | Timeout-Gesetz | TimeoutError: portal timeout" in lines
        stored = [c.args[0] for c in scraper.config.cache.store_raw.call_args_list if c.args[0].startswith("vg2:")]
        assert stored == ["vg2:effizienzgesetz"]  # the crashed one is retried next cycle


class TestRunSummary:
    @pytest.mark.asyncio
    async def test_summary_still_printed_on_run_failure(self, capsys, caplog):
        scraper = _make_scraper()

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.run", new=mock_run),
            caplog.at_level(logging.INFO, logger="bawue.bawue_beteiligung_scraper"),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Beteiligung Run Summary ===" in captured.out
        duration_line = next(line for line in captured.out.splitlines() if "Duration" in line)
        assert "Duration: 0m 00s" in duration_line  # human readable, no raw seconds
        assert scraper.summary[0] == "Beteiligung"  # issue #52: the cycle report still gets it
        assert any("Completed in" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_summary_lists_failed_vorgaenge_with_reason(self, capsys):
        from bawue.api import BawueApiError

        scraper = _make_scraper()
        mock_config = MagicMock()
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
        assert scraper._failed == 1


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


class TestIssue104RefreshOnChange:
    """Issue #104: a cached consultation is rebuilt when the portal shows progress (DD-052)."""

    @staticmethod
    def _scraper_with_dict_cache():
        scraper = _make_scraper()
        store: dict[str, str] = {}
        scraper.config.cache.get_raw.side_effect = lambda key, *_: store.get(key)
        scraper.config.cache.store_raw.side_effect = lambda key, value, *_: store.__setitem__(key, value) or True
        return scraper, store

    @staticmethod
    async def _list(scraper, process, detail):
        scraper._client.fetch_process_list.return_value = [process]
        scraper._client.fetch_process_detail.return_value = "<html></html>"

        async def _to_thread(fn, *args):
            return fn(*args)

        with (
            patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", side_effect=_to_thread),
            patch("bawue.bawue_beteiligung_scraper.parse_process_detail", return_value=detail),
        ):
            return await scraper.listing_page_extractor("lp-18")

    async def _cycle(self, scraper, process, detail):
        """List, check the cache, and cache as the pipeline does after an upload."""
        [slug] = await self._list(scraper, process, detail)
        hit = await scraper.get_cached_result(slug) is not None
        if not hit:
            await scraper.store_extracted_result(slug, MagicMock())
        return hit

    @pytest.mark.asyncio
    async def test_unchanged_page_is_a_hit(self):
        scraper, store = self._scraper_with_dict_cache()
        assert not await self._cycle(scraper, _make_process(), _make_detail())
        assert await self._cycle(scraper, _make_process(), _make_detail())
        assert list(store) == ["vg2:entbuerokratisierung"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("process", "detail"),
        [
            (
                _make_process(),
                _make_detail(
                    pdf_links=[
                        {"title": "Entwurf (PDF)", "url": "https://beteiligungsportal.baden-wuerttemberg.de/a.pdf"},
                        {"title": "Begründung (PDF)", "url": "https://beteiligungsportal.baden-wuerttemberg.de/b.pdf"},
                    ]
                ),
            ),
            (_make_process(status="open"), _make_detail()),
            (_make_process(), _make_detail(title="Entbürokratisierung II")),
            (_make_process(), _make_detail(ministry="Staatsministerium")),
            (_make_process(), _make_detail(comment_deadline=date(2025, 11, 20))),
        ],
        ids=["added-pdf", "status", "title", "ministry", "deadline"],
    )
    async def test_progress_is_a_miss(self, process, detail):
        scraper, _ = self._scraper_with_dict_cache()
        await self._cycle(scraper, _make_process(), _make_detail())
        assert not await self._cycle(scraper, process, detail)

    @pytest.mark.asyncio
    async def test_item_extractor_reuses_the_listed_detail(self):
        scraper, _ = self._scraper_with_dict_cache()
        detail = _make_detail()
        [slug] = await self._list(scraper, _make_process(), detail)
        scraper._client.fetch_process_detail.reset_mock()

        with patch.object(scraper, "_build_vorgang", AsyncMock(return_value="built")) as build:
            assert await scraper.item_extractor(slug) == "built"
        scraper._client.fetch_process_detail.assert_not_called()
        build.assert_awaited_once_with(slug, detail)

    @pytest.mark.asyncio
    async def test_failed_detail_fetch_is_not_cached(self):
        """No fingerprint, no cache entry: item_extractor retries and counts the failure (#83)."""
        scraper, store = self._scraper_with_dict_cache()
        scraper._client.fetch_process_list.return_value = [_make_process()]
        scraper._client.fetch_process_detail.side_effect = TimeoutError("portal timeout")

        async def _to_thread(fn, *args):
            return fn(*args)

        with patch("bawue.bawue_beteiligung_scraper.asyncio.to_thread", side_effect=_to_thread):
            [slug] = await scraper.listing_page_extractor("lp-18")
        assert await scraper.get_cached_result(slug) is None
        await scraper.store_extracted_result(slug, MagicMock())
        assert store == {}
