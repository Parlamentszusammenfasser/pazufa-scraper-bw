"""Scraper orchestration base classes, replacing the removed collector.interface.

Ports the process_lpurls → process_items → process_results → run pipeline
mechanically from collector/interface.py. The default send_result
implementations (which posted to the old openapi_client API) are dropped:
every BaWue scraper already overrides send_result with its own
bawue.api-based upload, so keeping a dead default would just be an
unmaintained second code path.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from hashlib import sha256
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID

import aiohttp

from bawue.config import BawueConfig

logger = logging.getLogger(__name__)


class Scraper(ABC):
    # Class-level defaults so tests that build a scraper via object.__new__()
    # (bypassing __init__ to isolate unit-under-test) still have these attrs.
    listing_urls: ClassVar[list[str]] = []
    scraper_id: UUID | None = None
    config: BawueConfig | None = None
    session: aiohttp.ClientSession | None = None

    def __init__(
        self,
        config: BawueConfig,
        collector_id: UUID,
        listing_urls: list[str],
        session: aiohttp.ClientSession,
    ) -> None:
        assert isinstance(collector_id, UUID)
        assert isinstance(session, aiohttp.ClientSession)
        self.scraper_id = collector_id
        self.listing_urls = listing_urls
        self.config = config
        self.session = session
        self.item_count = 0
        self.items_done = 0
        logger.info("Initialized %s with %d listing urls", self.__class__.__name__, len(self.listing_urls))

    async def process_lpurls(self, lpurls: list[str]) -> set[Any]:
        logger.info("Processing Listing Page URLs Now")
        try:
            tasks = [self.listing_page_extractor(lpage) for lpage in self.listing_urls]

            if self.config.linearize:
                item_list = [await t for t in tasks]
            else:
                item_list = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(item_list):
                if isinstance(result, Exception):
                    logger.error(
                        "%s: Error extracting listing page %s: %s",
                        self.__class__.__name__,
                        self.listing_urls[i],
                        result,
                    )
                    item_list[i] = []

            return {x for xs in item_list if isinstance(xs, list) for x in xs}
        except Exception as e:
            logger.error("%s: Error gathering listing page extraction: %s", self.__class__.__name__, e, exc_info=True)
            # Empty set, not None: process_items iterates the result.
            return set()

    async def helper_extract_send_item(self, item: Any, semaphore: asyncio.Semaphore) -> tuple[Any, Any] | None:
        async with semaphore:
            logger.info("Extraction started on item %s", item)
            extracted_item = await self.item_extractor(item)
        self.items_done += 1
        logger.info(
            "Extraction Progress: %d/%d items, (%.1f%%) %s",
            self.items_done,
            self.item_count,
            100 * self.items_done / self.item_count,
            item,
        )
        if not extracted_item:
            return None

        self.log_item(extracted_item)
        sent_item = await self.send_result(extracted_item)
        if not sent_item:
            return None

        key = await self.make_cache_key(item)
        await self.store_extracted_result(key, extracted_item)
        return (sent_item, item)

    async def process_items(self, items: set[Any]) -> list[Any]:
        tasks = []
        processed_count = 0
        skipped_count = 0
        logger.info("Processing Items Now")
        semaphore = asyncio.Semaphore(self.config.max_concurrency)

        for item in sorted(items):
            key = await self.make_cache_key(item)
            cached = await self.get_cached_result(key)
            if cached is not None:
                logger.debug("%s found in cache, skipping...", key)
                skipped_count += 1
                continue

            tasks.append(self.helper_extract_send_item(item, semaphore))
            processed_count += 1

        self.item_count = len(tasks)
        logger.info(
            "%s: Processing %d items, skipping %d cached items",
            self.__class__.__name__,
            processed_count,
            skipped_count,
        )

        results = []
        if self.config.linearize:
            for t in tasks:
                try:
                    results.append(await t)
                except Exception as e:
                    logger.error("%s: Error during item extraction: %s", self.__class__.__name__, e, exc_info=True)
        else:
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.error(
                    "%s: Error during item extraction gathering: %s", self.__class__.__name__, e, exc_info=True
                )

        return results

    async def process_results(self, results: list[Any]) -> tuple[int, int, int]:
        success_count = 0
        error_count = 0
        ignored_count = 0
        for result in results:
            if result and not isinstance(result, Exception) and result[0]:
                success_count += 1
            elif not result or (not isinstance(result, Exception) and not result[0]):
                ignored_count += 1
            else:
                error_count += 1
                if isinstance(result, Exception):
                    logger.error(
                        "%s: Item extraction failed with exception: %s",
                        self.__class__.__name__,
                        result,
                        exc_info=True,
                    )
                else:
                    logger.error("%s: Item extraction failed with result: %s", self.__class__.__name__, result)
        logger.info(
            "Extractor %s completed: %d successes, %d errors", self.__class__.__name__, success_count, error_count
        )
        return (success_count, ignored_count, error_count)

    async def run(self) -> None:
        iset = await self.process_lpurls(self.listing_urls)
        rset = await self.process_items(iset)
        await self.process_results(rset)

    @abstractmethod
    async def get_cached_result(self, item_key: str) -> Any | None: ...

    @abstractmethod
    async def store_extracted_result(self, item_key: str, result: Any) -> Any | None: ...

    @abstractmethod
    async def make_cache_key(self, item: Any) -> str | None: ...

    @abstractmethod
    def log_item(self, item: Any, override: bool = True) -> None: ...

    @abstractmethod
    async def send_result(self, item: Any) -> Any | None: ...

    @abstractmethod
    async def listing_page_extractor(self, url: str) -> list[Any]: ...

    @abstractmethod
    async def item_extractor(self, listing_item: Any) -> Any: ...


class VorgangsScraper(Scraper):
    def log_item(self, item: Any, override: bool = True) -> None:
        logdir = self.config.api_obj_log if self.config.api_obj_log else ("locallogs" if override else None)
        if logdir is None:
            return
        logger.debug("Logging Item to %s", logdir)
        try:
            filepath = Path(logdir) / f"{self.scraper_id}.jsonl"
            if not filepath.parent.exists():
                logger.info("Creating Filepath: %s", filepath.parent)
                filepath.parent.mkdir(parents=True)
            with filepath.open("a", encoding="utf-8") as file:
                file.write(json.dumps(item, default=str) + ",\n")
        except Exception as e:
            logger.error("Failed to write to API object log: %s", e)

    async def make_cache_key(self, item: Any) -> str:
        return str(item)

    # Vorgänge are cached as attrs to_dict() JSON under a versioned `vg2:` key
    # (see bawue.types / Phase 1 cache-format notes) — both Vorgang scrapers
    # share this exact logic, so it lives here instead of being duplicated.
    async def get_cached_result(self, item_key: str) -> str | None:
        return self.config.cache.get_raw(f"vg2:{item_key}", "Vorgang")

    async def store_extracted_result(self, item_key: str, result: Any) -> None:
        self.config.cache.store_raw(f"vg2:{item_key}", json.dumps(result.to_dict()), "Vorgang")


class SitzungsScraper(Scraper):
    def log_item(self, item: Any, override: bool = True) -> None:
        logdir = self.config.api_obj_log if self.config.api_obj_log else ("locallogs" if override else None)
        if logdir is None:
            return
        logger.info("Logging Item to %s", logdir)
        try:
            filepath = Path(logdir) / f"{self.scraper_id}.jsonl"
            if not filepath.parent.exists():
                logger.info("Creating Filepath: %s", filepath.parent)
                filepath.parent.mkdir(parents=True)
            with filepath.open("a", encoding="utf-8") as file:
                file.write(json.dumps(item, default=str) + ",\n")
        except Exception as e:
            logger.error("Failed to write to API object log: %s", e)

    async def store_extracted_result(self, item_key: str, result: Any) -> None:
        self.config.cache.store_raw(item_key, str(result))

    async def get_cached_result(self, item_key: str) -> str | None:
        return self.config.cache.get_raw(item_key)

    async def make_cache_key(self, item: Any) -> str:
        return f"sz:{sha256(str(item).encode()).hexdigest()}"
