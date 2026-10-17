"""Tests for the Tagesordnung (Plenum agenda) parser (GitHub issue #66, DD-070).

Fixture: the agenda of the 14th Plenarsitzung on 21.10.2026 as published on
https://www.landtag-bw.de/de/aktuelles/tagesordnungen (Stand 16.10.2026), text as
extracted by ``bawue_dok.extract_pdf_text`` (kreuzberg 4.10.3). The extraction differs by
platform: on Linux (CI, Docker) it has no blank lines between paragraphs, on macOS it has.
"""

from datetime import date
from pathlib import Path

import pytest

from bawue.tagesordnung import ParsedTop, agenda_links, parse_tops

FIXTURES = Path(__file__).parent.parent / "fixtures" / "tagesordnung"


@pytest.fixture(params=["linux", "macos"])
def agenda(request) -> str:
    return (FIXTURES / f"2026-10-21_14_Plenarsitzung.{request.param}.txt").read_text()


PDF_PATH = "/resource/blob/656162/f6ca3d092305e2f3a4a53e8a5d22619c/2026-10-21_14_Plenarsitzung.pdf"


class TestAgendaLinks:
    def test_plenarsitzung_link_yields_date_number_and_url(self):
        html = f'<a href="{PDF_PATH}">Tagesordnung</a><a href="{PDF_PATH}">PDF</a>'
        assert agenda_links(html) == [
            (date(2026, 10, 21), 14, f"https://www.landtag-bw.de{PDF_PATH}"),
        ]

    def test_other_pdfs_are_ignored(self):
        html = '<a href="/resource/blob/654206/58a959a809ef14bef9ac466c39a359e8/Willkommen-E.pdf">x</a>'
        assert agenda_links(html) == []


class TestParseTopsIssue66:
    def test_all_items_in_order(self, agenda):
        tops = parse_tops(agenda)
        assert [t.nummer for t in tops] == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_titel_runs_to_the_drucksache_dehyphenated(self, agenda):
        tops = parse_tops(agenda)
        # The agenda separates with an en dash.
        assert tops[0].titel == (
            "Antrag der Fraktion der AfD – Einsetzung und Auftrag eines Untersuchungsausschusses zur "  # noqa: RUF001
            "Organisation sowie Haushalts- und Wirtschaftsführung des Landesmedienzentrums "
            "Baden-Württemberg (LMZ) („UsA Landesmedienzentrum“) – Drucksache 18/672"  # noqa: RUF001
        )
        # macOS: "Debatte/ vorgezogene"
        assert tops[1].titel.replace("/ ", "/") == "Aktuelle Debatte/vorgezogene Initiative"
        # Not the speaking times of the second column.
        assert all("Min." not in t.titel for t in tops)
        assert tops[4].titel.endswith("– Drucksache 18/369")  # noqa: RUF001

    def test_gesetzentwurf_top_links_all_its_drucksachen(self, agenda):
        """Second reading: the Gesetzentwurf and the Beschlussempfehlung (DD-070)."""
        tops = parse_tops(agenda)
        assert tops[3] == ParsedTop(4, tops[3].titel, ("18/700",))
        assert tops[4].drucksachen == ("18/369", "18/659")
        assert tops[6].drucksachen == ("18/692",)

    def test_antrag_top_links_no_drucksache(self, agenda):
        """Anträge have no Vorgang in the backend: their Dokument would be an orphan (DD-070)."""
        tops = parse_tops(agenda)
        assert tops[0].drucksachen == ()
        assert tops[7].drucksachen == ()

    def test_unnumbered_drucksache_is_skipped(self, agenda):
        """'Drucksache 18/........': the Gesetzentwurf is not printed yet."""
        tops = parse_tops(agenda)
        assert tops[5].drucksachen == ()

    def test_number_inside_an_item_does_not_start_a_new_one(self):
        text = "1. Erste Beratung des Gesetzentwurfs\nDrucksache 18/1\n \n3. Oktober 2026\n2. Antrag\n"
        assert [t.nummer for t in parse_tops(text)] == [1, 2]

    def test_no_items(self):
        assert parse_tops("Zur Zeit liegen keine Tagesordnungen vor") == []


class TestReviewPr124:
    def test_antrag_mentioning_a_gesetzentwurf_links_nothing(self):
        """Only a reading of a Gesetzentwurf has a Vorgang; an Antrag's Dokument would be an orphan."""
        text = (
            "1. Antrag der Fraktion der SPD\n– Die Landesregierung soll einen Gesetzentwurf vorlegen\n"  # noqa: RUF001
            "– Drucksache 18/300\n"  # noqa: RUF001
        )
        assert parse_tops(text)[0].drucksachen == ()
