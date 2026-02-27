"""Tests for PARLIS HTML parsing and Fundstelle regex extraction."""

from bawue.parlis_parser import parse_fundstelle_text, parse_results

SAMPLE_HTML_RECORD = """<html><body>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Gesetz zur Änderung des Landeshochschulgesetzes</a>
  <dl>
    <dt>Vorgangs-ID:</dt><dd>V-12345</dd>
    <dt>Vorgangstyp:</dt><dd>Gesetzgebung</dd>
    <dt>Initiative:</dt><dd>Fraktion GRÜNE</dd>
    <dt>Aktueller Stand:</dt><dd>Verkündet</dd>
  </dl>
  <a class="fundstellenLinks" href="https://www.landtag-bw.de/resource/blob/12345/doc.pdf">
    Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)
  </a>
  <a class="fundstellenLinks" href="">
    Erste Beratung   Plenarprotokoll 17/141 05.02.2026
  </a>
  <a class="fundstellenLinks" href="https://www.landtag-bw.de/resource/blob/67890/report.pdf">
    Beschlussempfehlung und Bericht    Ausschuss für Wirtschaft  02.02.2026 Drucksache 17/10210
  </a>
  <script>var url = "/parlis/vorgang/V-12345";</script>
</div>
</body></html>"""

SAMPLE_HTML_TWO_RECORDS = """<html><body>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Gesetz A</a>
  <dl><dt>Vorgangs-ID:</dt><dd>V-001</dd><dt>Vorgangstyp:</dt><dd>Gesetzgebung</dd><dt>Initiative:</dt><dd>CDU</dd></dl>
  <a class="fundstellenLinks" href="">Gesetzentwurf    CDU  01.01.2026 Drucksache 17/10000</a>
  <script>var url = "/parlis/vorgang/V-001";</script>
</div>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Gesetz B</a>
  <dl><dt>Vorgangs-ID:</dt><dd>V-002</dd><dt>Vorgangstyp:</dt><dd>Gesetzgebung</dd><dt>Initiative:</dt><dd>SPD</dd></dl>
  <a class="fundstellenLinks" href="">Gesetzentwurf    SPD  02.01.2026 Drucksache 17/10001</a>
  <script>var url = "/parlis/vorgang/V-002";</script>
</div>
</body></html>"""


class TestParseFundstelleText:
    def test_extracts_date(self):
        result = parse_fundstelle_text("Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)")
        assert result["datum"] == "04.02.2026"

    def test_extracts_drucksache(self):
        result = parse_fundstelle_text("Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)")
        assert result["drucksache"] == "17/10266"

    def test_extracts_plenarprotokoll(self):
        result = parse_fundstelle_text("Erste Beratung   Plenarprotokoll 17/141 05.02.2026")
        assert result["plenarprotokoll"] == "17/141"

    def test_extracts_station_typ(self):
        result = parse_fundstelle_text("Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)")
        assert result["station_typ"] == "Gesetzentwurf"

    def test_extracts_ausschuss(self):
        result = parse_fundstelle_text(
            "Beschlussempfehlung und Bericht    Ausschuss für Wirtschaft  02.02.2026 Drucksache 17/10210"
        )
        assert "Ausschuss für Wirtschaft" in result["ausschuss"]

    def test_extracts_pages(self):
        result = parse_fundstelle_text("Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)")
        assert result["seiten"] == 13

    def test_preserves_raw_text(self):
        text = "Gesetzentwurf    Fraktion GRÜNE  04.02.2026"
        result = parse_fundstelle_text(text)
        assert result["raw"] == text


