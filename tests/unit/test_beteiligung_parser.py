"""Tests for the Beteiligungsportal HTML parser."""

from datetime import date
from pathlib import Path

import pytest

from bawue.beteiligung_parser import (
    RawBeteiligungDetail,
    RawBeteiligungProcess,
    parse_process_detail,
    parse_process_list,
    release_date,
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


@pytest.fixture()
def effizienzgesetz_html():
    return (FIXTURES / "effizienzgesetz_detail.html").read_text()


@pytest.fixture()
def esf_foerderperiode_html():
    return (FIXTURES / "esf_foerderperiode_detail.html").read_text()


class TestParseProcessList:
    def test_parses_every_entry(self, lp17_index_html):
        processes = parse_process_list(lp17_index_html)
        assert len(processes) == 4
        assert all(isinstance(p, RawBeteiligungProcess) for p in processes)
        by_title = {p.title: p for p in processes}
        assert {"Klima-Maßnahmen-Register 2026", "Entbürokratisierung", "Regulierung des Mietmarkts"} <= by_title.keys()
        urls = [p.url for p in processes]
        assert "/de/mitmachen/lp-17/entbuerokratisierung" in urls
        assert "/de/mitmachen/lp-17/rettungsdienstplanverordnung" in urls
        assert by_title["Klima-Maßnahmen-Register 2026"].status == "open"
        assert by_title["Entbürokratisierung"].status == "closed"
        assert by_title["Entbürokratisierung"].slug == "entbuerokratisierung"
        rettung = next(p for p in processes if "Rettungsdienst" in p.title)
        assert "\xad" not in rettung.title  # soft hyphen removed
        assert "Rettungsdienstplanverordnung" in rettung.title


class TestParseProcessDetail:
    def test_legislative_page(self, entbuerokratisierung_html):
        detail = parse_process_detail(entbuerokratisierung_html, BASE_URL)
        assert isinstance(detail, RawBeteiligungDetail)
        assert detail.title == "Entbürokratisierung"
        assert detail.ministry == "Ministerium des Inneren, für Digitalisierung und Kommunen"
        assert detail.phases == [
            "Online-Kommentierung",
            "Antwort des Ministeriums",
            "Beratung und Beschluss",
            "Geltendes Gesetz",
        ]
        [pdf] = detail.pdf_links
        assert pdf["title"] == (
            "Zweites Gesetz zum Abbau verzichtbarer Formerfordernisse und zur Änderung weiterer Vorschriften (PDF)"
        )
        assert pdf["url"].startswith("https://")  # absolute
        assert pdf["url"].endswith(".pdf")

    def test_rettungsdienstplanverordnung(self, rettungsdienstplanverordnung_html):
        detail = parse_process_detail(rettungsdienstplanverordnung_html, BASE_URL)
        assert "\xad" not in detail.title  # soft hyphens stripped
        assert len(detail.pdf_links) == 1
        assert "Rettungsdienstplanverordnung" in detail.pdf_links[0]["title"]

    def test_non_legislative_page(self, klima_register_html):
        detail = parse_process_detail(klima_register_html, BASE_URL)
        assert detail.title == "Klima-Maßnahmen-Register 2026"  # from the article template
        assert detail.ministry == "Ministerium für Umwelt, Klima und Energiewirtschaft"
        assert detail.pdf_links == []


class TestIssue45LinkListMarkup:
    def test_effizienzgesetz_extracts_link_list_pdf(self, effizienzgesetz_html):
        detail = parse_process_detail(effizienzgesetz_html, BASE_URL)
        assert detail.pdf_links == [
            {
                "title": "Gesetz zur Abschaffung von Berichts-, Dokumentations- und "
                "Aufbewahrungspflichten (Effizienzgesetz) (PDF)",
                "url": f"{BASE_URL}/fileadmin/redaktion/beteiligungsportal/StM/260730_Effizienzgesetz.pdf",
            }
        ]

    def test_esf_external_pdfs_ignored(self, esf_foerderperiode_html):
        detail = parse_process_detail(esf_foerderperiode_html, BASE_URL)
        assert detail.pdf_links == []

    def test_same_pdf_in_both_markups_deduplicated(self):
        page = """<html><body>
            <a class="link-download-block" href="/fileadmin/entwurf.pdf">Entwurf (PDF)</a>
            <a class="link-list__link" href="/fileadmin/entwurf.pdf">Entwurf (PDF)</a>
        </body></html>"""
        detail = parse_process_detail(page, BASE_URL)
        assert detail.pdf_links == [{"title": "Entwurf (PDF)", "url": f"{BASE_URL}/fileadmin/entwurf.pdf"}]

    def test_portal_host_matched_case_insensitively(self):
        url = "https://Beteiligungsportal.Baden-Wuerttemberg.de/fileadmin/entwurf.pdf"
        page = f"""<html><body>
            <a class="link-list__link" href="{url}">Entwurf (PDF)</a>
        </body></html>"""
        detail = parse_process_detail(page, BASE_URL)
        assert detail.pdf_links == [{"title": "Entwurf (PDF)", "url": url}]


class TestIssue123ReleaseDate:
    """Issue #123 (DD-074): the draft is dated by its publication, not by the comment deadline."""

    WP18 = date(2026, 5, 1)
    PDF = (
        f"{BASE_URL}/fileadmin/redaktion/beteiligungsportal/Dokumente/2026/"
        "261012_Entwurf_Haushaltsbegleitgesetz_2027.pdf"
    )

    @pytest.fixture()
    def haushaltsbegleitgesetz_html(self):
        return (FIXTURES / "haushaltsbegleitgesetz_2027_detail.html").read_text()

    def test_draft_without_comment_phase(self, haushaltsbegleitgesetz_html):
        detail = parse_process_detail(haushaltsbegleitgesetz_html, BASE_URL)
        assert detail.is_draft
        assert [link["url"] for link in detail.pdf_links] == [self.PDF]

    @pytest.mark.parametrize(
        ("phases", "is_draft"),
        [
            (["Online-Kommentierung", "Antwort des Ministeriums"], True),
            (["Gesetzentwurf", "Beratung und Beschluss"], True),
            ([], False),  # strategy pages, Bürgerforen
            (["Kabinettsausschuss", "Ihre Meinung", "Strategie und Umsetzung"], False),
        ],
    )
    def test_is_draft_by_phase(self, phases, is_draft):
        assert RawBeteiligungDetail(title="T", ministry="M", phases=phases).is_draft is is_draft

    @pytest.mark.parametrize(
        ("url", "last_modified", "expected"),
        [
            (PDF, date(2026, 10, 12), date(2026, 10, 12)),
            # Re-uploaded later: the file name keeps the earlier date.
            (PDF, date(2026, 11, 20), date(2026, 10, 12)),
            # File name after the upload: not the publication date.
            (PDF, date(2026, 10, 1), date(2026, 10, 1)),
            # Typo before the Wahlperiode (Gewalthilfe: 200818_ for 2026-08-18).
            (f"{BASE_URL}/fileadmin/200818_GewHAG.pdf", date(2026, 8, 18), date(2026, 8, 18)),
            (f"{BASE_URL}/fileadmin/261399_Entwurf.pdf", date(2026, 10, 12), date(2026, 10, 12)),
            (f"{BASE_URL}/fileadmin/Entwurf.pdf", date(2026, 10, 12), date(2026, 10, 12)),
            # No Last-Modified header: the file name alone, if not in the future.
            (PDF, None, date(2026, 10, 12)),
            (f"{BASE_URL}/fileadmin/991231_Entwurf.pdf", None, None),
            (f"{BASE_URL}/fileadmin/Entwurf.pdf", None, None),
        ],
    )
    def test_release_date(self, url, last_modified, expected):
        assert release_date(url, last_modified, earliest=self.WP18) == expected
