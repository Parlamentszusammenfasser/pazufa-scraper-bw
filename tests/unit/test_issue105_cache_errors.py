"""Issue #105: Redis failing mid-run is no cache miss — skip the work, count it, report it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bawue.__main__ import _report_sections
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.cache import RedisUnavailableError


@pytest.mark.asyncio
async def test_lookup_error_skips_the_item_and_counts_it():
    scraper = object.__new__(BawueVorgaengeScraper)
    scraper.config = MagicMock(max_concurrency=1, linearize=True)
    scraper.get_cached_result = AsyncMock(side_effect=[RedisUnavailableError("redis down"), None])
    scraper.helper_extract_send_item = AsyncMock(return_value=None)

    await scraper.process_items({"V-1", "V-2"})

    assert scraper.cache_errors == 1
    assert scraper.item_count == 1  # only V-2 is built
    scraper.helper_extract_send_item.assert_awaited_once()


def test_report_names_cache_errors():
    scraper = MagicMock(summary=("Vorgänge", ["Found: 1"]), cache_errors=3)
    [(_, lines)] = _report_sections([scraper], [])
    assert lines[-1] == "Cache errors: 3 (skipped, retried next run)"


def test_report_is_quiet_without_cache_errors():
    scraper = MagicMock(summary=("Vorgänge", ["Found: 1"]), cache_errors=0)
    [(_, lines)] = _report_sections([scraper], [])
    assert lines == ["Found: 1"]


def _broken_cache():
    cache = MagicMock()
    cache.get_raw.side_effect = RedisUnavailableError("redis down")
    return cache


@pytest.mark.asyncio
async def test_kurztitel_falls_back_to_titel_without_llm_call():
    from unittest.mock import patch

    from bawue.bawue_dok import vorgang_kurztitel

    with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock) as llm_call:
        assert await vorgang_kurztitel(MagicMock(), "Langer Titel", "Zusammenfassung", cache=_broken_cache()) == (
            "Langer Titel"
        )
    llm_call.assert_not_called()


@pytest.mark.asyncio
async def test_ressort_stays_unset_without_llm_call():
    from unittest.mock import patch

    from bawue.bawue_dok import vorgang_ressort

    with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock) as llm_call:
        assert await vorgang_ressort(MagicMock(), "Titel", "Zusammenfassung", cache=_broken_cache()) is None
    llm_call.assert_not_called()
