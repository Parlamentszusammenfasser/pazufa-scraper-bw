"""Tests for the Beteiligungsportal HTML parser."""

from pathlib import Path

import pytest

from bawue.beteiligung_parser import (
    RawBeteiligungDetail,
    RawBeteiligungProcess,
    parse_process_detail,
    parse_process_list,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "beteiligung"
BASE_URL = "https://beteiligungsportal.baden-wuerttemberg.de"


@pytest.fixture()
def lp17_index_html():
    return (FIXTURES / "lp17_index.html").read_text()


@pytest.fixture()
def entbuerokratisierung_html():
    return (FIXTURES / "entbuerokratisierung_detail.html").read_text()


@pytest.fixture()
def rettungsdienstplanverordnung_html():
    return (FIXTURES / "rettungsdienstplanverordnung_detail.html").read_text()


@pytest.fixture()
def klima_register_html():
    return (FIXTURES / "klima_register_detail.html").read_text()


class TestParseProcessList:
    def test_extracts_all_entries(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        assert len(processes) == 4

    def test_extracts_titles(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        titles = [p.title for p in processes]
        assert "Klima-Maßnahmen-Register 2026" in titles
        assert "Entbürokratisierung" in titles
        assert "Regulierung des Mietmarkts" in titles

    def test_extracts_urls(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        urls = [p.url for p in processes]
        assert "/de/mitmachen/lp-17/entbuerokratisierung" in urls
        assert "/de/mitmachen/lp-17/rettungsdienstplanverordnung" in urls

    def test_extracts_status_open(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        klima = next(p for p in processes if "Klima" in p.title)
        assert klima.status == "open"

    def test_extracts_status_closed(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        entbuero = next(p for p in processes if "Entbürokratisierung" in p.title)
        assert entbuero.status == "closed"

    def test_returns_dataclass_instances(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        assert all(isinstance(p, RawBeteiligungProcess) for p in processes)

    def test_extracts_slug_from_url(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        entbuero = next(p for p in processes if "Entbürokratisierung" in p.title)
        assert entbuero.slug == "entbuerokratisierung"

    def test_soft_hyphen_removed_from_title(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        rettung = next(p for p in processes if "Rettungsdienst" in p.title)
        assert "\xad" not in rettung.title
        assert "Rettungsdienstplanverordnung" in rettung.title


class TestParseProcessDetail:
    def test_extracts_title(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        assert detail.title == "Entbürokratisierung"

    def test_extracts_ministry(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        assert detail.ministry == "Ministerium des Inneren, für Digitalisierung und Kommunen"

    def test_extracts_pdf_links(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        assert len(detail.pdf_links) == 1
        pdf = detail.pdf_links[0]
        expected_title = (
            "Zweites Gesetz zum Abbau verzichtbarer Formerfordernisse"
            " und zur Änderung weiterer Vorschriften (PDF)"
        )
        assert pdf["title"] == expected_title
        assert pdf["url"].endswith(".pdf")
        assert pdf["url"].startswith("https://")

    def test_extracts_comment_deadline(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        assert detail.comment_deadline == "13.11.2025"

    def test_extracts_phases(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        assert detail.phases == [
            "Online-Kommentierung",
            "Antwort des Ministeriums",
            "Beratung und Beschluss",
            "Geltendes Gesetz",
        ]

    def test_returns_dataclass_instance(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        assert isinstance(detail, RawBeteiligungDetail)

    def test_rettungsdienstplanverordnung_extracts_pdf(self, rettungsdienstplanverordnung_html):
        detail = parse_process_detail(rettungsdienstplanverordnung_html, BASE_URL)
        assert len(detail.pdf_links) == 1
        assert "Rettungsdienstplanverordnung" in detail.pdf_links[0]["title"]

    def test_rettungsdienstplanverordnung_comment_deadline(self, rettungsdienstplanverordnung_html):
        detail = parse_process_detail(rettungsdienstplanverordnung_html, BASE_URL)
        assert detail.comment_deadline == "29.11.2025"

    def test_no_pdfs_on_non_legislative_page(self, klima_register_html):
        detail = parse_process_detail(klima_register_html, BASE_URL)
        assert detail.pdf_links == []

    def test_ministry_on_non_legislative_page(self, klima_register_html):
        detail = parse_process_detail(klima_register_html, BASE_URL)
        assert detail.ministry == "Ministerium für Umwelt, Klima und Energiewirtschaft"

    def test_no_comment_deadline_when_open(self, klima_register_html):
        detail = parse_process_detail(klima_register_html, BASE_URL)
        assert detail.comment_deadline is None

    def test_pdf_url_is_absolute(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        for pdf in detail.pdf_links:
            assert pdf["url"].startswith("https://")

    def test_title_strips_soft_hyphens(self, rettungsdienstplanverordnung_html):
        detail = parse_process_detail(rettungsdienstplanverordnung_html, BASE_URL)
        assert "\xad" not in detail.title
