"""Entry point for the BaWue scraper, replacing `python -m collector`.

Unlike the removed collector, scrapers are not auto-discovered from a
directory — there are only four of them, so a static registry is simpler
and avoids importlib plugin-loading machinery.
"""

import asyncio
import logging
import time

import aiohttp
from dotenv import load_dotenv

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_sitzungen_scraper import BawueSitzungenScraper
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.config import BawueConfig
from bawue.pipeline import Scraper

load_dotenv()

logger = logging.getLogger("bawue")

import litellm  # noqa: E402, F401

logging.getLogger("LiteLLM").setLevel(logging.WARNING)

SCRAPERS = [BawueVorgaengeScraper, BawueBeteiligungScraper, BawueSitzungenScraper]


def load_scrapers(config: BawueConfig, session: aiohttp.ClientSession) -> list[Scraper]:
    scrapers = []
    for cls in SCRAPERS:
        enabled = len(config.scrapers) == 0
        for scn in config.scrapers:
            if cls.__name__.lower().startswith(scn.lower()):
                enabled = True
                break
        if not enabled:
            continue
        scrapers.append(cls(config, session))

    logger.info("Enabled Scrapers are: %s", ", ".join(type(s).__name__ for s in scrapers))
    return scrapers


async def main(config: BawueConfig) -> None:
    logger.info("Starting new Scraping Cycle")
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit_per_host=1)) as session:
        scrapers = load_scrapers(config, session)
        scraper_tasks = []
        for scraper in scrapers:
            logger.info("Running scraper: %s", scraper.__class__.__name__)
            scraper_tasks.append(scraper.run())

        logger.info("Running %d scraper tasks concurrently", len(scraper_tasks))
        if not config.linearize:
            results = await asyncio.gather(*scraper_tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error("Some Task failed: %s", r)
        else:
            for t in scraper_tasks:
                await t


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s: %(filename)-20s: %(message)s",
    )

    config = BawueConfig()
    config.load()

    logger.info("Starting BaWue scraper manager.")
    logger.info("Configuration Complete")
    if config.dry_run:
        logger.warning("DRY RUN mode enabled — no data will be submitted to the API")

    last_run = None
    while True:
        if last_run is not None and time.time() - last_run < config.cycle_time_s:
            logger.info("Last scraping cycle finished, sleeping until next cycle. Bye!")
            time.sleep(config.cycle_time_s - (time.time() - last_run))
            continue
        try:
            last_run = time.time()
            asyncio.run(main(config))
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break
        except Exception as e:
            logger.error("Error: %s", e)
            continue
        if config.once:
            logger.info("Single cycle completed (--once mode). Shutting down.")
            break
