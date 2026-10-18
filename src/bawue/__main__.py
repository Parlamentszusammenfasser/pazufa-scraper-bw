"""Entry point for the BaWue scraper, replacing `python -m collector`.

Unlike the removed collector, scrapers are not auto-discovered from a
directory — there are only four of them, so a static registry is simpler
and avoids importlib plugin-loading machinery.
"""

import asyncio
import logging
import os
import sys

import aiohttp
from dotenv import load_dotenv

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_dok import clear_hash_cache
from bawue.bawue_sitzungen_scraper import BawueSitzungenScraper
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.cache import RedisUnavailableError
from bawue.config import BawueConfig
from bawue.notifications import send_alert, send_run_report
from bawue.pipeline import Scraper
from bawue.wochenbericht import weekly_section

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


async def main(config: BawueConfig) -> bool:
    """Run one scraping cycle; True if any scraper failed."""
    logger.info("Starting new Scraping Cycle")
    clear_hash_cache()  # once per cycle: the scrapers run concurrently and share it (issue #101)
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
            sections = _report_sections(scrapers, results)
            weekly = weekly_section(config)  # after the uploads, so this run's changes count (issue #82)
            missing = [line for s in scrapers for line in getattr(s, "missing_documents", [])]
            send_run_report(
                config,
                [*sections, weekly] if weekly else sections,
                # Weekly, like the Wochenbericht: Anne asks the Landtag about these (issue #128).
                plain_sections=[("Fehlende Dokumente", missing)] if weekly and missing else [],
            )
    return any(isinstance(r, Exception) for r in results)


def _report_sections(scrapers: list[Scraper], results: list) -> list[tuple[str, list[str]]]:
    """Each scraper's summary, plus a FAILED line for one that raised, so a crash is never
    silent in the report (issue #52). *results* are gather's, aligned with *scrapers*."""
    sections = []
    for i, scraper in enumerate(scrapers):
        title, lines = scraper.summary or (type(scraper).__name__, [])
        error = results[i] if i < len(results) else None
        if isinstance(error, Exception):
            lines = [*lines, f"FAILED: {type(error).__name__}: {' '.join(str(error).split())[:200]}"]
        if getattr(scraper, "cache_errors", 0):
            lines = [*lines, f"Cache errors: {scraper.cache_errors} (skipped, retried next run)"]
        if lines:
            sections.append((title, lines))
    return sections


def log_startup() -> None:
    """Name the running image's version, set at build time (Dockerfile ARG, issue #84).
    pyproject.toml can't tell: CI only pushes the release tag (semantic-release --no-commit)."""
    version = os.environ.get("SCRAPER_VERSION") or "dev"
    logger.info("Starting BaWue scraper manager (version %s).", version)


def load_config() -> BawueConfig:
    """Load the config; without Redis the run must not start, so fail fast and alert."""
    config = BawueConfig()
    try:
        config.load()
    except RedisUnavailableError as e:
        send_alert(config, f"{e}. No data was scraped.")
        sys.exit(1)
    return config


def run(config: BawueConfig) -> None:
    """One scraping cycle. A cycle that raises alerts Mattermost and exits 1 (Cloud Run: failed);
    a failed scraper exits 1 too, its error is already in the run report."""
    try:
        failed = asyncio.run(main(config))
    except Exception as e:
        send_alert(config, f"Scraping cycle failed: {type(e).__name__}: {' '.join(str(e).split())[:200]}")
        raise
    if failed:
        logger.error("Scraping cycle finished with failed scrapers.")
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s: %(filename)-20s: %(message)s",
    )
    # Before config.load(): a broken config must still say which image ran.
    log_startup()

    config = load_config()

    logger.info("Configuration Complete")

    # One cycle per process; the scheduler starts the next.
    run(config)
    logger.info("Scraping cycle completed. Shutting down.")
