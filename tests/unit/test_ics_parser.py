"""Tests for the ICS parser module."""

from datetime import date, datetime
from pathlib import Path

import pytest

from bawue.ics_parser import (
    clean_title,
    extract_gremium_name,
    extract_session_number,
    group_events_by_date,
    parse_ics_feed,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def ics_bytes() -> bytes:
    return (FIXTURES_DIR / "sample_calendar.ics").read_bytes()


class TestExtractSessionNumber:
    """Test session number extraction from SUMMARY strings."""

    def test_plenarsitzung_extracts_number(self):
        assert extract_session_number("Plenarsitzung: 142. Sitzung") == 142

    def test_plenarsitzung_large_number(self):
        assert extract_session_number("Plenarsitzung: 143. Sitzung") == 143

    def test_ausschuss_returns_zero(self):
        assert extract_session_number("Fraktions- und Ausschusssitzungen: Ausschuesse") == 0

    def test_fina_returns_zero(self):
        assert extract_session_number("Fraktions- und Ausschusssitzungen: FinA") == 0

    def test_haushaltsberatungen_returns_zero(self):
        assert extract_session_number("Haushaltsberatungen: Finanzausschuss") == 0


class TestCleanTitle:
    """Test title cleaning to remove trailing colon artifacts."""

    def test_strips_trailing_colon_space(self):
        assert clean_title("Plenarsitzung: ") == "Plenarsitzung"

    def test_strips_trailing_colon_no_space(self):
        assert clean_title("Plenarsitzung:") == "Plenarsitzung"

    def test_preserves_full_title(self):
        assert clean_title("Plenarsitzung: 142. Sitzung") == "Plenarsitzung: 142. Sitzung"

    def test_preserves_committee_title(self):
        title = "Fraktions- und Ausschusssitzungen: Ausschuesse"
        assert clean_title(title) == title

    def test_empty_string(self):
        assert clean_title("") == ""


class TestExtractGremiumName:
    """Test gremium name extraction from SUMMARY strings."""

    def test_plenarsitzung(self):
        assert extract_gremium_name("Plenarsitzung: 142. Sitzung") == "plenum"

    def test_ausschuesse_umbrella_excluded(self):
        """Generic "Ausschuesse" umbrella entries carry no committee-name signal
        and must be filtered out per DoD name-specificity (DD-006)."""
        assert extract_gremium_name("Fraktions- und Ausschusssitzungen: Ausschuesse") is None

    def test_finanzausschuss(self):
        assert extract_gremium_name("Fraktions- und Ausschusssitzungen: FinA") == "Finanzausschuss"

    def test_haushaltsberatungen(self):
        assert extract_gremium_name("Haushaltsberatungen: Finanzausschuss") == "Finanzausschuss"

    def test_fraktionen_excluded(self):
        assert extract_gremium_name("Fraktions- und Ausschusssitzungen: Fraktionen") is None

    def test_praesidium_excluded(self):
        assert extract_gremium_name("Prasidium: Sitzung des Prasidiums") is None

    def test_wahl_excluded(self):
        assert extract_gremium_name("Wahl: Oberbuergermeisterwahl") is None

    def test_unknown_prefix_excluded(self):
        assert extract_gremium_name("Something entirely different") is None


class TestParseIcsFeed:
    """Test ICS feed parsing and filtering."""

    def test_parses_included_events(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        # 9 total events, 4 excluded (Ausschuesse umbrella, Fraktionen,
        # Prasidium, Wahl) → 5 included
        assert len(events) == 5

    def test_event_fields(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        plenar = [e for e in events if e.uid == "evt-plenar-001@landtag-bw.de"]
        assert len(plenar) == 1
        evt = plenar[0]
        assert evt.summary == "Plenarsitzung: 142. Sitzung"
        assert evt.gremium_name == "plenum"
        assert evt.nummer == 142
        assert evt.dtstart == datetime(2026, 2, 25, 11, 0)
        assert evt.dtend == datetime(2026, 2, 25, 18, 0)

    def test_trailing_colon_summary_is_cleaned(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        evt = [e for e in events if e.uid == "evt-plenar-003@landtag-bw.de"]
        assert len(evt) == 1
        assert evt[0].summary == "Plenarsitzung"
        assert evt[0].gremium_name == "plenum"

    def test_finanzausschuss_event_nummer_is_zero(self, ics_bytes):
        """Non-plenary events have nummer=0 (no regex match in SUMMARY)."""
        events = parse_ics_feed(ics_bytes)
        fina = [e for e in events if e.gremium_name == "Finanzausschuss"]
        assert len(fina) >= 1
        assert fina[0].nummer == 0

    def test_excludes_ausschuesse_umbrella(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        uids = [e.uid for e in events]
        assert "evt-ausschuss-001@landtag-bw.de" not in uids

    def test_excludes_fraktionen(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        uids = [e.uid for e in events]
        assert "evt-fraktion-001@landtag-bw.de" not in uids

    def test_excludes_praesidium(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        uids = [e.uid for e in events]
        assert "evt-praesidium-001@landtag-bw.de" not in uids

    def test_excludes_wahl(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        uids = [e.uid for e in events]
        assert "evt-wahl-001@landtag-bw.de" not in uids

    def test_empty_calendar(self):
        ics = b"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Test//EN\nEND:VCALENDAR\n"
        assert parse_ics_feed(ics) == []


class TestGroupEventsByDate:
    """Test grouping parsed events by calendar date."""

    def test_groups_by_date(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        grouped = group_events_by_date(events)

        # 2026-02-24: FinA (the "Ausschuesse" umbrella is filtered, DD-006)
        assert len(grouped[date(2026, 2, 24)]) == 1
        # 2026-02-25: Plenar 142
        assert len(grouped[date(2026, 2, 25)]) == 1
        # 2026-02-26: Plenar 143
        assert len(grouped[date(2026, 2, 26)]) == 1
        # 2026-02-27: Plenar (trailing colon)
        assert len(grouped[date(2026, 2, 27)]) == 1
        # 2026-03-03: Haushaltsberatungen
        assert len(grouped[date(2026, 3, 3)]) == 1

    def test_returns_correct_dates(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        grouped = group_events_by_date(events)
        assert set(grouped.keys()) == {
            date(2026, 2, 24),
            date(2026, 2, 25),
            date(2026, 2, 26),
            date(2026, 2, 27),
            date(2026, 3, 3),
        }

    def test_empty_events(self):
        grouped = group_events_by_date([])
        assert grouped == {}
