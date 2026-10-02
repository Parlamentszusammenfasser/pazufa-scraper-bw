"""Issue #56: the per-Vorgang LLM calls are counted in the run report.

DD-055 defers checking the Ressort classification to the first live run, so the
run must say how it went:

1. ``vorgang_ressort`` counts each classification as classified, null (the model
   said nothing fits), rejected (an answer that names no ``Ressort`` member) or
   failed (the call raised). A cache hit counts by its outcome, so the raw answer
   is cached — a rejected answer must not turn into a null on the next run.
2. ``vorgang_kurztitel`` counts a generated title or a fallback to ``titel``.
3. Both appear in ``LLMMetrics.format_lines`` — the log summary and the Mattermost
   run report of both scrapers — even when no document was enriched.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bawue.bawue_beteiligung_scraper import _print_beteiligung_summary
from bawue.bawue_dok import KURZTITEL_MAX_LEN, LLMMetrics, vorgang_kurztitel, vorgang_ressort
from bawue.bawue_vorgaenge_scraper import _print_vorgaenge_summary
from bawue.types import TODO_MARKER

TITEL = "Gesetz zur Förderung des Ausbaus der Windenergie in Baden-Württemberg"
SUMMARY = "Der Entwurf beschleunigt Genehmigungen für Windkraftanlagen."
VORGANG_COUNTERS = (
    "kurztitel_generated",
    "kurztitel_fallback",
    "ressort_classified",
    "ressort_null",
    "ressort_rejected",
    "ressort_failed",
)


@pytest.fixture(autouse=True)
def _no_unplanned_llm_call():
    """A call no test planned for is counted as failed — and never leaves the machine."""
    with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, side_effect=AssertionError):
        yield


def _llm():
    llm = MagicMock()
    llm.api_key = "test-key"
    llm.temperature = 0.1
    llm.timeout_seconds = 60.0
    return llm


def _patch_llm(*answers: object):
    replies = []
    for answer in answers:
        reply = MagicMock()
        reply.choices = [MagicMock()]
        reply.choices[0].message.content = json.dumps(answer, ensure_ascii=False)
        replies.append(reply)
    return patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, side_effect=replies)


def _cache(stored: str | None = None):
    cache = MagicMock()
    cache.get_raw.return_value = stored
    return cache


def _only(counter: str | None) -> dict[str, int]:
    """Every counter, document ones included, at 0 — except *counter* at 1."""
    return {**vars(LLMMetrics()), **({counter: 1} if counter else {})}


class TestRessortCounters:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("answer", "counter"),
        [("Energie", "ressort_classified"), (None, "ressort_null"), ("Windkraft", "ressort_rejected")],
    )
    async def test_each_answer_counts_once_by_outcome(self, answer, counter):
        metrics = LLMMetrics()
        with _patch_llm({"begruendung": "…", "ressort": answer}):
            await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_cache(), metrics=metrics)

        assert vars(metrics) == _only(counter)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", ["", "   ", 42, ["Energie"], {"ressort": "Energie"}])
    async def test_an_answer_that_is_neither_a_value_nor_null_is_rejected_and_logged(self, answer, caplog):
        """Null is the one way to say "nothing fits"; anything else off the enum is a
        drift the run report must show — and the warning must name it."""
        metrics = LLMMetrics()
        with _patch_llm({"ressort": answer}), caplog.at_level(logging.WARNING, logger="bawue.bawue_dok"):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_cache(), metrics=metrics) is None

        assert vars(metrics) == _only("ressort_rejected")
        assert "unknown Ressort" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply", [["Finanzen"], "Finanzen"])
    async def test_a_reply_that_is_no_json_object_counts_as_failed(self, reply):
        """Valid JSON, but no object: one Vorgang's odd reply must not fail its build."""
        metrics = LLMMetrics()
        cache = _cache()
        with _patch_llm(reply):
            assert await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=cache, metrics=metrics) is None

        assert vars(metrics) == _only("ressort_failed")
        cache.store_raw.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_call_counts_as_failed(self):
        metrics = LLMMetrics()
        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_cache(), metrics=metrics)

        assert vars(metrics) == _only("ressort_failed")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stored", "counter"),
        [
            ('{"ressort": "Umwelt"}', "ressort_classified"),
            ('{"ressort": null}', "ressort_null"),
            ('{"ressort": "Windkraft"}', "ressort_rejected"),
            ("Umwelt", "ressort_classified"),  # the first iteration of this cache stored the bare value
            ("not json at all", "ressort_rejected"),  # a corrupt entry is read as a raw answer
        ],
    )
    async def test_cache_hit_counts_by_its_outcome(self, stored, counter):
        metrics = LLMMetrics()
        await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_cache(stored), metrics=metrics)

        assert vars(metrics) == _only(counter)

    @pytest.mark.asyncio
    async def test_rejected_answer_stays_rejected_on_the_next_run(self):
        """Caching the parsed value would store a rejection as null, and from the second
        run on the report would blame the model for what is an enum drift."""
        first = _cache()
        with _patch_llm({"begruendung": "Windkraft halt.", "ressort": "Windkraft"}):
            await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=first)
        stored = first.store_raw.call_args[0][1]

        metrics = LLMMetrics()
        await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_cache(stored), metrics=metrics)

        assert json.loads(stored) == {"ressort": "Windkraft", "begruendung": "Windkraft halt."}
        assert vars(metrics) == _only("ressort_rejected")

    @pytest.mark.asyncio
    async def test_the_answer_is_logged_short_and_on_one_line(self, caplog):
        """The raw answer is re-logged on every cache hit, and the model's text must not
        forge log lines in the plain-text production log."""
        with (
            _patch_llm({"begruendung": "Kurz.\nERROR forged line", "ressort": "x" * 10_000}),
            caplog.at_level(logging.INFO, logger="bawue.bawue_dok"),
        ):
            await vorgang_ressort(_llm(), TITEL, SUMMARY, cache=_cache())

        messages = [r.getMessage() for r in caplog.records]
        assert messages
        assert all(len(m) < 300 and "\n" not in m for m in messages)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("llm", "titel"), [(None, TITEL), (_llm(), TODO_MARKER), (_llm(), "  ")])
    async def test_nothing_is_counted_without_a_classification(self, llm, titel):
        metrics = LLMMetrics()
        await vorgang_ressort(llm, titel, SUMMARY, cache=_cache(), metrics=metrics)

        assert vars(metrics) == _only(None)


