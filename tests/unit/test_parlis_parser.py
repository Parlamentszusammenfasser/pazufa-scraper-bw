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
