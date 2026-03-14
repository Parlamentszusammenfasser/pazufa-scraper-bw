"""BaWue Beteiligung scraper: VorgangsScraper subclass for Beteiligungsportal Baden-Württemberg."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import aiohttp
import toml
from collector.config import CollectorConfiguration
from collector.interface import VorgangsScraper
from openapi_client.models import (
    Autor,
    Doktyp,
    Dokument,
    Gremium,
    Parlament,
    Station,
    StationDokumenteInner,
    Stationstyp,
    VgIdent,
    VgIdentTyp,
    Vorgang,
    Vorgangstyp,
)

from bawue.beteiligung_client import BASE_URL, BeteiligungClient
from bawue.beteiligung_parser import (
    RawBeteiligungDetail,
    RawBeteiligungProcess,
    parse_process_detail,
)

logger = logging.getLogger(__name__)

DEFAULT_WAHLPERIODE = 17
DEFAULT_BETEILIGUNG_DELAY = 2.0


class BawueBeteiligungScraper(VorgangsScraper):
    """Scrapes pre-parliamentary draft laws from the Beteiligungsportal Baden-Württemberg.

    Auto-discovered by the framework when placed in the scrapers directory.
    """

    def __init__(self, config: CollectorConfiguration, session: aiohttp.ClientSession) -> None:
        beteiligung_config = self._load_config(config)
        self._wahlperiode = beteiligung_config.get("wahlperiode", DEFAULT_WAHLPERIODE)
        delay = beteiligung_config.get("request-delay-s", DEFAULT_BETEILIGUNG_DELAY)

        listing_urls = [f"lp-{self._wahlperiode}"]
        super().__init__(config, uuid.UUID(config.collector_id), listing_urls, session)

        self._client = BeteiligungClient(wahlperiode=self._wahlperiode, request_delay_s=delay)
        self._raw_cache: dict[str, RawBeteiligungProcess] = {}

        self._published: int = 0
        self._failed: int = 0
        self._skipped: int = 0

    @staticmethod
    def _load_config(config: CollectorConfiguration) -> dict:
        """Load [beteiligung] section from the collector config file."""
        config_file = getattr(config, "config_file", None)
        if config_file:
            try:
                loaded = toml.load(config_file)
                return loaded.get("beteiligung", {})
            except Exception:
                logger.warning("Could not load [beteiligung] section from config file: %s", config_file, exc_info=True)
        return {}

    async def run(self) -> None:
        start = time.monotonic()
        try:
            await super().run()
        finally:
            duration = time.monotonic() - start
            logger.info("Completed in %.1fs", duration)
            _print_beteiligung_summary(self._published, self._skipped, self._failed, duration)

    async def send_result(self, item: Vorgang) -> Vorgang | None:
        result = await super().send_result(item)
        if result is not None:
            self._published += 1
        else:
            self._failed += 1
        return result

    async def listing_page_extractor(self, lp_key: str) -> list[str]:
        """Fetch the process list and return slugs for each process."""
        processes = await asyncio.to_thread(self._client.fetch_process_list)
        logger.info("Found %d processes on Beteiligungsportal for %s", len(processes), lp_key)

        slugs = []
        for process in processes:
            self._raw_cache[process.slug] = process
            slugs.append(process.slug)

        return slugs

    async def item_extractor(self, slug: str) -> Vorgang | None:
        """Fetch detail page and build a Vorgang with preparl-regent station."""
        process = self._raw_cache.pop(slug, None)
        if process is None:
            logger.error("No cached process data for slug %s", slug)
            return None

        html = await asyncio.to_thread(self._client.fetch_process_detail, process.url)
        detail = parse_process_detail(html, BASE_URL)

        return self._build_vorgang(slug, detail)

    def _build_vorgang(self, slug: str, detail: RawBeteiligungDetail) -> Vorgang | None:
        """Convert parsed Beteiligungsportal data into a framework Vorgang model.

        Returns None if the detail page has no PDF links (non-legislative content).
        """
        if not detail.pdf_links:
            logger.info("Skipping '%s' — no Entwurf PDFs found", detail.title)
            self._skipped += 1
            return None

        api_id = uuid5(NAMESPACE_URL, f"beteiligung-{slug}")

        # Parse comment deadline as station timestamp
        zp_start = (
            datetime.strptime(detail.comment_deadline, "%d.%m.%Y").replace(tzinfo=UTC)
            if detail.comment_deadline
            else datetime.now(UTC)
        )

        # Build documents
        dokumente: list[StationDokumenteInner] = []
        for pdf in detail.pdf_links:
            dok = Dokument(
                titel=pdf["title"],
                volltext="",
                hash="",
                typ=Doktyp.PREPARL_MINUS_ENTWURF,
                zp_modifiziert=zp_start,
                zp_referenz=zp_start,
                link=pdf["url"],
                autoren=[Autor(organisation=detail.ministry)],
            )
            dokumente.append(StationDokumenteInner(dok))

        gremium = Gremium(parlament=Parlament.BW, name="Landesregierung", wahlperiode=self._wahlperiode)

        station = Station(
            typ=Stationstyp.PREPARL_MINUS_REGENT,
            dokumente=dokumente,
            zp_start=zp_start,
            gremium=gremium,
        )

        beteiligung_url = f"{BASE_URL}/de/mitmachen/lp-{self._wahlperiode}/{slug}"
        ids = [VgIdent(id=beteiligung_url, typ=VgIdentTyp.VORGNR)]

        return Vorgang(
            api_id=str(api_id),
            titel=detail.title,
            kurztitel=slug,
            typ=Vorgangstyp.GG_MINUS_LAND_MINUS_PARL,
            wahlperiode=self._wahlperiode,
            verfassungsaendernd=False,
            initiatoren=[Autor(organisation=detail.ministry)],
            stationen=[station],
            ids=ids,
        )


def _print_beteiligung_summary(published: int, skipped: int, failed: int, duration: float) -> None:
    discovered = published + skipped + failed
    lines = [
        "=== BaWue Beteiligung Run Summary ===",
        f"Duration: {duration:.1f}s",
        f"Discovered:  {discovered}",
        f"Published:   {published}",
        f"Skipped:     {skipped}  (no legislative PDFs)",
        f"Failed:      {failed}",
    ]
    print("\n".join(lines))
