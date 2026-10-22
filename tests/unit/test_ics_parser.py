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

    @pytest.mark.parametrize(
        ("summary", "nummer"),
        [
            ("Plenarsitzung: 142. Sitzung", 142),
            ("Plenarsitzung: 143. Sitzung", 143),
            ("Fraktions- und Ausschusssitzungen: Ausschuesse", 0),
            ("Fraktions- und Ausschusssitzungen: FinA", 0),
            ("Haushaltsberatungen: Finanzausschuss", 0),
        ],
    )
    def test_extract_session_number(self, summary, nummer):
        assert extract_session_number(summary) == nummer


class TestCleanTitle:
    """Test title cleaning to remove trailing colon artifacts."""

    @pytest.mark.parametrize(
        ("title", "cleaned"),
        [
            ("Plenarsitzung: ", "Plenarsitzung"),
            ("Plenarsitzung:", "Plenarsitzung"),
            ("Plenarsitzung: 142. Sitzung", "Plenarsitzung: 142. Sitzung"),
            ("Fraktions- und Ausschusssitzungen: Ausschuesse", "Fraktions- und Ausschusssitzungen: Ausschuesse"),
            ("", ""),
        ],
    )
    def test_clean_title(self, title, cleaned):
        assert clean_title(title) == cleaned


class TestExtractGremiumNames:
    """Test gremium name extraction from SUMMARY strings."""

    @pytest.mark.parametrize(
        ("summary", "names"),
        [
            ("Plenarsitzung: 142. Sitzung", ["plenum"]),
            ("Fraktions- und Ausschusssitzungen: FinA", ["Ausschuss für Finanzen"]),
            ("Haushaltsberatungen: FinA", ["Ausschuss für Finanzen"]),
        ],
    )
    def test_extract_gremium_names(self, summary, names):
        assert extract_gremium_names(summary) == names

    @pytest.mark.parametrize(
        "summary",
        [
            # The live feed's umbrella entry names no committee (DD-006 name-specificity), 28 in the feed.
            "Fraktions- und Ausschusssitzungen: ",
            "Fraktions- und Ausschusssitzungen: Fraktionen",  # 49 in the feed
            "Präsidium: ",
            "Wahl: Oberbuergermeisterwahl",
            "Something entirely different",
        ],
    )
    def test_excluded_silently(self, summary, caplog):
        with caplog.at_level(logging.WARNING, logger="bawue.ics_parser"):
            assert extract_gremium_names(summary) == []

        assert not caplog.records  # not "Unknown committee"


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

    def test_repeated_abbreviation_yields_one_gremium(self):
        """Two Sitzungen with one UID, date and Gremium would share an api_id (UNIQUE in the backend)."""
        assert extract_gremium_names("Fraktions- und Ausschusssitzungen: FinA, FinA") == ["Ausschuss für Finanzen"]

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
        # 9 total events, 4 excluded (unnamed committee block, Fraktionen, Präsidium, Wahl) → 5 included
        assert len(events) == 5
        uids = {e.uid for e in events}
        for excluded in ["ausschuss-001", "fraktion-001", "praesidium-001", "wahl-001"]:
            assert f"evt-{excluded}@landtag-bw.de" not in uids

    def test_event_fields(self, ics_bytes):
        events = {e.uid: e for e in parse_ics_feed(ics_bytes)}
        evt = events["evt-plenar-001@landtag-bw.de"]
        assert evt.summary == "Plenarsitzung: 142. Sitzung"
        assert evt.gremium_name == "plenum"
        assert evt.nummer == 142
        assert evt.dtstart == datetime(2026, 2, 25, 11, 0)
        assert evt.dtend == datetime(2026, 2, 25, 18, 0)
        # A trailing-colon summary is cleaned.
        assert events["evt-plenar-003@landtag-bw.de"].summary == "Plenarsitzung"
        assert events["evt-plenar-003@landtag-bw.de"].gremium_name == "plenum"
        # Non-plenary events have nummer=0 (no regex match in SUMMARY).
        fina = [e for e in events.values() if e.gremium_name == "Ausschuss für Finanzen"]
        assert fina and fina[0].nummer == 0

    def test_empty_calendar(self):
        ics = b"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Test//EN\nEND:VCALENDAR\n"
        assert parse_ics_feed(ics) == []


class TestGroupEventsByDate:
    """Test grouping parsed events by calendar date."""

    def test_groups_by_date(self, ics_bytes):
        grouped = group_events_by_date(parse_ics_feed(ics_bytes))

        assert {day: len(events) for day, events in grouped.items()} == {
            date(2026, 2, 24): 1,  # FinA (the unnamed committee block is filtered, DD-006)
            date(2026, 2, 25): 1,  # Plenar 142
            date(2026, 2, 26): 1,  # Plenar 143
            date(2026, 2, 27): 1,  # Plenar (trailing colon)
            date(2026, 3, 3): 1,  # Haushaltsberatungen
        }

    def test_empty_events(self):
        grouped = group_events_by_date([])
        assert grouped == {}
