"""Tests for the Tagesordnung (Plenum agenda) parser (GitHub issue #66, DD-070).

Fixture: the agenda of the 14th Plenarsitzung on 21.10.2026 as published on
https://www.landtag-bw.de/de/aktuelles/tagesordnungen (Stand 16.10.2026), text as
extracted by ``bawue_dok.extract_pdf_text``.
"""

from datetime import date
from pathlib import Path

from bawue.tagesordnung import ParsedTop, agenda_links, parse_tops

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tagesordnung" / "2026-10-21_14_Plenarsitzung.txt"
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
    def test_all_items_in_order(self):
        tops = parse_tops(FIXTURE.read_text())
        assert [t.nummer for t in tops] == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_titel_is_the_first_paragraph_dehyphenated(self):
        tops = parse_tops(FIXTURE.read_text())
        # The agenda separates with an en dash.
        assert tops[0].titel == (
            "Antrag der Fraktion der AfD – Einsetzung und Auftrag eines Untersuchungsausschusses zur "  # noqa: RUF001
            "Organisation sowie Haushalts- und Wirtschaftsführung des Landesmedienzentrums "
            "Baden-Württemberg (LMZ) („UsA Landesmedienzentrum“) – Drucksache 18/672"  # noqa: RUF001
        )
        assert tops[1].titel == "Aktuelle Debatte/ vorgezogene Initiative"

    def test_gesetzentwurf_top_links_all_its_drucksachen(self):
        """Second reading: the Gesetzentwurf and the Beschlussempfehlung (DD-070)."""
        tops = parse_tops(FIXTURE.read_text())
        assert tops[3] == ParsedTop(4, tops[3].titel, ("18/700",))
        assert tops[4].drucksachen == ("18/369", "18/659")
        assert tops[6].drucksachen == ("18/692",)

    def test_antrag_top_links_no_drucksache(self):
        """Anträge have no Vorgang in the backend: their Dokument would be an orphan (DD-070)."""
        tops = parse_tops(FIXTURE.read_text())
        assert tops[0].drucksachen == ()
        assert tops[7].drucksachen == ()

    def test_unnumbered_drucksache_is_skipped(self):
        """'Drucksache 18/........': the Gesetzentwurf is not printed yet."""
        tops = parse_tops(FIXTURE.read_text())
        assert tops[5].drucksachen == ()

    def test_number_inside_an_item_does_not_start_a_new_one(self):
        text = "1. Erste Beratung des Gesetzentwurfs\nDrucksache 18/1\n \n3. Oktober 2026\n2. Antrag\n"
        assert [t.nummer for t in parse_tops(text)] == [1, 2]

    def test_no_items(self):
        assert parse_tops("Zur Zeit liegen keine Tagesordnungen vor") == []
