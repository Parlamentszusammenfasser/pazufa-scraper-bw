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


def _kraft(gbl: str, verkuendung: date) -> list[date]:
    text = (FIXTURES / "inkrafttreten" / f"GBl-{gbl}.txt").read_text()
    return parse_inkrafttreten(text, verkuendung)


class TestParseInkrafttretenIssue98:
    """GitHub issue #98 / DD-071: the dates the law's own Inkrafttreten clause names.

    Fixtures: the text before and at the signature of real WP17 laws, as extracted
    by ``bawue_dok.extract_pdf_text`` from the electronic Gesetzblatt.
    """

    def test_tag_nach_verkuendung(self):
        """GBl 2026 Nr. 18: "Artikel 3" without an "Inkrafttreten" heading."""
        assert _kraft("2026-18", date(2026, 2, 27)) == [date(2026, 2, 28)]

    def test_erster_tag_des_vierten_folgenden_monats(self):
        """GBl 2026 Nr. 14, LNRSchG (V-244420); the old law's "außer Kraft" date is ignored."""
        assert _kraft("2026-14", date(2026, 2, 27)) == [date(2026, 6, 1)]

    def test_uebernaechster_monat(self):
        assert _kraft("2025-71", date(2025, 7, 29)) == [date(2025, 9, 1)]

    def test_monate_nach_verkuendung(self):
        assert _kraft("2025-25", date(2025, 3, 28)) == [date(2025, 9, 28), date(2025, 6, 28)]

    def test_staggered_lists_every_date(self):
        assert _kraft("2026-21", date(2026, 2, 27)) == [date(2026, 3, 1), date(2028, 1, 1)]

    def test_retroactive_part(self):
        """GBl 2026 Nr. 17: "mit Wirkung vom 1. Juli 2017" for one Nummer only."""
        assert _kraft("2026-17", date(2026, 2, 27)) == [date(2026, 2, 28), date(2017, 7, 1)]

    def test_retroactive_law(self):
        assert _kraft("2025-11", date(2025, 2, 19)) == [date(2025, 1, 1)]

    def test_gleichzeitig_repeats_a_named_date(self):
        assert _kraft("2025-15", date(2025, 3, 3)) == [date(2025, 1, 1), date(2025, 3, 4)]

    def test_treaty_law_uses_its_own_clause(self):
        """GBl 2026 Nr. 81: the Staatsvertrag follows the signature and has its own
        Inkrafttreten; the law announces the treaty's date later."""
        assert _kraft("2026-81", date(2026, 10, 13)) == [date(2026, 10, 14)]

    @pytest.mark.parametrize(
        "gbl",
        [
            "2025-61",  # Artikel 2 "zeitgleich mit dem Reformstaatsvertrag"
            "2025-81",  # "sobald alle Länder … geändert haben"
        ],
    )
    def test_condition_without_date_is_not_guessed(self, gbl):
        assert _kraft(gbl, date(2025, 7, 1)) == []

    def test_no_clause(self):
        assert parse_inkrafttreten("Artikel 1\nÄnderung\nStuttgart, den 1. März 2026", date(2026, 3, 1)) == []
