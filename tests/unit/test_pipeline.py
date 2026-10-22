"""Scraper.process_lpurls: one failing listing page must not lose the others."""

from unittest.mock import MagicMock

import pytest

from bawue.pipeline import Scraper


@pytest.mark.asyncio
async def test_failing_listing_page_keeps_the_others():
    """Production gathers the Vorgangstyp searches; a PARLIS error in one is logged and skipped."""
    scraper = MagicMock()
    scraper.config.linearize = False
    scraper.listing_urls = ["Gesetzgebung", "Volksantrag", "Staatsvertrag"]

    async def extract(url):
        if url == "Volksantrag":
            raise RuntimeError("PARLIS down")
        return [f"{url}-1", "V-shared"]

    scraper.listing_page_extractor = extract

    assert await Scraper.process_lpurls(scraper, scraper.listing_urls) == {
        "Gesetzgebung-1",
        "Staatsvertrag-1",
        "V-shared",  # deduplicated across pages
    }
