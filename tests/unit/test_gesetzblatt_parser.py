"""Tests for the Gesetzblatt Baden-Württemberg HTML parser."""

from datetime import date
from pathlib import Path

import pytest

from bawue.gesetzblatt_parser import (
    RawGesetzblattDetail,
    parse_detail,
    parse_inkrafttreten,
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
    def test_parses_every_field(self, detail_gesetz_html):
        detail = parse_detail(detail_gesetz_html, BASE_URL)
        assert isinstance(detail, RawGesetzblattDetail)
        assert detail.titel == "Gesetz zur Änderung des Landesdatenschutzgesetzes und anderer Gesetze"
        assert (detail.jahr, detail.nummer) == (2026, 19)
        assert detail.publikationsdatum == "27.02.2026"
        assert detail.ausfertigungsdatum == "10.02.2026"
        assert detail.typ == "Gesetz"
        assert detail.federfuehrung == "Innenministerium (IM)"
        assert detail.pdf_url.startswith("https://www.baden-wuerttemberg.de")
        assert "eID=dumpFile" in detail.pdf_url
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


def _kraft(gbl: str, verkuendung: date) -> list[date]:
    text = (FIXTURES / "inkrafttreten" / f"GBl-{gbl}.txt").read_text()
    return parse_inkrafttreten(text, verkuendung)


class TestParseInkrafttretenIssue98:
    """Issue #98 / DD-071. Fixtures: text of real WP17 laws up to their signature."""

    @pytest.mark.parametrize(
        ("gbl", "verkuendung", "dates"),
        [
            # LNRSchG (V-244420); the old law's "außer Kraft" date is ignored.
            pytest.param("2026-14", date(2026, 2, 27), [date(2026, 6, 1)], id="vierter-folgender-monat"),
            pytest.param("2025-71", date(2025, 7, 29), [date(2025, 9, 1)], id="uebernaechster-monat"),
            pytest.param(
                "2025-25", date(2025, 3, 28), [date(2025, 9, 28), date(2025, 6, 28)], id="monate-nach-verkuendung"
            ),
            pytest.param("2026-21", date(2026, 2, 27), [date(2026, 3, 1), date(2028, 1, 1)], id="staggered"),
            # "mit Wirkung vom 1. Juli 2017" for one Nummer only.
            pytest.param("2026-17", date(2026, 2, 27), [date(2026, 2, 28), date(2017, 7, 1)], id="retroactive-part"),
            pytest.param("2025-15", date(2025, 3, 3), [date(2025, 1, 1), date(2025, 3, 4)], id="gleichzeitig"),
            # The Staatsvertrag follows the signature with its own Inkrafttreten; the law announces it later.
            pytest.param("2026-81", date(2026, 10, 13), [date(2026, 10, 14)], id="treaty-own-clause"),
            # "sobald alle Länder … geändert haben": a condition without a date is not guessed.
            pytest.param("2025-81", date(2025, 7, 1), [], id="condition-without-date"),
        ],
    )
    def test_real_laws(self, gbl, verkuendung, dates):
        assert _kraft(gbl, verkuendung) == dates

    @pytest.mark.parametrize(
        "clause",
        [
            # GBl 2025 Nr. 81 without its paragraph (2): the date belongs to a "Fassung"
            "Artikel 7\nDie Artikel 1 und 3 treten in Kraft, sobald alle Länder die Verträge in der am "
            "1. Januar 2025 geltenden Fassung geändert haben. Artikel 2 tritt am 1. Januar 2027 in Kraft.\n",
            "Artikel 5\n(2) Artikel 3 ist erstmals am 1. Januar 2027 anzuwenden. (3) Artikel 4 tritt in Kraft, "
            "sobald der Bund zustimmt.\n",
        ],
    )
    def test_date_of_another_sentence_does_not_count(self, clause):
        """Review of PR #125: an unrelated date hid a condition and dated the station."""
        assert parse_inkrafttreten(clause, date(2025, 7, 1)) == []

    def test_no_clause(self):
        assert parse_inkrafttreten("Artikel 1\nÄnderung\nStuttgart, den 1. März 2026", date(2026, 3, 1)) == []
