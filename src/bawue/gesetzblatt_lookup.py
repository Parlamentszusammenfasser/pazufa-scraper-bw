"""Gesetzblatt ``(Jahr, Nr.) → Publikationsdatum`` lookup (DD-047), and its Inkrafttreten (DD-071).

PARLIS dates a Gesetzblatt Fundstelle by the *Ausfertigung* ("Gesetz vom …"),
not by the day the Gesetzblatt was actually issued — the `postparl-gsblt` station
therefore carries the wrong date (issue #9). The real Ausgabedatum exists only in
the Gesetzblatt itself, which this module resolves on demand from the citation
already present in the Fundstelle.

Lookups are cached (including misses) because one Gesetzblatt issue is routinely
cited by several Vorgänge — GBl 2023 Nr. 21 alone is referenced by five WP17
laws — and each would otherwise re-fetch the same detail page.
"""

import asyncio
import logging
import tempfile
from datetime import UTC, datetime, time
from pathlib import Path

from bawue.gesetzblatt_client import GesetzblattClient
from bawue.gesetzblatt_parser import RawGesetzblattDetail, parse_detail, parse_inkrafttreten
from bawue.pdf_text import extract_pdf_text

logger = logging.getLogger(__name__)


class GesetzblattUnavailable(Exception):
    """The Gesetzblatt could not be fetched: worth a retry next cycle (DD-071)."""


def parse_german_date(value: str | None) -> datetime | None:
    """Parse ``DD.MM.YYYY`` into a UTC datetime, or None if unparseable."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y").replace(tzinfo=UTC)
    except ValueError:
        return None


class GesetzblattDateLookup:
    """Resolves a Gesetzblatt citation to its publication date."""

    def __init__(self, client: GesetzblattClient) -> None:
        self._client = client
        self._cache: dict[tuple[int, int], RawGesetzblattDetail | None] = {}
        self._kraft: dict[tuple[int, int], datetime | None] = {}
        self._unavailable: set[tuple[int, int]] = set()

    async def publikationsdatum(self, jahr: int, nummer: int) -> datetime | None:
        """Return the Ausgabedatum of Gesetzblatt ``jahr`` Nr. ``nummer``.

        None when the entry cannot be resolved — the electronic Gesetzblatt only
        goes back to 2024, so older citations (and any fetch/parse failure) simply
        leave the caller's PARLIS date in place.
        """
        detail = await self._detail(jahr, nummer)
        return parse_german_date(detail.publikationsdatum) if detail else None

    async def pdf_url(self, jahr: int, nummer: int) -> str | None:
        """Official PDF of the entry: the Landtag's copy is incomplete (2026 Nr. 81: 404). None before 2024."""
        detail = await self._detail(jahr, nummer)
        return detail.pdf_url if detail else None

    async def inkrafttreten(self, jahr: int, nummer: int) -> datetime | None:
        """Latest date of the law's Inkrafttreten clause: the whole law is in force (DD-071).

        None if the entry, its PDF or the clause cannot be read.
        """
        key = (jahr, nummer)
        if key not in self._kraft:
            self._kraft[key] = await self._read_inkrafttreten(jahr, nummer)
        if key in self._unavailable:
            raise GesetzblattUnavailable(f"Gesetzblatt {jahr} Nr. {nummer} unavailable")
        return self._kraft[key]

    async def _read_inkrafttreten(self, jahr: int, nummer: int) -> datetime | None:
        detail = await self._detail(jahr, nummer)
        verkuendung = await self.publikationsdatum(jahr, nummer)
        if detail is None or detail.pdf_url is None or verkuendung is None:
            return None
        try:
            content = await asyncio.to_thread(self._client.fetch_pdf, detail.pdf_url)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
            pdf_path = Path(tmp.name)
            try:
                text, _hash = await extract_pdf_text(pdf_path)
            finally:
                pdf_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Gesetzblatt %d Nr. %d: PDF unreadable (%s)", jahr, nummer, exc)
            self._unavailable.add((jahr, nummer))
            return None
        dates = parse_inkrafttreten(text, verkuendung.date())
        if not dates:
            logger.warning(
                "Gesetzblatt %d Nr. %d: Inkrafttreten clause not understood, no postparl-kraft", jahr, nummer
            )
            return None
        return datetime.combine(max(dates), time(), tzinfo=UTC)

    async def _detail(self, jahr: int, nummer: int) -> RawGesetzblattDetail | None:
        key = (jahr, nummer)
        if key not in self._cache:
            self._cache[key] = await asyncio.to_thread(self._fetch, jahr, nummer)
        return self._cache[key]

    def _fetch(self, jahr: int, nummer: int) -> RawGesetzblattDetail | None:
        try:
            html = self._client.fetch_detail_for(jahr, nummer)
        except Exception as exc:  # network error, 404 for a pre-2024 citation, …
            logger.info("No Gesetzblatt entry for %d Nr. %d (%s)", jahr, nummer, exc)
            if getattr(getattr(exc, "response", None), "status_code", None) != 404:
                self._unavailable.add((jahr, nummer))
            return None
        try:
            detail = parse_detail(html, self._client.base_url)
        except Exception as exc:
            logger.warning("Could not parse Gesetzblatt %d Nr. %d: %s", jahr, nummer, exc)
            return None
        if parse_german_date(detail.publikationsdatum) is None:
            logger.warning(
                "Gesetzblatt %d Nr. %d has no parseable Publikationsdatum (%r)",
                jahr,
                nummer,
                detail.publikationsdatum,
            )
        return detail