class TestParseFundstelleAutor:
    def test_single_author(self):
        result = parse_fundstelle_text("Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)")
        assert result["autor_text"] == "Fraktion GRÜNE"

    def test_multiple_authors(self):
        result = parse_fundstelle_text(
            "Gesetzentwurf    Fraktion GRÜNE, Fraktion der CDU  04.02.2026 Drucksache 17/10266"
        )
        assert result["autor_text"] == "Fraktion GRÜNE, Fraktion der CDU"

    def test_landesregierung(self):
        result = parse_fundstelle_text("Gesetzentwurf    Landesregierung  01.03.2026 Drucksache 17/11000   (5 S.)")
        assert result["autor_text"] == "Landesregierung"

    def test_no_autor_for_ausschuss(self):
        result = parse_fundstelle_text(
            "Beschlussempfehlung und Bericht    Ausschuss für Wirtschaft  02.02.2026 Drucksache 17/10210"
        )
        assert "autor_text" not in result

    def test_no_autor_for_plenarprotokoll(self):
        result = parse_fundstelle_text("Erste Beratung   Plenarprotokoll 17/141 05.02.2026")
        assert "autor_text" not in result

    def test_no_gap_text(self):
        result = parse_fundstelle_text("Gesetzentwurf    04.02.2026 Drucksache 17/10266")
        assert "autor_text" not in result


class TestParseResults:
    def test_parses_single_record(self):
        results = parse_results(SAMPLE_HTML_RECORD)
        assert len(results) == 1
        vorgang = results[0]
        assert vorgang["titel"] == "Gesetz zur Änderung des Landeshochschulgesetzes"
        assert vorgang["vorgangs_id"] == "V-12345"
        assert vorgang["Initiative"] == "Fraktion GRÜNE"
        assert len(vorgang["fundstellen_parsed"]) == 3

    def test_parses_multiple_records(self):
        results = parse_results(SAMPLE_HTML_TWO_RECORDS)
        assert len(results) == 2
        assert results[0]["titel"] == "Gesetz A"
        assert results[1]["titel"] == "Gesetz B"

    def test_parses_all_fundstelle_types(self):
        results = parse_results(SAMPLE_HTML_RECORD)
        fundstellen = results[0]["fundstellen_parsed"]

        # Gesetzentwurf fundstelle
        assert fundstellen[0]["datum"] == "04.02.2026"
        assert fundstellen[0]["drucksache"] == "17/10266"
        assert fundstellen[0]["station_typ"] == "Gesetzentwurf"
        assert fundstellen[0]["seiten"] == 13
        assert fundstellen[0]["pdf_url"] == "https://www.landtag-bw.de/resource/blob/12345/doc.pdf"

        # Plenarprotokoll fundstelle
        assert fundstellen[1]["datum"] == "05.02.2026"
        assert fundstellen[1]["plenarprotokoll"] == "17/141"
        assert fundstellen[1]["station_typ"] == "Erste Beratung"

        # Ausschuss fundstelle
        assert fundstellen[2]["datum"] == "02.02.2026"
        assert fundstellen[2]["drucksache"] == "17/10210"
        assert fundstellen[2]["station_typ"] == "Beschlussempfehlung und Bericht"
        assert "Ausschuss für Wirtschaft" in fundstellen[2]["ausschuss"]

    def test_extracts_detail_url(self):
        results = parse_results(SAMPLE_HTML_RECORD)
        assert results[0]["detail_url"] == "https://parlis.landtag-bw.de/parlis/vorgang/V-12345"

    def test_empty_html_returns_empty_list(self):
        results = parse_results("<html><body></body></html>")
        assert results == []


class TestParseFundstellePageNumberNotConfusedForYear:
    def test_page_number_not_confused_for_year(self):
        text = "vom 00.00.3640   Plenarprotokoll 17/60 09.03.2023  S. 3640-3644"
        result = parse_fundstelle_text(text)
        assert result["datum"] == "09.03.2023"


class TestParseFundstelleTextGermanDate:
    def test_extracts_german_long_form_date(self):
        text = "Gesetz  vom 16. Dezember 2025 Gesetzblatt für Baden-Württemberg 2025 Nr. 147     S. 1-3"
        result = parse_fundstelle_text(text)
        assert result["datum"] == "16.12.2025"

    def test_extracts_single_digit_day(self):
        text = "Gesetz  vom 4. Februar 2026 Gesetzblatt für Baden-Württemberg 2026 Nr. 12"
        result = parse_fundstelle_text(text)
        assert result["datum"] == "04.02.2026"

    def test_dd_mm_yyyy_still_works(self):
        result = parse_fundstelle_text("Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266")
        assert result["datum"] == "04.02.2026"

    def test_no_date_absent_datum_key(self):
        result = parse_fundstelle_text("Gesetz ohne Datum Gesetzblatt für Baden-Württemberg")
        assert "datum" not in result


