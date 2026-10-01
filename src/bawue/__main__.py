"""Entry point for the BaWue scraper, replacing `python -m collector`.

Unlike the removed collector, scrapers are not auto-discovered from a
directory — there are only four of them, so a static registry is simpler
and avoids importlib plugin-loading machinery.
"""

import asyncio
import logging
import os
import time

import aiohttp
from dotenv import load_dotenv

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_sitzungen_scraper import BawueSitzungenScraper
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.config import BawueConfig
from bawue.notifications import send_run_report
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
        results: list = []
        try:
            if not config.linearize:
                results = await asyncio.gather(*scraper_tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.error("Some Task failed: %s", r)
            else:
                for t in scraper_tasks:
                    await t
        finally:
            # One Mattermost message per cycle, not one per scraper (issue #52).
            send_run_report(config, _report_sections(scrapers, results))


def _report_sections(scrapers: list[Scraper], results: list) -> list[tuple[str, list[str]]]:
    """Each scraper's summary, plus a FAILED line for one that raised, so a crash is never
    silent in the report (issue #52). *results* are gather's, aligned with *scrapers*."""
    sections = []
    for i, scraper in enumerate(scrapers):
        title, lines = scraper.summary or (type(scraper).__name__, [])
        error = results[i] if i < len(results) else None
        if isinstance(error, Exception):
            lines = [*lines, f"FAILED: {type(error).__name__}: {' '.join(str(error).split())[:200]}"]
        if lines:
            sections.append((title, lines))
    return sections


def log_startup() -> None:
    """Name the running image's version, set at build time (Dockerfile ARG, issue #84).
    pyproject.toml can't tell: CI only pushes the release tag (semantic-release --no-commit)."""
    version = os.environ.get("SCRAPER_VERSION") or "dev"
    logger.info("Starting BaWue scraper manager (version %s).", version)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s: %(filename)-20s: %(message)s",
    )
    # Before config.load(): a broken config must still say which image ran.
    log_startup()

    config = BawueConfig()
    config.load()

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
