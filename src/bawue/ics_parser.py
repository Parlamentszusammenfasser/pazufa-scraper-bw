"""Stateless ICS calendar parsing and event filtering for BaWue Sitzungen."""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from icalendar import Calendar


@dataclass
class ParsedEvent:
    """A parsed and filtered ICS calendar event."""

    uid: str
    summary: str
    dtstart: datetime
    dtend: datetime
    gremium_name: str
    nummer: int = 0


def extract_session_number(summary: str) -> int:
    """Extract session number from ICS SUMMARY. Returns 0 if not present."""
    match = re.search(r"(\d+)\.\s*Sitzung", summary)
    return int(match.group(1)) if match else 0


def extract_gremium_name(summary: str) -> str | None:
    """Extract gremium name from an ICS SUMMARY string.

    Returns the gremium name if the event should be included, or None to filter it out.
    """
    if summary.startswith("Plenarsitzung:"):
        return "Plenum"

    if summary.startswith("Fraktions- und Ausschusssitzungen:"):
        suffix = summary.split(": ", 1)[1] if ": " in summary else ""
        if suffix == "Ausschuesse":
            return "Ausschusssitzungen"
        if suffix == "FinA":
            return "Finanzausschuss"
        # Fraktionen and other faction-only entries are excluded
        return None

    if summary.startswith("Haushaltsberatungen:"):
        return summary.split(": ", 1)[1] if ": " in summary else None

    # Prasidium, Wahl, and unknown prefixes are excluded
    return None


def parse_ics_feed(ics_data: bytes) -> list[ParsedEvent]:
    """Parse an ICS calendar feed and return filtered events."""
    cal = Calendar.from_ical(ics_data)
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", ""))
        gremium_name = extract_gremium_name(summary)
        if gremium_name is None:
            continue

        dtstart = component.get("DTSTART").dt
        dtend = component.get("DTEND").dt

        # Ensure we have datetime, not date
        if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
            dtstart = datetime(dtstart.year, dtstart.month, dtstart.day)
        if isinstance(dtend, date) and not isinstance(dtend, datetime):
            dtend = datetime(dtend.year, dtend.month, dtend.day)

        events.append(
            ParsedEvent(
                uid=str(component.get("UID", "")),
                summary=summary,
                dtstart=dtstart,
                dtend=dtend,
                gremium_name=gremium_name,
                nummer=extract_session_number(summary),
            )
        )

    return events


def group_events_by_date(events: list[ParsedEvent]) -> dict[date, list[ParsedEvent]]:
    """Group parsed events by their calendar date (from dtstart)."""
    grouped: dict[date, list[ParsedEvent]] = defaultdict(list)
    for event in events:
        event_date = event.dtstart.date() if isinstance(event.dtstart, datetime) else event.dtstart
        grouped[event_date].append(event)
    return dict(grouped)
