"""Tests for the Gesetzblatt Baden-Württemberg HTML parser."""

from pathlib import Path

import pytest

from bawue.gesetzblatt_parser import (
    RawGesetzblattDetail,
    parse_detail,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gesetzblatt"
BASE_URL = "https://www.baden-wuerttemberg.de"


@pytest.fixture()
def detail_gesetz_html() -> str:
    return (FIXTURES / "detail_gesetz_2026-19.html").read_text()


@pytest.fixture()
def detail_verordnung_html() -> str:
    return (FIXTURES / "detail_verordnung_2026-48.html").read_text()


@pytest.fixture()
def detail_bekanntmachung_html() -> str:
    return (FIXTURES / "detail_bekanntmachung_2025-1.html").read_text()


class TestParseDetailGesetz:
    def test_returns_dataclass(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert isinstance(detail, RawGesetzblattDetail)

    def test_titel(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.titel == "Gesetz zur Änderung des Landesdatenschutzgesetzes und anderer Gesetze"

    def test_jahr_nummer(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.jahr == 2026
        assert detail.nummer == 19

    def test_publikationsdatum(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.publikationsdatum == "27.02.2026"

    def test_ausfertigungsdatum(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.ausfertigungsdatum == "10.02.2026"

    def test_typ_is_gesetz(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.typ == "Gesetz"

    def test_federfuehrung(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.federfuehrung == "Innenministerium (IM)"

    def test_pdf_url_present(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.pdf_url is not None
        assert "eID=dumpFile" in detail.pdf_url
        assert detail.pdf_url.startswith("https://www.baden-wuerttemberg.de")

    def test_pdf_filename(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert detail.pdf_filename == "GBl._2026_Nr._19_vom_27.02.2026_signed.pdf"


class TestParseDetailVerordnung:
    def test_typ_is_verordnung(self, detail_verordnung_html):
        detail = parse_detail(detail_verordnung_html, BASE_URL)
        assert detail.typ == "Verordnung"

    def test_titel_contains_verordnung(self, detail_verordnung_html):
        detail = parse_detail(detail_verordnung_html, BASE_URL)
        assert "Verordnung" in detail.titel


class TestParseDetailMissingFields:
    """Degraded pages must yield the documented empty/None defaults, because the
    scraper's skip logic depends on them (empty pdf_url / publikationsdatum → skip).
    A parser change that started raising or returning wrong defaults here would
    silently break those skips."""

    def test_empty_document_yields_defaults(self):
        detail = parse_detail("<html><body></body></html>", BASE_URL)
        assert detail.titel == ""
        assert detail.jahr == 0
        assert detail.nummer == 0
        assert detail.publikationsdatum == ""
        assert detail.ausfertigungsdatum is None
        assert detail.typ == ""
        assert detail.federfuehrung is None
        assert detail.pdf_url is None
        assert detail.pdf_filename is None

    def test_missing_pdf_link_yields_none(self):
        html = '<html><body><div class="tx-rsmbwlawsheet"><h1>Ein Gesetz</h1></div></body></html>'
        detail = parse_detail(html, BASE_URL)
        assert detail.titel == "Ein Gesetz"
        assert detail.pdf_url is None
        assert detail.pdf_filename is None


class TestParseDetailPdfLink:
    """PDF-link extraction branches not exercised by the real (absolute-href) fixtures."""

    def test_relative_href_is_resolved_to_absolute(self):
        html = '<html><body><a href="/index.php?eID=dumpFile&t=r&fn=GBl.pdf">PDF</a></body></html>'
        detail = parse_detail(html, BASE_URL)
        assert detail.pdf_url == "https://www.baden-wuerttemberg.de/index.php?eID=dumpFile&t=r&fn=GBl.pdf"
        assert detail.pdf_filename == "GBl.pdf"

    def test_pdf_filename_none_when_fn_query_absent(self):
        html = (
            '<html><body><a href="https://www.baden-wuerttemberg.de/index.php?eID=dumpFile&t=r">PDF</a></body></html>'
        )
        detail = parse_detail(html, BASE_URL)
        assert detail.pdf_url is not None
        assert "eID=dumpFile" in detail.pdf_url
        assert detail.pdf_filename is None


class TestParseDetailBekanntmachung:
    def test_typ_is_bekanntmachung(self, detail_bekanntmachung_html):
        detail = parse_detail(detail_bekanntmachung_html, BASE_URL)
        assert detail.typ == "Bekanntmachung"

    def test_jahr_nummer(self, detail_bekanntmachung_html):
        detail = parse_detail(detail_bekanntmachung_html, BASE_URL)
        assert detail.jahr == 2025
        assert detail.nummer == 1

    def test_federfuehrung_staatsministerium(self, detail_bekanntmachung_html):
        detail = parse_detail(detail_bekanntmachung_html, BASE_URL)
        assert detail.federfuehrung == "Staatsministerium (StM)"
