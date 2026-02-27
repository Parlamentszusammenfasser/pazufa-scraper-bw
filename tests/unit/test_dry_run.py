"""Tests for the dry-run CLI orchestration module."""

from datetime import date
from unittest.mock import MagicMock, patch

from bawue.dry_run import parse_args, run_beteiligung, run_sitzungen, run_vorgaenge

WP17_START = date(2021, 4, 26)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_vorgang(vid="V-001", vorgangstyp="Gesetzgebung"):
    return {
        "titel": "Test Gesetz",
        "vorgangs_id": vid,
        "Vorgangstyp": vorgangstyp,
        "Initiative": "Fraktion GRÜNE",
        "fundstellen_parsed": [
            {
                "raw": "Gesetzentwurf 04.02.2026",
                "datum": "04.02.2026",
                "drucksache": "17/10266",
                "station_typ": "Gesetzentwurf",
                "pdf_url": "https://example.com/doc.pdf",
            },
        ],
    }


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.scraper == "all"
        assert args.wahlperiode_start_date == date(2021, 4, 26)
        assert args.wahlperiode == 17
        assert args.limit is None
        assert args.verbosity == 0
        assert args.json is False

    def test_wahlperiode_start_date_flag(self):
        args = parse_args(["--wahlperiode-start-date", "2022-01-01"])
        assert args.wahlperiode_start_date == date(2022, 1, 1)

    def test_scraper_flag(self):
        args = parse_args(["--scraper", "vorgaenge"])
        assert args.scraper == "vorgaenge"

    def test_limit(self):
        args = parse_args(["--limit", "5"])
        assert args.limit == 5

    def test_vorgangstyp(self):
        args = parse_args(["--vorgangstyp", "Kleine Anfrage"])
        assert args.vorgangstyp == "Kleine Anfrage"

    def test_json_flag(self):
        args = parse_args(["--json"])
        assert args.json is True

    def test_verbosity(self):
        args = parse_args(["--verbosity", "2"])
        assert args.verbosity == 2


# ---------------------------------------------------------------------------
# run_vorgaenge
# ---------------------------------------------------------------------------


class TestRunVorgaenge:
    @patch("bawue.dry_run.ParlisClient")
    def test_calls_parlis_client_search(self, MockParlisClient):
        mock_client = MockParlisClient.return_value
        mock_client.search.return_value = [_raw_vorgang()]

        reports, raw_list = run_vorgaenge(
            wahlperiode=17,
            wahlperiode_start_date=WP17_START,
            vorgangstypen=["Gesetzgebung"],
            limit=None,
        )

        mock_client.search.assert_called_once()
        args = mock_client.search.call_args[0]
        assert args[0] == "Gesetzgebung"
        assert args[1] == WP17_START, f"Expected date_from={WP17_START}, got {args[1]}"
        assert isinstance(args[2], date)

    @patch("bawue.dry_run.ParlisClient")
    def test_returns_vorgang_reports(self, MockParlisClient):
        mock_client = MockParlisClient.return_value
        mock_client.search.return_value = [_raw_vorgang()]

        reports, raw_list = run_vorgaenge(
            wahlperiode=17,
            wahlperiode_start_date=WP17_START,
            vorgangstypen=["Gesetzgebung"],
            limit=None,
        )

        assert len(reports) == 1
        assert reports[0].vorgang_id == "V-001"
        assert len(raw_list) == 1

    @patch("bawue.dry_run.ParlisClient")
    def test_limit_truncates(self, MockParlisClient):
        mock_client = MockParlisClient.return_value
        mock_client.search.return_value = [
            _raw_vorgang("V-001"),
            _raw_vorgang("V-002"),
            _raw_vorgang("V-003"),
        ]

        reports, raw_list = run_vorgaenge(
            wahlperiode=17,
            wahlperiode_start_date=WP17_START,
            vorgangstypen=["Gesetzgebung"],
            limit=2,
        )

        assert len(reports) == 2
        assert len(raw_list) == 2

    @patch("bawue.dry_run.ParlisClient")
    def test_multiple_types(self, MockParlisClient):
        mock_client = MockParlisClient.return_value
        mock_client.search.side_effect = [
            [_raw_vorgang("V-001", "Gesetzgebung")],
            [_raw_vorgang("V-002", "Kleine Anfrage")],
        ]

        reports, raw_list = run_vorgaenge(
            wahlperiode=17,
            wahlperiode_start_date=WP17_START,
            vorgangstypen=["Gesetzgebung", "Kleine Anfrage"],
            limit=None,
        )

        assert len(reports) == 2
        assert mock_client.search.call_count == 2

    @patch("bawue.dry_run.ParlisClient")
    def test_empty_results(self, MockParlisClient):
        mock_client = MockParlisClient.return_value
        mock_client.search.return_value = []

        reports, raw_list = run_vorgaenge(
            wahlperiode=17,
            wahlperiode_start_date=WP17_START,
            vorgangstypen=["Gesetzgebung"],
            limit=None,
        )

        assert reports == []
        assert raw_list == []


