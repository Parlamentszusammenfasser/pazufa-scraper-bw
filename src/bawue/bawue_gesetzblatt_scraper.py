"""BaWue Gesetzblatt scraper: VorgangsScraper subclass for the Gesetzblatt Baden-Württemberg.

Iterates all entries from start_year to the current year by binary-searching
the highest entry number per year (detail/YYYY-N URL pattern) and fetching each
detail page on demand. Only entries of type ``Gesetz`` become Vorgänge — Verordnungen,
Bekanntmachungen, and Berichtigungen are filtered out.

The dedicated source solves the PARLIS date bug (issue #9): PARLIS only carries the
Gesetzesbeschluss/Ausfertigung date in its Fundstellen, so the `postparl-gsblt`
station built from PARLIS is dated with the wrong day. Here the station's ``zp_start``
is the Gesetzblatt **Publikationsdatum** (the true Ausgabedatum) and the document's
``zp_referenz`` is the Ausfertigungsdatum — see DD-044.

The MVP emits standalone Gesetzblatt-Vorgänge; cross-source merging with PARLIS
Vorgänge (via ``VgIdent(typ="initdrucks")``) is deferred because the Drucksachennummer
lives only in the PDF text and because emitting `initdrucks` on the PARLIS side is
currently gated off (DD-028/DD-034). See DD-044 and docs/gesetzblatt_scraping.md.
"""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import aiohttp

from bawue.api import build_client
from bawue.config import BawueConfig
from bawue.config_loader import load_toml_section
from bawue.gesetzblatt_client import BASE_URL, GesetzblattClient
from bawue.gesetzblatt_parser import RawGesetzblattDetail, parse_detail
from bawue.notifications import send_mattermost_summary
from bawue.pipeline import VorgangsScraper
from bawue.rate_limiter import create_upload_limiter
from bawue.types import (
    TODO_MARKER,
    Autor,
    Doktyp,
    Dokument,
    Gremium,
    Parlament,
    ReservedGremium,
    Station,
    Stationstyp,
    Vorgang,
    Vorgangstyp,
    todo_if_blank,
)
from bawue.upload_throttle import upload_vorgang

logger = logging.getLogger(__name__)

DEFAULT_WAHLPERIODE = 17
DEFAULT_GSBLT_DELAY = 1.0
DEFAULT_GSBLT_START_YEAR = 2024

_GSBLT_ACCEPTED_TYPES: frozenset[str] = frozenset({"Gesetz"})

_LISTING_KEY = "gsblt-default"
_DETAIL_PATH = "/de/service/gesetze-und-verordnungen/gesetzblatt/detail"


def _current_year() -> int:
    return datetime.now(UTC).year