class TestKurztitelCounters:
    GOOD = "Schneller Windkraftausbau"
    TOO_LONG = "x" * (KURZTITEL_MAX_LEN + 1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answers",
        [[GOOD], [TOO_LONG, GOOD]],
        ids=["first-attempt", "after-reprompt"],
    )
    async def test_generated_title_counts_once(self, answers):
        metrics = LLMMetrics()
        with _patch_llm(*({"kurztitel": a} for a in answers)):
            await vorgang_kurztitel(_llm(), TITEL, SUMMARY, cache=_cache(), metrics=metrics)

        assert vars(metrics) == _only("kurztitel_generated")

    @pytest.mark.asyncio
    async def test_cache_hit_counts_as_generated(self):
        metrics = LLMMetrics()
        await vorgang_kurztitel(_llm(), TITEL, SUMMARY, cache=_cache(self.GOOD), metrics=metrics)

        assert vars(metrics) == _only("kurztitel_generated")

    @pytest.mark.asyncio
    async def test_still_invalid_after_reprompt_counts_as_fallback(self):
        metrics = LLMMetrics()
        with _patch_llm({"kurztitel": self.TOO_LONG}, {"kurztitel": self.TOO_LONG}):
            await vorgang_kurztitel(_llm(), TITEL, SUMMARY, cache=_cache(), metrics=metrics)

        assert vars(metrics) == _only("kurztitel_fallback")

    @pytest.mark.asyncio
    async def test_failed_call_counts_as_fallback(self):
        metrics = LLMMetrics()
        with patch("bawue.bawue_dok.litellm.acompletion", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await vorgang_kurztitel(_llm(), TITEL, SUMMARY, cache=_cache(), metrics=metrics)

        assert vars(metrics) == _only("kurztitel_fallback")

    @pytest.mark.asyncio
    async def test_nothing_is_counted_without_an_llm(self):
        metrics = LLMMetrics()
        await vorgang_kurztitel(None, TITEL, SUMMARY, metrics=metrics)

        assert vars(metrics) == _only(None)


def _vorgang_metrics() -> LLMMetrics:
    metrics = LLMMetrics()
    metrics.kurztitel_generated, metrics.kurztitel_fallback = 12, 1
    metrics.ressort_classified, metrics.ressort_null, metrics.ressort_rejected = 10, 2, 1
    return metrics


class TestReport:
    def test_vorgang_counters_have_their_own_block(self):
        lines = _vorgang_metrics().format_lines()

        assert "LLM enrichment:" not in lines
        assert lines[-3:] == [
            "LLM per Vorgang:",
            "  Kurztitel:   12 generated, 1 fallback to titel",
            "  Ressort:     10 classified, 2 null, 1 rejected, 0 failed",
        ]

    @pytest.mark.parametrize("counter", VORGANG_COUNTERS)
    def test_any_single_counter_shows_the_block(self, counter):
        """Above all a run where every call failed — a provider outage — must show."""
        metrics = LLMMetrics()
        setattr(metrics, counter, 1)

        assert "LLM per Vorgang:" in metrics.format_lines()

    def test_document_counts_alone_show_no_vorgang_block(self):
        metrics = LLMMetrics()
        metrics.success = 3

        assert "LLM per Vorgang:" not in metrics.format_lines()

    def test_vorgaenge_report_shows_counters_without_enriched_documents(self):
        """A run whose only changed Vorgang has an unpublished PDF enriches nothing,
        but still classified that Vorgang."""
        lines = _print_vorgaenge_summary(
            18,
            {},
            new_or_retried=1,
            changed=0,
            cached=0,
            published=1,
            skipped=0,
            failed=0,
            duration=1.0,
            llm_metrics=_vorgang_metrics(),
        )

        assert "LLM per Vorgang:" in lines

    def test_beteiligung_report_shows_counters_without_enriched_documents(self):
        lines = _print_beteiligung_summary(1, 0, 1, 0, 0, 1.0, _vorgang_metrics())

        assert "LLM per Vorgang:" in lines