# ---------------------------------------------------------------------------
# run_beteiligung
# ---------------------------------------------------------------------------


class TestRunBeteiligung:
    @patch("bawue.dry_run.parse_process_detail")
    @patch("bawue.dry_run.BeteiligungClient")
    def test_fetches_and_analyzes(self, MockBetClient, mock_parse_detail):
        from bawue.beteiligung_parser import RawBeteiligungProcess

        mock_client = MockBetClient.return_value
        mock_client.fetch_process_list.return_value = [
            RawBeteiligungProcess(
                title="Klimaschutzgesetz",
                url="/de/mitmachen/lp-17/klima",
                slug="klima",
                status="closed",
            ),
        ]
        mock_client.fetch_process_detail.return_value = "<html></html>"
        mock_parse_detail.return_value = type("D", (), {
            "title": "Klimaschutzgesetz",
            "ministry": "UM",
            "pdf_links": [{"title": "E", "url": "http://x.pdf"}],
        })()

        reports = run_beteiligung(wahlperiode=17, limit=None)

        assert len(reports) == 1
        assert reports[0].slug == "klima"
        assert reports[0].pdf_count == 1

    @patch("bawue.dry_run.parse_process_detail")
    @patch("bawue.dry_run.BeteiligungClient")
    def test_limit(self, MockBetClient, mock_parse_detail):
        from bawue.beteiligung_parser import RawBeteiligungProcess

        mock_client = MockBetClient.return_value
        mock_client.fetch_process_list.return_value = [
            RawBeteiligungProcess(title="A", url="/a", slug="a", status="open"),
            RawBeteiligungProcess(title="B", url="/b", slug="b", status="open"),
        ]
        mock_client.fetch_process_detail.return_value = "<html></html>"
        mock_parse_detail.return_value = type("D", (), {
            "title": "A", "ministry": "M", "pdf_links": [],
        })()

        reports = run_beteiligung(wahlperiode=17, limit=1)

        assert len(reports) == 1


# ---------------------------------------------------------------------------
# run_sitzungen
# ---------------------------------------------------------------------------


class TestRunSitzungen:
    @patch("bawue.dry_run.requests.get")
    def test_fetches_and_analyzes(self, mock_get):

        # Build a minimal valid ICS
        ics_content = (
            b"BEGIN:VCALENDAR\r\n"
            b"BEGIN:VEVENT\r\n"
            b"UID:ev1\r\n"
            b"SUMMARY:Plenarsitzung: Tag 1\r\n"
            b"DTSTART:20260220T090000\r\n"
            b"DTEND:20260220T170000\r\n"
            b"END:VEVENT\r\n"
            b"BEGIN:VEVENT\r\n"
            b"UID:ev2\r\n"
            b"SUMMARY:Prasidium: Sitzung\r\n"
            b"DTSTART:20260220T100000\r\n"
            b"DTEND:20260220T120000\r\n"
            b"END:VEVENT\r\n"
            b"END:VCALENDAR\r\n"
        )
        mock_resp = MagicMock()
        mock_resp.content = ics_content
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        reports, total_all, total_filtered = run_sitzungen()

        assert len(reports) == 1  # only 1 date
        assert reports[0].event_count == 1  # Prasidium filtered out
        assert total_all == 2
        assert total_filtered == 1

    @patch("bawue.dry_run.requests.get")
    def test_empty_ics(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        reports, total_all, total_filtered = run_sitzungen()

        assert reports == []
        assert total_all == 0
        assert total_filtered == 0