class BawueGesetzblattScraper(VorgangsScraper):
    """Scrapes the Gesetzblatt Baden-Württemberg via binary-searched detail pages."""

    def __init__(self, config: BawueConfig, session: aiohttp.ClientSession) -> None:
        gsblt_config = load_toml_section(config, "gesetzblatt")
        self._wahlperiode = gsblt_config.get("wahlperiode", DEFAULT_WAHLPERIODE)
        self._start_year: int = gsblt_config.get("start-year", DEFAULT_GSBLT_START_YEAR)
        delay = gsblt_config.get("request-delay-s", DEFAULT_GSBLT_DELAY)

        listing_urls = [_LISTING_KEY]
        super().__init__(config, uuid.UUID(config.collector_id), listing_urls, session)

        self._client = GesetzblattClient(request_delay_s=delay)
        self._api_client = build_client(config.database_url, config.api_key)
        self._upload_limiter = create_upload_limiter()

        self._published: int = 0
        self._failed: int = 0
        self._skipped: int = 0

    async def run(self) -> None:
        start = time.monotonic()
        try:
            await super().run()
        finally:
            duration = time.monotonic() - start
            logger.info("Completed in %.1fs", duration)
            lines = _print_gesetzblatt_summary(self._published, self._skipped, self._failed, duration)
            send_mattermost_summary(self.config, "BaWue Gesetzblatt Run Summary", lines)

    async def send_result(self, item: Vorgang) -> Vorgang | None:
        outcome = upload_vorgang(
            self._api_client,
            self.scraper_id,
            self._upload_limiter,
            item,
            dry_run=self.config.dry_run,
            log_item=self.log_item,
        )
        if outcome.vorgang is not None:
            self._published += 1
        else:
            self._failed += 1
        return outcome.vorgang

    async def listing_page_extractor(self, listing_key: str) -> list[str]:
        """Enumerate all Gesetzblatt entries from start_year to the current year.

        Binary-searches the maximum entry number per year via HEAD requests,
        then returns one key per entry in the form "YYYY-N".
        """
        end_year = _current_year()
        keys: list[str] = []
        for year in range(self._start_year, end_year + 1):
            max_num = await asyncio.to_thread(self._client.find_max_number, year)
            logger.info("Gesetzblatt year %d: %d entries", year, max_num)
            for n in range(1, max_num + 1):
                keys.append(f"{year}-{n}")
        logger.info("Total Gesetzblatt keys: %d", len(keys))
        return keys

    async def item_extractor(self, key: str) -> Vorgang | None:
        """Fetch a Gesetzblatt detail page on demand and build a Vorgang.

        ``jahr``/``nummer`` are taken from the authoritative URL key ("YYYY-N")
        rather than the parsed page: the parser defaults both to 0 when a field
        is missing/renamed, which would collapse distinct laws onto one identity
        (``gsblt-YYYY-0``). The key is what this scraper enumerated, so it is the
        stable source of identity.
        """
        url = f"{BASE_URL}{_DETAIL_PATH}/{key}"
        raw_html = await asyncio.to_thread(self._client.fetch_detail, url)
        detail = parse_detail(raw_html, BASE_URL)
        year_str, _, num_str = key.partition("-")
        detail.jahr = int(year_str)
        detail.nummer = int(num_str)
        return await self._build_vorgang(detail)

    async def _build_vorgang(self, detail: RawGesetzblattDetail) -> Vorgang | None:
        """Convert parsed Gesetzblatt metadata into a framework Vorgang.

        Returns None when the Gbl-Typ is not in ``_GSBLT_ACCEPTED_TYPES``,
        when no PDF link is available, or when the publication date is unparseable.
        """
        if detail.typ not in _GSBLT_ACCEPTED_TYPES:
            logger.info(
                "Skipping Gesetzblatt %s-%s ('%s'): unsupported Gbl-Typ '%s'",
                detail.jahr,
                detail.nummer,
                detail.titel[:60],
                detail.typ,
            )
            self._skipped += 1
            return None

        if not detail.pdf_url:
            logger.warning(
                "Skipping Gesetzblatt %s-%s ('%s'): no PDF link found",
                detail.jahr,
                detail.nummer,
                detail.titel[:60],
            )
            self._skipped += 1
            return None

        zp_publik = _parse_german_date(detail.publikationsdatum)
        if zp_publik is None:
            logger.error(
                "Skipping Gesetzblatt %s-%s: unparseable publication date '%s'",
                detail.jahr,
                detail.nummer,
                detail.publikationsdatum,
            )
            self._skipped += 1
            return None
        zp_ausfertigung = _parse_german_date(detail.ausfertigungsdatum) or zp_publik

        slug = f"gsblt-{detail.jahr}-{detail.nummer}"
        api_id = uuid5(NAMESPACE_URL, slug)
        organisation = detail.federfuehrung or "Landesregierung"

        dok = Dokument(
            titel=todo_if_blank(detail.titel),
            volltext=TODO_MARKER,
            hash_=TODO_MARKER,
            typ=Doktyp.MITTEILUNG,
            zp_modifiziert=zp_publik,
            zp_referenz=zp_ausfertigung,
            link=detail.pdf_url,
            autoren=[Autor(organisation=organisation)],
        )

        # Vorgang-scoped stable station api_id (DD-028/DD-034). Without it the
        # backend matches this station against others by shared document hash —
        # and this scraper never enriches, so every document carries the constant
        # TODO_MARKER hash. Two Gesetze would then collide on that shared hash
        # (HTTP 500 rel_station_dokument_pkey). There is exactly one station per
        # Vorgang, so the slug alone scopes it; the date is deliberately left out
        # of the key so a corrected Publikationsdatum still re-matches the row.
        station_key = f"bawue-station-{slug}-{Stationstyp.POSTPARL_GSBLT.value}"
        station = Station(
            api_id=str(uuid5(NAMESPACE_URL, station_key)),
            typ=Stationstyp.POSTPARL_GSBLT,
            dokumente=[dok],
            zp_start=zp_publik,
            gremium=Gremium(
                parlament=Parlament.BW,
                name=ReservedGremium.GESETZESBLATT,
                wahlperiode=self._wahlperiode,
            ),
        )

        return Vorgang(
            api_id=str(api_id),
            titel=todo_if_blank(detail.titel),
            kurztitel=slug,
            typ=Vorgangstyp.GG_LAND_PARL,
            wahlperiode=self._wahlperiode,
            verfassungsaendernd=False,
            initiatoren=[Autor(organisation=organisation)],
            stationen=[station],
        )


def _parse_german_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y").replace(tzinfo=UTC)
    except ValueError:
        return None


def _print_gesetzblatt_summary(published: int, skipped: int, failed: int, duration: float) -> list[str]:
    discovered = published + skipped + failed
    lines = [
        f"Duration: {duration:.1f}s",
        f"Discovered:  {discovered}",
        f"Published:   {published}",
        f"Skipped:     {skipped}  (non-Gesetz Gbl-Typ or missing PDF)",
        f"Failed:      {failed}",
    ]
    print("=== BaWue Gesetzblatt Run Summary ===\n" + "\n".join(lines))
    return lines
