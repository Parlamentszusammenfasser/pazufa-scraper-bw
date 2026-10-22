"""Scraper.process_lpurls: one failing listing page must not lose the others."""

from unittest.mock import MagicMock

import pytest

from bawue.pipeline import Scraper


@pytest.mark.asyncio
@pytest.mark.parametrize("linearize", [False, True], ids=["gathered", "linearized"])
async def test_failing_listing_page_keeps_the_others(linearize):
    """A PARLIS error in one Vorgangstyp search is logged and skipped, in either mode."""
    scraper = MagicMock()
    scraper.config.linearize = linearize
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
