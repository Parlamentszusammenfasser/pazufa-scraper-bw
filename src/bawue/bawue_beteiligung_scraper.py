"""BaWue Beteiligung scraper: VorgangsScraper subclass for Beteiligungsportal Baden-Württemberg."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import aiohttp

from bawue.api import build_client
from bawue.bawue_dok import LLMMetrics, clear_hash_cache
from bawue.beteiligung_client import BASE_URL, BeteiligungClient
from bawue.beteiligung_parser import (
    RawBeteiligungDetail,
    RawBeteiligungProcess,
    parse_process_detail,
)
from bawue.config import BawueConfig
from bawue.config_loader import load_toml_section
from bawue.notifications import send_mattermost_summary
from bawue.pipeline import VorgangsScraper
from bawue.rate_limiter import create_upload_limiter
from bawue.run_report import FailedItem, format_duration, format_failed_section
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
    VgIdent,
    Vorgang,
    Vorgangstyp,
    canonicalize_organisation,
    is_verfassungsaendernd,
    todo_if_blank,
)
from bawue.upload_throttle import upload_vorgang

logger = logging.getLogger(__name__)

DEFAULT_WAHLPERIODE = 17
DEFAULT_BETEILIGUNG_DELAY = 2.0


class BawueBeteiligungScraper(VorgangsScraper):
    """Scrapes pre-parliamentary draft laws from the Beteiligungsportal Baden-Württemberg.

    Auto-discovered by the framework when placed in the scrapers directory.
    """

    def __init__(self, config: BawueConfig, session: aiohttp.ClientSession) -> None:
        beteiligung_config = load_toml_section(config, "beteiligung")
        self._wahlperiode = beteiligung_config.get("wahlperiode", DEFAULT_WAHLPERIODE)
        delay = beteiligung_config.get("request-delay-s", DEFAULT_BETEILIGUNG_DELAY)

        listing_urls = [f"lp-{self._wahlperiode}"]
        super().__init__(config, uuid.UUID(config.collector_id), listing_urls, session)

        self._client = BeteiligungClient(wahlperiode=self._wahlperiode, request_delay_s=delay)
        self._api_client = build_client(config.database_url, config.api_key)
        self._raw_cache: dict[str, RawBeteiligungProcess] = {}

        self._upload_limiter = create_upload_limiter()

        self._published: int = 0
        self._failed: int = 0
        self._skipped: int = 0
        self._failed_items: list[FailedItem] = []

        # LLM document enrichment (optional, requires LLM_PROVIDER_KEY or LLM_PROVIDER_BASE_URL)
        llm_key = getattr(config, "llm_provider_key", None)
        llm_base_url = getattr(config, "llm_provider_base_url", None)
        self._llm_enabled = bool(llm_key) or bool(llm_base_url)
        self._llm = None
        self._llm_metrics = LLMMetrics()
        llm_config = load_toml_section(config, "llm")
        self._llm_model = config.llm_model
        self._llm_truncate_tokens = int(llm_config.get("truncate-tokens", 12000))
        if self._llm_enabled:
            from pazufa_corelib.llm import LLMConnector

            self._llm = LLMConnector(
                model=config.llm_model,
                api_key=llm_key,
                rate_limit_max_calls=5,
                rate_limit_window_seconds=60,
            )
            # corelib v0.1.2 LLMConnector takes no api_base kwarg; bawue_dok reads
            # it off the instance (getattr) and passes it to litellm at call time.
            self._llm.api_base = llm_base_url

    async def run(self) -> None:
        start = time.monotonic()
        try:
            await super().run()
        finally:
            duration = time.monotonic() - start
            logger.info("Completed in %.1fs", duration)
            lines = _print_beteiligung_summary(
                self._published,
                self._skipped,
                self._failed,
                duration,
                self._llm_metrics if self._llm_enabled else None,
                self._failed_items,
            )
            send_mattermost_summary(self.config, "BaWue Beteiligung Run Summary", lines)

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
            self._failed_items.append(
                FailedItem(
                    item_id=str(item.kurztitel or item.api_id),
                    titel=item.titel,
                    reason=outcome.error or "unknown error",
                )
            )
        return outcome.vorgang

    async def listing_page_extractor(self, lp_key: str) -> list[str]:
        """Fetch the process list and return slugs for each process."""
        clear_hash_cache()
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

        # An empty ministry yields no Autor — the new backend rejects
        # empty organisation strings, so the list stays empty rather than
        # carrying a placeholder author.
        ministry_autoren = (
            [Autor(organisation=canonicalize_organisation(detail.ministry))]
            if detail.ministry and detail.ministry.strip()
            else []
        )

        # Build documents
        dokumente: list[Dokument] = []
        trojaner_scores: list[int] = []
        for pdf in detail.pdf_links:
            dok = Dokument(
                titel=todo_if_blank(pdf["title"]),
                volltext=TODO_MARKER,
                hash_=TODO_MARKER,
                typ=Doktyp.PREPARL_ENTWURF,
                zp_modifiziert=zp_start,
                zp_referenz=zp_start,
                link=pdf["url"],
                autoren=ministry_autoren,
            )

            if self._llm_enabled and self._llm is not None:
                try:
                    from bawue.bawue_dok import enrich_dokument

                    result = await enrich_dokument(
                        self.session,
                        self._llm,
                        dok,
                        model=self._llm_model,
                        max_tokens=self._llm_truncate_tokens,
                        metrics=self._llm_metrics,
                        cache=self.config.cache,
                    )
                    dok = result.dokument
                    if result.trojanergefahr is not None:
                        trojaner_scores.append(result.trojanergefahr)
                except Exception:
                    logger.warning("Document enrichment failed for %s", pdf["url"])

            dokumente.append(dok)

        gremium = Gremium(
            parlament=Parlament.BW,
            name=ReservedGremium.REGIERUNG,
            wahlperiode=self._wahlperiode,
        )

        station = Station(
            typ=Stationstyp.PREPARL_REGENT,
            dokumente=dokumente,
            zp_start=zp_start,
            gremium=gremium,
            trojanergefahr=max(trojaner_scores) if trojaner_scores else None,
        )

        beteiligung_url = f"{BASE_URL}/de/mitmachen/lp-{self._wahlperiode}/{slug}"
        ids = [VgIdent(id=beteiligung_url, typ="vorgnr")]

        return Vorgang(
            api_id=str(api_id),
            titel=todo_if_blank(detail.title),
            kurztitel=slug,
            typ=Vorgangstyp.GG_LAND_PARL,
            wahlperiode=self._wahlperiode,
            verfassungsaendernd=is_verfassungsaendernd(detail.title),
            initiatoren=ministry_autoren,
            stationen=[station],
            ids=ids,
        )


def _print_beteiligung_summary(
    published: int,
    skipped: int,
    failed: int,
    duration: float,
    llm_metrics: LLMMetrics | None = None,
    failed_items: list[FailedItem] | None = None,
) -> list[str]:
    discovered = published + skipped + failed
    lines = [
        f"Duration: {format_duration(duration)}",
        f"Discovered:  {discovered}",
        f"Published:   {published}",
        f"Skipped:     {skipped}  (no legislative PDFs)",
        f"Failed:      {failed}",
    ]
    if llm_metrics is not None and llm_metrics.total > 0:
        lines.extend(llm_metrics.format_lines())
    if failed_items:
        lines.extend(format_failed_section(failed_items, header="Failed Vorgänge"))
    print("=== BaWue Beteiligung Run Summary ===\n" + "\n".join(lines))
    return lines
