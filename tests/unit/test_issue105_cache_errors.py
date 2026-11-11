"""Issue #105: Redis failing mid-run is no cache miss — skip the work, count it, report it."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bawue.__main__ import _report_sections
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.cache import RedisUnavailableError
from bawue.vorgang_llm import vorgang_kurztitel, vorgang_ressort
from tests.factories import make_scraper


@pytest.mark.asyncio
async def test_lookup_error_skips_the_item_and_counts_it():
    scraper = make_scraper(BawueVorgaengeScraper)
    scraper.config = MagicMock(max_concurrency=1, linearize=True)
    scraper.get_cached_result = AsyncMock(side_effect=[RedisUnavailableError("redis down"), None])
    scraper.helper_extract_send_item = AsyncMock(return_value=None)

    await scraper.process_items({"V-1", "V-2"})

    assert scraper.cache_errors == 1
    assert scraper.item_count == 1  # only V-2 is built
    scraper.helper_extract_send_item.assert_awaited_once()


@pytest.mark.parametrize(
    ("cache_errors", "expected"),
    [(3, ["  - V-1 | HTTP 500", "Cache errors: 3 (skipped, retried next run)"]), (0, ["  - V-1 | HTTP 500"])],
)
def test_report_names_cache_errors(cache_errors, expected):
    scraper = MagicMock(title="Vorgänge", errors=["  - V-1 | HTTP 500"], cache_errors=cache_errors)
    [(_, lines)] = _report_sections([scraper], [])
    assert lines == expected


def _broken_cache():
    cache = MagicMock()
    cache.get_raw.side_effect = RedisUnavailableError("redis down")
    return cache


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [vorgang_kurztitel, vorgang_ressort])
async def test_llm_call_fails_the_build_without_llm_call(call):
    """A fallback would be cached with the Vorgang if Redis is back by then; failing retries it."""
    with (
        patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock) as llm_call,
        pytest.raises(RedisUnavailableError),
    ):
        await call(MagicMock(), "Titel", "Zusammenfassung", cache=_broken_cache())
    llm_call.assert_not_called()
