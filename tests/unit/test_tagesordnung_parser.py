"""Tests for the Tagesordnung PDF parser."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from bawue.tagesordnung_parser import extract_text_from_pdf, parse_tops

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_TEXT = (FIXTURES_DIR / "sample_tagesordnung.txt").read_text(encoding="utf-8")


class TestParseTops:
    def test_parse_tops_returns_correct_count(self):
        tops = parse_tops(SAMPLE_TEXT)
        assert len(tops) == 3

    def test_parse_tops_extracts_nummer(self):
        tops = parse_tops(SAMPLE_TEXT)
        nummern = [t.nummer for t in tops]
        assert "1" in nummern
        assert "2" in nummern
        assert "3" in nummern

    def test_parse_tops_extracts_titel(self):
        tops = parse_tops(SAMPLE_TEXT)
        expected_top1 = (
            "Aktuelle Debatte \u2013 Starke Frauen, starke Wirtschaft \u2013 "
            "Selbstbestimmung statt sozialer Kälte beantragt von der Fraktion GRÜNE"
        )
        assert tops[0].titel == expected_top1
        assert tops[2].titel == "Fragestunde"

    def test_parse_tops_strips_outcome_annotations(self):
        tops = parse_tops(SAMPLE_TEXT)
        titel_2 = tops[1].titel
        assert "angenommen" not in titel_2.lower()
        assert "abgelehnt" not in titel_2.lower()
        assert "überwiesen" not in titel_2.lower()

    def test_parse_tops_strips_drucksache(self):
        tops = parse_tops(SAMPLE_TEXT)
        titel_2 = tops[1].titel
        assert "Drucksache" not in titel_2

    def test_parse_tops_empty_text(self):
        assert parse_tops("") == []
        assert parse_tops("   ") == []

    def test_parse_tops_no_tops(self):
        assert parse_tops("Einige Zeilen ohne Tagesordnungspunkte.") == []

    def test_parse_tops_strips_header_annotation(self):
        text = "Das Plenum hat\nfolgende Beschlüsse\ngefasst:\n1. Fragestunde\n2. Aktuelle Debatte\n"
        tops = parse_tops(text)
        assert len(tops) == 2
        assert tops[0].titel == "Fragestunde"


class TestExtractTextFromPdf:
    def test_extract_text_calls_pdftotext(self):
        mock_result = MagicMock()
        mock_result.stdout = b"1. Fragestunde\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = extract_text_from_pdf(b"%PDF-1.4 fake")

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "pdftotext"
        assert call_args.kwargs.get("input") == b"%PDF-1.4 fake"
        assert result == "1. Fragestunde\n"

    def test_extract_text_returns_string(self):
        mock_result = MagicMock()
        mock_result.stdout = b"1. Test\n"

        with patch("subprocess.run", return_value=mock_result):
            result = extract_text_from_pdf(b"%PDF fake")

        assert isinstance(result, str)
