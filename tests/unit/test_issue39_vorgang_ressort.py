"""Issue #39: Vorgang.ressort is classified by the LLM (DD-055).

Aligned with pazufa-scraper-bb's ressort classification (its issue #53) so a
Ressort means the same thing across Länder:

1. One own LLM call per Vorgang, from ``titel`` + the initiating document's
   ``zusammenfassung``; the value comes from corelib's ``Ressort`` enum.
2. The subject matter of the regulation decides, not who submitted it — so the
   full enum must reach the model, and the prompt carries the shared rules.
3. ``None`` when nothing fits, when the LLM is off, or when it fails; the caller
   then omits the field (``UNSET``).
4. Cached per (titel, summary, prompt) under its own prefix, so the
   ``llm-semantics:`` cache stays untouched. A classified "nothing fits" is a
   permanent answer and is cached too; only a failed call stays uncached.
5. The model reasons in a ``begruendung`` before naming the Ressort, and a
   near-miss spelling of a compound value is normalised rather than dropped.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bawue.bawue_dok import RESSORT_PROMPT, vorgang_ressort
from bawue.types import TODO_MARKER, Ressort

TITEL = "Gesetz zur Förderung des Ausbaus der Windenergie in Baden-Württemberg"
SUMMARY = "Der Entwurf beschleunigt Genehmigungen für Windkraftanlagen."


def _llm():
    llm = MagicMock()
    llm.api_key = "test-key"
    llm.temperature = 0.1
    llm.timeout_seconds = 60.0
    return llm


def _response(ressort, begruendung="Weil es fachlich passt."):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json.dumps({"begruendung": begruendung, "ressort": ressort}, ensure_ascii=False)
    return resp


def _patch_llm(*ressorts):
    return patch(
        "bawue.bawue_dok.litellm.acompletion",
        new_callable=AsyncMock,
        side_effect=[_response(r) for r in ressorts],
    )


def _empty_cache():
    cache = MagicMock()
    cache.get_raw.return_value = None
    return cache


class TestVorgangRessort:
    @pytest.mark.asyncio
    async def test_classifies_into_the_enum(self):
        with _patch_llm("Energie"):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_empty_cache()) == Ressort.ENERGIE

    @pytest.mark.asyncio
    async def test_none_when_nothing_fits(self):
        """`null` must survive: the caller omits the field rather than guessing."""
        with _patch_llm(None):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_empty_cache()) is None

    @pytest.mark.asyncio
    async def test_value_outside_the_enum_is_rejected(self):
        """A hallucinated portfolio must not reach the payload as a raw string."""
        with _patch_llm("Windkraft"):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_empty_cache()) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answer",
        [
            "Verkehr/Infrastruktur",
            "verkehr/infrastruktur",
            "Verkehr / Infrastruktur",
            "VERKEHRINFRASTRUKTUR",
            " Verkehr/Infrastruktur ",
        ],
    )
    async def test_near_miss_spelling_of_a_compound_value_is_normalised(self, answer):
        """8 of the 33 values are slash/hyphen compounds — the spellings a small model
        drifts to must not cost the field silently."""
        with _patch_llm(answer):
            result = await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_empty_cache())

        assert result == Ressort.VERKEHRINFRASTRUKTUR

    @pytest.mark.asyncio
    async def test_placeholder_title_is_not_classified(self):
        """`todo_if_blank` turns a missing title into "TODO"; classifying that would
        invent a Ressort from nothing — and cache it forever."""
        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock) as call:
            assert await vorgang_ressort(_llm(), TODO_MARKER, None, cache=_empty_cache()) is None
        call.assert_not_called()

    @pytest.mark.asyncio
    async def test_prompt_asks_for_the_reasoning_before_the_label(self):
        """The model needs room to reason before committing, and the reason is what
        makes a wrong classification auditable afterwards (as in the BB scraper)."""
        assert "begruendung" in RESSORT_PROMPT
        assert RESSORT_PROMPT.index("begruendung") < RESSORT_PROMPT.rindex("ressort")

    @pytest.mark.asyncio
    async def test_no_llm_yields_none(self):
        assert await vorgang_ressort(None, TITEL, SUMMARY, cache=_empty_cache()) is None

    @pytest.mark.asyncio
    async def test_llm_failure_yields_none(self):
        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_empty_cache()) is None

    @pytest.mark.asyncio
    async def test_every_ressort_value_reaches_the_model(self):
        """A truncated or stale list silently narrows what the classifier can return
        (same canary as the BB scraper's)."""
        with _patch_llm("Energie") as mock_call:
            await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_empty_cache())
        prompt = mock_call.call_args.kwargs["messages"][-1]["content"]
        missing = [r.value for r in Ressort if r.value not in prompt]
        assert not missing, f"Ressort values never shown to the model: {missing}"

    @pytest.mark.asyncio
    async def test_prompt_states_the_shared_rule(self):
        """The subject matter decides, not the submitting body — the rule BW and BB share."""
        assert "nicht der einbringende Akteur" in RESSORT_PROMPT

    @pytest.mark.asyncio
    async def test_result_is_cached_under_its_own_prefix(self):
        cache = _empty_cache()
        with _patch_llm("Energie"):
            await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=cache)
        key, value = cache.store_raw.call_args[0][:2]
        assert key.startswith("vorgang-ressort:")
        assert json.loads(value)["ressort"] == "Energie"

    @pytest.mark.asyncio
    async def test_classified_null_is_cached_too(self):
        """ "Nothing fits" is a permanent answer — re-asking it every run pays for the
        same null forever and lets the label flip between runs."""
        cache = _empty_cache()
        with _patch_llm(None):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=cache) is None
        assert json.loads(cache.store_raw.call_args[0][1])["ressort"] is None

    @pytest.mark.asyncio
    async def test_failed_call_is_not_cached(self):
        """A transient failure must not pin an empty result."""
        cache = _empty_cache()
        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=cache)
        cache.store_raw.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", ['{"ressort": "Umwelt"}', "Umwelt", "not json at all"])
    async def test_unparseable_cache_entry_does_not_crash_the_run(self, stored):
        """A malformed entry — or one written by the first iteration of this cache,
        which stored the bare value — must degrade to a re-read, never raise."""
        cache = MagicMock()
        cache.get_raw.return_value = stored
        expected = Ressort.UMWELT if "Umwelt" in stored else None
        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=cache) == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", ['{"ressort": "Umwelt"}', '{"ressort": null}'])
    async def test_cache_hit_skips_the_llm(self, stored):
        cache = MagicMock()
        cache.get_raw.return_value = stored
        expected = Ressort.UMWELT if "Umwelt" in stored else None
        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock) as call:
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=cache) == expected
        call.assert_not_called()
