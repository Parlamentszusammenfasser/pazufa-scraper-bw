"""Issue #101: without --once, __main__ starts a new event loop each cycle (asyncio.run).

A semaphore created at import binds to the first loop that waits on it; in the next
cycle every contended acquire raised "bound to a different event loop", and
enrich_dokument silently dropped LLM fields or volltext.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from bawue import bawue_dok


def _response(*_, **__):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps({"ok": True})
    return resp


async def _slow_completion(*args, **kwargs):
    await asyncio.sleep(0.01)  # hold the semaphore so the other calls contend
    return _response()


def test_llm_calls_contend_in_two_cycles():
    async def cycle():
        return await asyncio.gather(*(bawue_dok._llm_json(MagicMock(), "m", []) for _ in range(5)))

    with patch("bawue.bawue_dok.litellm.acompletion", new=AsyncMock(side_effect=_slow_completion)):
        assert asyncio.run(cycle()) == [{"ok": True}] * 5
        assert asyncio.run(cycle()) == [{"ok": True}] * 5


def test_ocr_limit_is_shared_within_a_cycle_and_fresh_per_cycle():
    async def cycle():
        sem = bawue_dok._ocr_semaphore()
        assert sem is bawue_dok._ocr_semaphore()  # both scrapers share one limit

        async def hold():
            async with bawue_dok._ocr_semaphore():
                await asyncio.sleep(0.01)

        await asyncio.gather(*(hold() for _ in range(4)))
        return sem

    assert asyncio.run(cycle()) is not asyncio.run(cycle())


def test_finished_loops_are_released():
    """A waited-on semaphore references its loop; keeping one per loop would leak a loop per cycle."""
    import gc
    import weakref

    loops = []

    async def cycle():
        loops.append(weakref.ref(asyncio.get_running_loop()))

        async def hold():
            async with bawue_dok._ocr_semaphore():
                await asyncio.sleep(0.01)

        await asyncio.gather(*(hold() for _ in range(4)))

    for _ in range(3):
        asyncio.run(cycle())
    gc.collect()
    assert loops[0]() is None
