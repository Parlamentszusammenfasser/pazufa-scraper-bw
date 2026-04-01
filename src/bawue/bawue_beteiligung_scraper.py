"""BaWue Beteiligung scraper: VorgangsScraper subclass for Beteiligungsportal Baden-Württemberg."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import aiohttp
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
    Vorgang,
    Vorgangstyp,
)

from bawue.beteiligung_client import BASE_URL, BeteiligungClient
from bawue.beteiligung_parser import (
    RawBeteiligungDetail,
    RawBeteiligungProcess,
    parse_process_detail,
)
from bawue.config_loader import load_toml_section
from bawue.rate_limiter import create_upload_limiter
from bawue.upload_throttle import upload_vorgang

logger = logging.getLogger(__name__)

DEFAULT_WAHLPERIODE = 17
DEFAULT_BETEILIGUNG_DELAY = 2.0


class BawueBeteiligungScraper(VorgangsScraper):
    """Scrapes pre-parliamentary draft laws from the Beteiligungsportal Baden-Württemberg.

    Auto-discovered by the framework when placed in the scrapers directory.
    """

    def __init__(self, config: CollectorConfiguration, session: aiohttp.ClientSession) -> None:
        beteiligung_config = load_toml_section(config, "beteiligung")
        self._wahlperiode = beteiligung_config.get("wahlperiode", DEFAULT_WAHLPERIODE)
        delay = beteiligung_config.get("request-delay-s", DEFAULT_BETEILIGUNG_DELAY)

        listing_urls = [f"lp-{self._wahlperiode}"]
        super().__init__(config, uuid.UUID(config.collector_id), listing_urls, session)

        self._client = BeteiligungClient(wahlperiode=self._wahlperiode, request_delay_s=delay)
        self._raw_cache: dict[str, RawBeteiligungProcess] = {}

        self._upload_limiter = create_upload_limiter()

        self._published: int = 0
        self._failed: int = 0
        self._skipped: int = 0

        # LLM document enrichment (optional, requires LLM_PROVIDER_KEY)
        self._llm_enabled = bool(getattr(config, "llm_provider_key", None))
        self._llm = None
        llm_config = load_toml_section(config, "llm")
        self._llm_model = config.llm_model
        self._llm_truncate_tokens = int(llm_config.get("truncate-tokens", 12000))
        if self._llm_enabled:
            from collector_core import LLMConnector

            self._llm = LLMConnector(
                model=config.llm_model,
                api_key=config.llm_provider_key,
                rate_limit_max_calls=5,
                rate_limit_window_seconds=60,
            )

    async def run(self) -> None:
        start = time.monotonic()
        try:
            await super().run()
        finally:
            duration = time.monotonic() - start
            logger.info("Completed in %.1fs", duration)
            _print_beteiligung_summary(self._published, self._skipped, self._failed, duration)

    async def send_result(self, item: Vorgang) -> Vorgang | None:
        result = upload_vorgang(
            self.config.oapiconfig,
            self.scraper_id,
            self._upload_limiter,
            item,
            dry_run=self.config.dry_run,
            log_item=self.log_item,
        )
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

        return await self._build_vorgang(slug, detail)

    async def _build_vorgang(self, slug: str, detail: RawBeteiligungDetail) -> Vorgang | None:
        """Convert parsed Beteiligungsportal data into a framework Vorgang model.

        Returns None if the detail page has no PDF links (non-legislative content).
        """
        if not detail.pdf_links:
            logger.info("Skipping '%s' — no Entwurf PDFs found", detail.title)
            self._skipped += 1
            return None

        api_id = uuid5(NAMESPACE_URL, f"beteiligung-{slug}")

        # Parse comment deadline as station timestamp
        if not detail.comment_deadline:
            logger.error("No comment_deadline for '%s', skipping Vorgang", slug)
            self._skipped += 1
            return None
        try:
            zp_start = datetime.strptime(detail.comment_deadline, "%d.%m.%Y").replace(tzinfo=UTC)
        except ValueError:
            logger.error(
                "Unparseable comment_deadline '%s' for '%s', skipping Vorgang",
                detail.comment_deadline,
                slug,
            )
            self._skipped += 1
            return None

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

            if self._llm_enabled and self._llm is not None:
                try:
                    from bawue.bawue_dok import enrich_dokument

                    dok = await enrich_dokument(
                        self.session,
                        self._llm,
                        dok,
                        model=self._llm_model,
                        max_tokens=self._llm_truncate_tokens,
                    )
                except Exception:
                    logger.warning("Document enrichment failed for %s", pdf["url"])

            dokumente.append(StationDokumenteInner(dok))

        gremium = Gremium(parlament=Parlament.BW, name="Landesregierung", wahlperiode=self._wahlperiode)

        station = Station(
            typ=Stationstyp.PREPARL_MINUS_REGENT,
            dokumente=dokumente,
            zp_start=zp_start,
            gremium=gremium,
        )

        beteiligung_url = f"{BASE_URL}/de/mitmachen/lp-{self._wahlperiode}/{slug}"
        ids = [VgIdent(id=beteiligung_url, typ="vorgnr")]

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
