"""Plenum agendas (Tagesordnungen) from landtag-bw.de → TOPs (GitHub issue #66, DD-070).

The page links the agenda PDF of the next Plenarsitzung only; committee agendas are not
published (DD-064). The file name carries the date and the session number.
"""

import re
from dataclasses import dataclass
from datetime import date

from bawue.bawue_dok import normalize_volltext

TAGESORDNUNGEN_URL = "https://www.landtag-bw.de/de/aktuelles/tagesordnungen"
_SITE = "https://www.landtag-bw.de"

_AGENDA_LINK_RE = re.compile(r"/resource/blob/[^\"'\s]+/(\d{4}-\d{2}-\d{2})_(\d+)_Plenarsitzung\.pdf")
_ITEM_START_RE = re.compile(r"^\s*(\d{1,3})\.(?:\s+(.*))?$")
# "18/........" (not printed yet) has no number and is skipped.
_DRUCKSACHE_RE = re.compile(r"Drucksache\s+(\d+/\d+)")
# A reading of a Gesetzentwurf: only these have a Vorgang (DD-070).
_GESETZENTWURF_TOP_RE = re.compile(r"(?:Erste|Zweite|Dritte) Beratung (?:des|der) Gesetzentw")
_DRUCKSACHE_LINE_RE = re.compile(r"[–-]\s*Drucksache\b")  # noqa: RUF001 - the agenda uses an en dash


@dataclass(frozen=True)
class ParsedTop:
    nummer: int
    titel: str
    # Only for Gesetzentwurf TOPs: other Vorgangstypen are not scraped, so their
    # Dokumente would have no Vorgang in the backend (DD-070).
    drucksachen: tuple[str, ...]


def agenda_links(html: str) -> list[tuple[date, int, str]]:
    """(Sitzungsdatum, Sitzungsnummer, PDF URL) of each Plenarsitzung agenda linked on the page."""
    links = dict.fromkeys(
        (date.fromisoformat(m.group(1)), int(m.group(2)), _SITE + m.group(0)) for m in _AGENDA_LINK_RE.finditer(html)
    )
    return list(links)


def parse_tops(text: str) -> list[ParsedTop]:
    """Split the agenda text into numbered items.

    An item starts at a line "N." or "N. <text>" with N the next expected number, so a
    date like "3. Oktober" inside an item never starts one. The title runs from the item
    start to its first "Drucksache" line, else it is the first line: what follows are
    notes and the speaking-time column, and only macOS extraction separates them by a
    blank line.
    """
    items: list[list[str]] = []
    for line in text.splitlines():
        m = _ITEM_START_RE.match(line)
        if m and int(m.group(1)) == len(items) + 1:
            items.append([m.group(2) or ""])
        elif items:
            items[-1].append(line)

    tops = []
    for nummer, lines in enumerate(items, start=1):
        block = "\n".join(lines)
        content = [line.strip() for line in lines if line.strip()]
        end = next((i for i, line in enumerate(content) if _DRUCKSACHE_LINE_RE.match(line)), 0)
        titel = normalize_volltext(" ".join(content[: end + 1]))
        drucksachen = tuple(dict.fromkeys(_DRUCKSACHE_RE.findall(block))) if _GESETZENTWURF_TOP_RE.match(titel) else ()
        tops.append(ParsedTop(nummer, titel, drucksachen))
    return tops
