"""Issue #57: the per-Vorgang LLM cache keys (DD-063).

The key composition is pinned. A stray edit to a prompt or to the user-message
format changes these golden keys and fails here instead of silently orphaning the
live cache. Update them only on purpose.
"""

from unittest.mock import MagicMock

import pytest

from bawue.bawue_dok import vorgang_kurztitel, vorgang_ressort

TITEL = "Gesetz zur Änderung des Landeshochschulgesetzes"
SUMMARY = "Der Entwurf ändert das Hochschulrecht."


async def _lookup_key(call, model: str = "gpt-5-nano") -> str:
    """The key *call* looks up; the cache hit keeps the LLM out of it."""
    cache = MagicMock()
    cache.get_raw.return_value = "{}"
    await call(MagicMock(), TITEL, SUMMARY, model=model, cache=cache)
    return cache.get_raw.call_args[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "golden"),
    [
        (vorgang_kurztitel, "vorgang-kurztitel:1d9de195fd517b414046dc4949e74204f548a0c02c3a9347ecf51202a81198b9"),
        (vorgang_ressort, "vorgang-ressort:7718b6e34a09f10d64e8276459dbd82f63a837c188d4a52deb6ac0fd54f512b3"),
    ],
    ids=["kurztitel", "ressort"],
)
async def test_cache_key_is_pinned(call, golden):
    assert await _lookup_key(call) == golden
