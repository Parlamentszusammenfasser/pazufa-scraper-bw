"""Issue #57: the per-Vorgang LLM cache keys include the model (DD-063).

1. After a model switch, the next build of a Vorgang asks the new model instead of
   serving the previous model's cached Kurztitel or Ressort.
2. The key composition is pinned. A stray edit to a prompt or to the user-message
   format changes these golden keys and fails here instead of silently orphaning
   the live cache. Update them only on purpose.
"""

from unittest.mock import MagicMock

import pytest

from bawue.vorgang_llm import vorgang_kurztitel, vorgang_ressort

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
        (vorgang_kurztitel, "vorgang-kurztitel:ce69975aff01d35101f769441ac61f98d33b1241712556d02d1f11d3498048dc"),
        (vorgang_ressort, "vorgang-ressort:6974537bdfaec2a2d3b297b5bdc08a5214718018a2e76efeba01ff177b5935a5"),
    ],
    ids=["kurztitel", "ressort"],
)
async def test_cache_key_is_pinned(call, golden):
    assert await _lookup_key(call) == golden


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [vorgang_kurztitel, vorgang_ressort], ids=["kurztitel", "ressort"])
async def test_cache_key_depends_on_the_model(call):
    assert await _lookup_key(call, model="gpt-5-nano") != await _lookup_key(call, model="gpt-5-mini")