SAMPLE_HTML_TIME_ELEMENT = """<html><body>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Gesetz zur Änderung XY</a>
  <dl>
    <dt>Vorgangs-ID:</dt><dd>V-99999</dd>
    <dt>Vorgangstyp:</dt><dd>Gesetzgebung</dd>
    <dt>Initiative:</dt><dd>Landesregierung</dd>
  </dl>
  <span>
    <time datetime="2026-02-09">9. Februar 2026</time>
    <a class="fundstellenLinks" href="https://www.landtag-bw.de/resource/blob/99/law.pdf">
      Gesetz  vom 9. Februar 2026 Gesetzblatt für Baden-Württemberg 2026 Nr. 10     S. 1-5
    </a>
  </span>
  <script>var url = "/parlis/vorgang/V-99999";</script>
</div>
</body></html>"""

SAMPLE_HTML_TIME_ELEMENT_WITH_EXISTING_DATE = """<html><body>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Gesetzentwurf XY</a>
  <dl>
    <dt>Vorgangs-ID:</dt><dd>V-88888</dd>
    <dt>Vorgangstyp:</dt><dd>Gesetzgebung</dd>
    <dt>Initiative:</dt><dd>CDU</dd>
  </dl>
  <span>
    <time datetime="2026-03-15">15. März 2026</time>
    <a class="fundstellenLinks" href="">
      Gesetzentwurf    CDU  04.02.2026 Drucksache 17/10266
    </a>
  </span>
  <script>var url = "/parlis/vorgang/V-88888";</script>
</div>
</body></html>"""


class TestParseResultsTimeElement:
    def test_extracts_date_from_time_element(self):
        results = parse_results(SAMPLE_HTML_TIME_ELEMENT)
        assert len(results) == 1
        fundstellen = results[0]["fundstellen_parsed"]
        assert len(fundstellen) == 1
        assert fundstellen[0]["datum"] == "09.02.2026"

    def test_time_element_does_not_override_existing_date(self):
        results = parse_results(SAMPLE_HTML_TIME_ELEMENT_WITH_EXISTING_DATE)
        assert len(results) == 1
        fundstellen = results[0]["fundstellen_parsed"]
        assert len(fundstellen) == 1
        # Text-based date (04.02.2026) should win over time element (15.03.2026)
        assert fundstellen[0]["datum"] == "04.02.2026"


SAMPLE_HTML_TIME_ELEMENT_SIBLING_SPAN = """<html><body>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Kleine Anfrage XY</a>
  <dl>
    <dt>Vorgangs-ID:</dt><dd>V-11111</dd>
    <dt>Vorgangstyp:</dt><dd>Kleine Anfrage</dd>
    <dt>Initiative:</dt><dd>Daniel Born (SPD)</dd>
  </dl>
  <span>
    <span><time datetime="2022-01-18">18.01.2022</time></span>
    <span><a class="fundstellenLinks" href="">Kleine Anfrage    Daniel Born (SPD)  Drucksache 17/1440</a></span>
  </span>
  <script>var url = "/parlis/vorgang/V-11111";</script>
</div>
</body></html>"""


class TestParseResultsTimeElementSiblingSpan:
    def test_extracts_date_from_time_element_in_sibling_span(self):
        """<time> in a sibling <span> of the <a>'s parent — date must still be found."""
        results = parse_results(SAMPLE_HTML_TIME_ELEMENT_SIBLING_SPAN)
        assert len(results) == 1
        fundstellen = results[0]["fundstellen_parsed"]
        assert len(fundstellen) == 1
        assert fundstellen[0]["datum"] == "18.01.2022"
