"""Gesetzblatt ``(Jahr, Nr.) → Publikationsdatum`` lookup (DD-047).

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
from datetime import UTC, datetime

from bawue.gesetzblatt_client import GesetzblattClient
from bawue.gesetzblatt_parser import parse_detail

logger = logging.getLogger(__name__)


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
        self._cache: dict[tuple[int, int], datetime | None] = {}

    async def publikationsdatum(self, jahr: int, nummer: int) -> datetime | None:
        """Return the Ausgabedatum of Gesetzblatt ``jahr`` Nr. ``nummer``.

        None when the entry cannot be resolved — the electronic Gesetzblatt only
        goes back to 2024, so older citations (and any fetch/parse failure) simply
        leave the caller's PARLIS date in place.
        """
        key = (jahr, nummer)
        if key in self._cache:
            return self._cache[key]
        result = await asyncio.to_thread(self._fetch, jahr, nummer)
        self._cache[key] = result
        return result

    def _fetch(self, jahr: int, nummer: int) -> datetime | None:
        try:
            html = self._client.fetch_detail_for(jahr, nummer)
        except Exception as exc:  # network error, 404 for a pre-2024 citation, …
            logger.info("No Gesetzblatt entry for %d Nr. %d (%s)", jahr, nummer, exc)
            return None
        try:
            detail = parse_detail(html, self._client.base_url)
        except Exception as exc:
            logger.warning("Could not parse Gesetzblatt %d Nr. %d: %s", jahr, nummer, exc)
            return None
        zp = parse_german_date(detail.publikationsdatum)
        if zp is None:
            logger.warning(
                "Gesetzblatt %d Nr. %d has no parseable Publikationsdatum (%r)",
                jahr,
                nummer,
                detail.publikationsdatum,
            )
        return zp
