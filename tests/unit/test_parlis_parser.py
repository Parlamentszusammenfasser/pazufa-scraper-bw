"""Tests for PARLIS HTML parsing, Fundstelle regex extraction, and JSON comment parsing."""

from pathlib import Path

from bawue.parlis_parser import (
    _extract_json_comments,
    _json_comment_to_raw_vorgang,
    _parse_results_from_html,
    _parse_results_from_json,
    _parse_wmv35_fundstellen,
    parse_fundstelle_text,
    parse_results,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "parlis"


class TestExtractJsonComments:
    def test_extracts_single_json_comment(self):
        html = '<div><!--{"key": "val"}--></div>'
        result = _extract_json_comments(html)
        assert len(result) == 1
        assert result[0]["key"] == "val"

    def test_extracts_multiple_json_comments(self):
        html = '<!--{"a": 1}--><div>text</div><!--{"b": 2}-->'
        result = _extract_json_comments(html)
        assert len(result) == 2
        assert result[0]["a"] == 1
        assert result[1]["b"] == 2

    def test_returns_empty_for_no_comments(self):
        html = "<html><body><div>no comments</div></body></html>"
        assert _extract_json_comments(html) == []

    def test_skips_malformed_json_comment(self):
        html = '<!--{broken json--><div></div><!--{"valid": true}-->'
        result = _extract_json_comments(html)
        assert len(result) == 1
        assert result[0]["valid"] is True

    def test_ignores_non_json_comments(self):
        html = '<!-- regular comment --><div></div><!--{"ok": 1}-->'
        result = _extract_json_comments(html)
        assert len(result) == 1
        assert result[0]["ok"] == 1

    def test_handles_nested_braces(self):
        html = '<!--{"outer": {"inner": [1, 2]}}-->'
        result = _extract_json_comments(html)
        assert len(result) == 1
        assert result[0]["outer"]["inner"] == [1, 2]

    def test_handles_multiline_json_comment(self):
        html = '<!--{\n  "key": "value"\n}-->'
        result = _extract_json_comments(html)
        assert len(result) == 1
        assert result[0]["key"] == "value"


SAMPLE_JSON_COMMENT_FULL = {
    "EWBV10": [{"main": "Gesetz zur Förderung erneuerbarer Energien"}],
    "EWBV02": [{"main": "V-98001"}],
    "WMV40": [{"main": "V-98001"}],
    "WMV41": [{"main": "Gesetzgebung"}],
    "WMV30": [{"main": " Fraktion GRÜNE, Fraktion CDU"}],
    "WMV31": [{"main": "Verkündet"}],
    "WMV32": [{"main": "Umwelt; Energie"}],
    "WMV35": [
        {
            "main": (
                "https://www.landtag-bw.de/files/gg1-entwurf.pdf"
                " @@ 326783 @@ application/pdf"
                " @@ Gesetzentwurf    Fraktion GRÜNE, Fraktion CDU  10.01.2026"
                " Drucksache 17/12001   (24 S.)"
                " || 243551 <br> "
                "https://www.landtag-bw.de/files/plp/17_0150.pdf#page=33"
                " @@  @@  @@ Erste Beratung   Plenarprotokoll 17/150 15.01.2026"
                "  S. 8659-8667 || 243589 <br> "
            )
        }
    ],
}


class TestJsonCommentToRawVorgang:
    def test_maps_basic_fields(self):
        result = _json_comment_to_raw_vorgang(SAMPLE_JSON_COMMENT_FULL)
        assert result is not None
        assert result["titel"] == "Gesetz zur Förderung erneuerbarer Energien"
        assert result["vorgangs_id"] == "V-98001"
        assert result["Vorgangstyp"] == "Gesetzgebung"
        assert result["Initiative"] == "Fraktion GRÜNE, Fraktion CDU"
        assert result["Aktueller Stand"] == "Verkündet"

    def test_builds_detail_url(self):
        result = _json_comment_to_raw_vorgang(SAMPLE_JSON_COMMENT_FULL)
        assert result["detail_url"] == "https://parlis.landtag-bw.de/parlis/vorgang/V-98001"

    def test_strips_leading_whitespace_from_initiative(self):
        result = _json_comment_to_raw_vorgang(SAMPLE_JSON_COMMENT_FULL)
        assert not result["Initiative"].startswith(" ")

    def test_falls_back_to_wmv40_for_vorgangs_id(self):
        data = {
            "WMV40": [{"main": "V-99999"}],
            "EWBV10": [{"main": "Some Title"}],
            "WMV41": [{"main": "Gesetzgebung"}],
        }
        result = _json_comment_to_raw_vorgang(data)
        assert result["vorgangs_id"] == "V-99999"

    def test_returns_none_for_empty_data(self):
        assert _json_comment_to_raw_vorgang({}) is None

    def test_returns_none_without_vorgangs_id(self):
        data = {"EWBV10": [{"main": "Title Only"}]}
        assert _json_comment_to_raw_vorgang(data) is None

    def test_handles_missing_optional_fields(self):
        data = {
            "EWBV02": [{"main": "V-11111"}],
            "EWBV10": [{"main": "Minimal Vorgang"}],
            "WMV41": [{"main": "Gesetzgebung"}],
        }
        result = _json_comment_to_raw_vorgang(data)
        assert result is not None
        assert result["titel"] == "Minimal Vorgang"
        assert result["vorgangs_id"] == "V-11111"
        assert "Initiative" not in result
        assert "Aktueller Stand" not in result

    def test_includes_fundstellen_parsed(self):
        result = _json_comment_to_raw_vorgang(SAMPLE_JSON_COMMENT_FULL)
        assert "fundstellen_parsed" in result
        assert len(result["fundstellen_parsed"]) == 2


class TestParseWmv35Fundstellen:
    def test_parses_single_fundstelle(self):
        wmv35 = (
            "https://example.com/doc.pdf @@ 12345 @@ application/pdf"
            " @@ Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)"
            " || 99999"
        )
        result = _parse_wmv35_fundstellen(wmv35)
        assert len(result) == 1
        assert result[0]["pdf_url"] == "https://example.com/doc.pdf"
        assert result[0]["datum"] == "04.02.2026"
        assert result[0]["drucksache"] == "17/10266"
        assert result[0]["station_typ"] == "Gesetzentwurf"
        assert result[0]["seiten"] == 13

    def test_parses_multiple_fundstellen(self):
        wmv35 = (
            "https://example.com/a.pdf @@ 1 @@ application/pdf"
            " @@ Gesetzentwurf    CDU  01.01.2026 Drucksache 17/10000 || 100 <br> "
            "https://example.com/b.pdf @@ 2 @@ application/pdf"
            " @@ Erste Beratung   Plenarprotokoll 17/141 05.02.2026 || 200 <br> "
        )
        result = _parse_wmv35_fundstellen(wmv35)
        assert len(result) == 2
        assert result[0]["station_typ"] == "Gesetzentwurf"
        assert result[1]["plenarprotokoll"] == "17/141"

    def test_extracts_pdf_url(self):
        wmv35 = (
            "https://www.landtag-bw.de/files/doc.pdf @@ 123 @@ application/pdf"
            " @@ Gesetzentwurf    CDU  01.01.2026 || 50"
        )
        result = _parse_wmv35_fundstellen(wmv35)
        assert result[0]["pdf_url"] == "https://www.landtag-bw.de/files/doc.pdf"

    def test_handles_empty_blob_and_mime(self):
        """Real PARLIS pattern: URL present but blob_id and mime are empty."""
        wmv35 = (
            "https://www.landtag-bw.de/files/plp/17_141.pdf#page=33"
            " @@  @@  @@ Erste Beratung   Plenarprotokoll 17/141 05.02.2026"
            "  S. 8659-8667 || 243589"
        )
        result = _parse_wmv35_fundstellen(wmv35)
        assert len(result) == 1
        assert result[0]["pdf_url"] == "https://www.landtag-bw.de/files/plp/17_141.pdf#page=33"
        assert result[0]["plenarprotokoll"] == "17/141"

    def test_returns_empty_for_empty_input(self):
        assert _parse_wmv35_fundstellen("") == []
        assert _parse_wmv35_fundstellen("   ") == []

    def test_strips_trailing_br(self):
        wmv35 = "https://example.com/a.pdf @@ 1 @@ pdf @@ Gesetzentwurf    CDU  01.01.2026 || 100 <br> "
        result = _parse_wmv35_fundstellen(wmv35)
        assert len(result) == 1

    def test_deduplicates_identical_segments(self):
        """PARLIS sometimes returns the same Fundstelle entry duplicated 2× or 3×."""
        wmv35 = (
            "https://example.com/a.pdf @@ 1 @@ application/pdf"
            " @@ Gesetzentwurf    CDU  01.01.2026 Drucksache 17/3273   (10 S.) || 100 <br> "
            "https://example.com/b.pdf @@ 2 @@ application/pdf"
            " @@ Erste Beratung   Plenarprotokoll 17/141 05.02.2026 || 200 <br> "
            "https://example.com/a.pdf @@ 1 @@ application/pdf"
            " @@ Gesetzentwurf    CDU  01.01.2026 Drucksache 17/3273   (10 S.) || 100 <br> "
            "https://example.com/b.pdf @@ 2 @@ application/pdf"
            " @@ Erste Beratung   Plenarprotokoll 17/141 05.02.2026 || 200 <br> "
        )
        result = _parse_wmv35_fundstellen(wmv35)
        assert len(result) == 2
        assert result[0]["drucksache"] == "17/3273"
        assert result[1]["plenarprotokoll"] == "17/141"

    def test_deduplicates_triple_repetition(self):
        """Three identical copies → only one remains."""
        segment = (
            "https://example.com/a.pdf @@ 1 @@ application/pdf"
            " @@ Gesetzentwurf    CDU  01.01.2026 Drucksache 17/3273 || 100"
        )
        wmv35 = f"{segment} <br> {segment} <br> {segment}"
        result = _parse_wmv35_fundstellen(wmv35)
        assert len(result) == 1

    def test_preserves_order_after_dedup(self):
        """After dedup, the original order of first-seen entries is preserved."""
        wmv35 = (
            "https://example.com/c.pdf @@ 3 @@ application/pdf"
            " @@ Zweite Beratung   Plenarprotokoll 17/155 20.03.2026 || 300 <br> "
            "https://example.com/a.pdf @@ 1 @@ application/pdf"
            " @@ Gesetzentwurf    CDU  01.01.2026 Drucksache 17/3273 || 100 <br> "
            "https://example.com/c.pdf @@ 3 @@ application/pdf"
            " @@ Zweite Beratung   Plenarprotokoll 17/155 20.03.2026 || 300 <br> "
        )
        result = _parse_wmv35_fundstellen(wmv35)
        assert len(result) == 2
        assert result[0]["plenarprotokoll"] == "17/155"
        assert result[1]["drucksache"] == "17/3273"


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


_SAMPLE_JSON_COMMENT = (
    '{"EWBV10": [{"main": "JSON-Parsed Title"}],'
    ' "EWBV02": [{"main": "V-77777"}],'
    ' "WMV41": [{"main": "Gesetzgebung"}],'
    ' "WMV30": [{"main": " Fraktion SPD"}],'
    ' "WMV31": [{"main": "Verkündet"}],'
    ' "WMV35": [{"main": "https://example.com/doc.pdf'
    " @@ 111 @@ application/pdf"
    " @@ Gesetzentwurf    Fraktion SPD  01.03.2026"
    ' Drucksache 17/11000   (5 S.) || 500"}]}'
)

SAMPLE_JSON_IN_HTML = f"""<html><body>
<div class="record-container">
  <div class="efxRecordRepeater" data-efx-rec="abc123">
    <!--{_SAMPLE_JSON_COMMENT}-->
    <a class="efxZoomShort-Vorgang">HTML-Parsed Title</a>
    <dl><dt>Vorgangs-ID:</dt><dd>V-77777</dd></dl>
  </div>
</div>
</body></html>"""


class TestParseResultsDispatcher:
    def test_prefers_json_when_available(self):
        results = parse_results(SAMPLE_JSON_IN_HTML)
        assert len(results) == 1
        assert results[0]["titel"] == "JSON-Parsed Title"
        assert results[0]["vorgangs_id"] == "V-77777"
        assert results[0]["Vorgangstyp"] == "Gesetzgebung"

    def test_falls_back_to_html_when_no_json(self):
        """Existing HTML-only fixtures still work via fallback."""
        results = parse_results(SAMPLE_HTML_RECORD)
        assert len(results) == 1
        assert results[0]["titel"] == "Gesetz zur Änderung des Landeshochschulgesetzes"

    def test_falls_back_on_malformed_json_comments(self):
        html = """<html><body>
<!--{this is not valid json}-->
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Fallback Title</a>
  <dl><dt>Vorgangs-ID:</dt><dd>V-55555</dd><dt>Vorgangstyp:</dt><dd>Gesetzgebung</dd></dl>
  <script>var url = "/parlis/vorgang/V-55555";</script>
</div>
</body></html>"""
        results = parse_results(html)
        assert len(results) == 1
        assert results[0]["titel"] == "Fallback Title"

    def test_json_path_extracts_fundstellen(self):
        results = parse_results(SAMPLE_JSON_IN_HTML)
        fundstellen = results[0]["fundstellen_parsed"]
        assert len(fundstellen) == 1
        assert fundstellen[0]["station_typ"] == "Gesetzentwurf"
        assert fundstellen[0]["datum"] == "01.03.2026"
        assert fundstellen[0]["pdf_url"] == "https://example.com/doc.pdf"


class TestParseFundstelleSingleSpaceFallback:
    """When PARLIS uses a single space as separator, station_typ is not extracted.

    The raw text is still preserved, allowing downstream fallback logic
    in _build_station to use it for enum mapping.
    """

    def test_single_space_gesetzesbeschluss_has_no_station_typ(self):
        text = "Gesetzesbeschluss des Landtags 04.02.2026 Drucksache 17/10254"
        result = parse_fundstelle_text(text)
        assert "station_typ" not in result
        assert result["raw"] == text
        assert result["datum"] == "04.02.2026"
        assert result["drucksache"] == "17/10254"

    def test_single_space_gesetz_has_no_station_typ(self):
        text = "Gesetz Gesetzblatt für Baden-Württemberg 2026 Nr. 20  S. 1  10.02.2026"
        result = parse_fundstelle_text(text)
        assert "station_typ" not in result
        assert result["raw"] == text

    def test_double_space_gesetzesbeschluss_has_station_typ(self):
        text = "Gesetzesbeschluss des Landtags  04.02.2026 Drucksache 17/10254"
        result = parse_fundstelle_text(text)
        assert result["station_typ"] == "Gesetzesbeschluss des Landtags"


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


class TestParseFundstelleGesetzblattYearFallback:
    def test_extracts_year_from_gesetzblatt_berichtigung(self):
        text = "Berichtigung des Gesetzes  Gesetzblatt für Baden-Württemberg 2022 Nr. 37     S. 595"
        result = parse_fundstelle_text(text)
        assert result["datum"] == "01.01.2022"
        assert result["station_typ"] == "Berichtigung des Gesetzes"

    def test_explicit_date_takes_precedence_over_gesetzblatt_year(self):
        text = "Gesetz  vom 16. Dezember 2025 Gesetzblatt für Baden-Württemberg 2025 Nr. 147  S. 1-3"
        result = parse_fundstelle_text(text)
        assert result["datum"] == "16.12.2025"

    def test_dd_mm_yyyy_takes_precedence_over_gesetzblatt_year(self):
        text = "Gesetz  Gesetzblatt für Baden-Württemberg 2026 Nr. 20  S. 1  10.02.2026"
        result = parse_fundstelle_text(text)
        assert result["datum"] == "10.02.2026"


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


def _compare_vorgang_core_fields(json_result, html_result):
    """Compare the core fields that both paths must agree on."""
    assert json_result["titel"] == html_result["titel"]
    assert json_result["vorgangs_id"] == html_result["vorgangs_id"]
    if "Vorgangstyp" in html_result:
        assert json_result["Vorgangstyp"] == html_result["Vorgangstyp"]
    if "Initiative" in html_result:
        assert json_result["Initiative"] == html_result["Initiative"]
    assert json_result["detail_url"] == html_result["detail_url"]

    json_fund = json_result.get("fundstellen_parsed", [])
    html_fund = html_result.get("fundstellen_parsed", [])
    assert len(json_fund) == len(html_fund), f"Fundstellen count mismatch: JSON={len(json_fund)}, HTML={len(html_fund)}"

    for i, (jf, hf) in enumerate(zip(json_fund, html_fund, strict=True)):
        assert jf.get("station_typ") == hf.get("station_typ"), f"Fund[{i}] station_typ"
        assert jf.get("datum") == hf.get("datum"), f"Fund[{i}] datum"
        assert jf.get("drucksache") == hf.get("drucksache"), f"Fund[{i}] drucksache"
        assert jf.get("plenarprotokoll") == hf.get("plenarprotokoll"), f"Fund[{i}] pp"
        assert jf.get("ausschuss") == hf.get("ausschuss"), f"Fund[{i}] ausschuss"
        assert jf.get("autor_text") == hf.get("autor_text"), f"Fund[{i}] autor"
        assert jf.get("seiten") == hf.get("seiten"), f"Fund[{i}] seiten"


class TestParseResultsParity:
    """Verify JSON and HTML parsing paths produce equivalent results."""

    def _load_and_compare(self, fixture_name: str):
        html_content = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        json_comments = _extract_json_comments(html_content)
        json_results = _parse_results_from_json(json_comments)
        html_results = _parse_results_from_html(html_content)
        assert len(json_results) == len(html_results)
        for jr, hr in zip(json_results, html_results, strict=True):
            _compare_vorgang_core_fields(jr, hr)

    def test_gesetzgebung_parity(self):
        self._load_and_compare("gesetzgebung_results_with_json.html")

    def test_kleine_anfrage_parity(self):
        self._load_and_compare("kleine_anfrage_results_with_json.html")

    def test_antrag_parity(self):
        self._load_and_compare("antrag_results_with_json.html")
