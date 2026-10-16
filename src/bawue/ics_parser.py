"""Stateless ICS calendar parsing and event filtering for BaWue Sitzungen."""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from icalendar import Calendar

from bawue.types import ReservedGremium

logger = logging.getLogger(__name__)

# ICS committee abbreviation → name as on the committee's landtag-bw.de page, which PARLIS
# uses too: Kalender and Vorgang Stationen then share one Gremium (DD-006, issue #117).
COMMITTEE_NAMES = {
    "BildungsA": "Ausschuss für Kultus",
    "EuA": "Ausschuss für Europa und Internationales",
    "FinA": "Ausschuss für Finanzen",
    "InnenA": "Ausschuss des Inneren und für Digitalisierung",
    "LandwA": "Ausschuss für Ländlichen Raum, Landwirtschaft und Heimat",
    "LaWoA": "Ausschuss für Landesentwicklung und Wohnen",
    "PetA": "Petitionsausschuss",
    "SozA": "Ausschuss für Soziales, Arbeit und Gesundheit",
    "StändA": "Ständiger Ausschuss",
    "UmEnA": "Ausschuss für Umwelt, Klima und Energiewirtschaft",
    "VerkA": "Ausschuss für Verkehr",
    "WirtA": "Ausschuss für Wirtschaft, Handwerk und Tourismus",
    "WissA": "Ausschuss für Wissenschaft, Forschung und Kunst",
}


@dataclass
class ParsedEvent:
    """A parsed and filtered ICS calendar event, one per Gremium it names."""

    uid: str
    summary: str
    dtstart: datetime
    dtend: datetime
    gremium_name: str
    nummer: int = 0


def clean_title(summary: str) -> str:
    """Strip trailing colon and whitespace from ICS summaries.

    The BaWue ICS feed sometimes has entries like "Plenarsitzung: " with nothing
    after the colon. This cleans them to "Plenarsitzung".
    """
    return summary.rstrip().rstrip(":").rstrip()


def extract_session_number(summary: str) -> int:
    """Extract session number from ICS SUMMARY. Returns 0 if not present."""
    match = re.search(r"(\d+)\.\s*Sitzung", summary)
    return int(match.group(1)) if match else 0


def extract_gremium_names(summary: str) -> list[str]:
    """Gremium names of an ICS SUMMARY, one per committee; empty if the event is filtered out (DD-006)."""
    if summary.startswith("Plenarsitzung:"):
        return [ReservedGremium.PLENUM]

    # Präsidium, Wahl, Gedenktag and unknown prefixes are excluded
    if not summary.startswith(("Fraktions- und Ausschusssitzungen:", "Haushaltsberatungen:")):
        return []

    names = []
    for abbr in summary.split(":", 1)[1].split(","):
        abbr = abbr.strip()
        # Faction meetings, or a block that names no committee
        if abbr in ("", "Fraktionen"):
            continue
        if abbr not in COMMITTEE_NAMES:
            # Fail loud: a new committee needs its name added, not a guess.
            logger.warning("Unknown committee %r in ICS summary %r, skipping it", abbr, summary)
            continue
        names.append(COMMITTEE_NAMES[abbr])
    # A repeated abbreviation would give two Sitzungen the same api_id (UNIQUE in the backend)
    return list(dict.fromkeys(names))


def parse_ics_feed(ics_data: bytes) -> list[ParsedEvent]:
    """Parse an ICS calendar feed and return filtered events."""
    cal = Calendar.from_ical(ics_data)
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("SUMMARY", ""))
        gremium_names = extract_gremium_names(summary)
        if not gremium_names:
            continue

        dtstart = component.get("DTSTART").dt
        dtend = component.get("DTEND").dt

        # Ensure we have datetime, not date
        if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
            dtstart = datetime(dtstart.year, dtstart.month, dtstart.day)
        if isinstance(dtend, date) and not isinstance(dtend, datetime):
            dtend = datetime(dtend.year, dtend.month, dtend.day)

        events.extend(
            ParsedEvent(
                uid=str(component.get("UID", "")),
                summary=clean_title(summary),
                dtstart=dtstart,
                dtend=dtend,
                gremium_name=gremium_name,
                nummer=extract_session_number(summary),
            )
            for gremium_name in gremium_names
        )

    return events


def group_events_by_date(events: list[ParsedEvent]) -> dict[date, list[ParsedEvent]]:
    """Group parsed events by their calendar date (from dtstart)."""
    grouped: dict[date, list[ParsedEvent]] = defaultdict(list)
    for event in events:
        event_date = event.dtstart.date() if isinstance(event.dtstart, datetime) else event.dtstart
        grouped[event_date].append(event)
    return dict(grouped)
