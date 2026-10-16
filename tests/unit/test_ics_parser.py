"""Tests for the ICS parser module."""

import logging
from datetime import date, datetime
from pathlib import Path

import pytest

from bawue.ics_parser import (
    clean_title,
    extract_gremium_names,
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


class TestExtractGremiumNames:
    """Test gremium name extraction from SUMMARY strings."""

    def test_plenarsitzung(self):
        assert extract_gremium_names("Plenarsitzung: 142. Sitzung") == ["plenum"]

    def test_unnamed_committee_block_excluded(self):
        """The live feed's umbrella entry names no committee (DD-006 name-specificity)."""
        assert extract_gremium_names("Fraktions- und Ausschusssitzungen: ") == []

    def test_finanzausschuss(self):
        assert extract_gremium_names("Fraktions- und Ausschusssitzungen: FinA") == ["Ausschuss für Finanzen"]

    def test_haushaltsberatungen(self):
        assert extract_gremium_names("Haushaltsberatungen: FinA") == ["Ausschuss für Finanzen"]

    def test_fraktionen_excluded(self):
        assert extract_gremium_names("Fraktions- und Ausschusssitzungen: Fraktionen") == []

    def test_praesidium_excluded(self):
        assert extract_gremium_names("Präsidium: ") == []

    def test_wahl_excluded(self):
        assert extract_gremium_names("Wahl: Oberbuergermeisterwahl") == []

    def test_unknown_prefix_excluded(self):
        assert extract_gremium_names("Something entirely different") == []


class TestIssue117NamedCommittees:
    """Issue #117: one Gremium per committee abbreviation, named as in PARLIS (DD-006)."""

    def test_combined_entry_yields_each_committee(self):
        assert extract_gremium_names("Fraktions- und Ausschusssitzungen: FinA, BildungsA, VerkA") == [
            "Ausschuss für Finanzen",
            "Ausschuss für Kultus",
            "Ausschuss für Verkehr",
        ]

    @pytest.mark.parametrize(
        "suffix",
        ["FinA, BildungsA, VerkA", "InnenA, WissA, WirtA, LandwA", "SozA, LaWoA, EuA", "StändA, UmEnA", "PetA"],
    )
    def test_live_feed_abbreviations_are_known(self, suffix, caplog):
        """Every abbreviation in the feed of 16.10.2026."""
        with caplog.at_level(logging.WARNING, logger="bawue.ics_parser"):
            names = extract_gremium_names(f"Fraktions- und Ausschusssitzungen: {suffix}")

        assert len(names) == len(suffix.split(","))
        assert not caplog.records

    @pytest.mark.parametrize(
        ("summary", "expected"),
        [
            ("Fraktions- und Ausschusssitzungen: FinA, NeuA", ["Ausschuss für Finanzen"]),
            ("Fraktions- und Ausschusssitzungen: Ausschuesse", []),
            ("Haushaltsberatungen: Finanzausschuss", []),
        ],
    )
    def test_unknown_abbreviation_is_skipped_and_logged(self, summary, expected, caplog):
        with caplog.at_level(logging.WARNING, logger="bawue.ics_parser"):
            assert extract_gremium_names(summary) == expected

        assert "Unknown committee" in caplog.text

    def test_parse_splits_combined_event(self):
        ics = (
            b"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:evt-1\nDTSTART:20270121T140000\n"
            b"DTEND:20270121T170000\nSUMMARY:Fraktions- und Ausschusssitzungen: FinA, BildungsA\nEND:VEVENT\n"
            b"END:VCALENDAR\n"
        )
        events = parse_ics_feed(ics)

        assert [e.gremium_name for e in events] == ["Ausschuss für Finanzen", "Ausschuss für Kultus"]
        assert {(e.uid, e.dtstart) for e in events} == {("evt-1", datetime(2027, 1, 21, 14, 0))}


class TestParseIcsFeed:
    """Test ICS feed parsing and filtering."""

    def test_parses_included_events(self, ics_bytes):
        events = parse_ics_feed(ics_bytes)
        # 9 total events, 4 excluded (unnamed committee block, Fraktionen,
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
        fina = [e for e in events if e.gremium_name == "Ausschuss für Finanzen"]
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

        # 2026-02-24: FinA (the unnamed committee block is filtered, DD-006)
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
