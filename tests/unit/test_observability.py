"""Issue #188: run metrics to the Pushgateway and logs to Loki (DD-082)."""

import json
import logging
import time
from unittest.mock import ANY, MagicMock, patch

import litellm
import pytest
import responses as responses_lib
from pazufa_metrics_client import PazufaMetricsClient

from bawue.bawue_dok import LLMMetrics
from bawue.cache import RedisUnavailableError
from bawue.observability import LokiHandler, add_loki_handler, push_run_metrics, record_entities, record_llm
from bawue.run_report import FailedItem


def _value(registry, name, **labels):
    return registry.get_sample_value(f"pazufa_scraper_{name}", labels)


class TestRecordEntities:
    def test_counts_by_state_result_status_and_reason(self):
        pmc = PazufaMetricsClient()
        failed = [
            FailedItem("V-1", None, "HTTP 422 Unprocessable Entity", status=422),
            FailedItem("V-2", None, "HTTP 422 Unprocessable Entity", status=422),
            FailedItem("V-3", None, "KeyError: 'datum'"),  # lost before any HTTP answer
        ]

        record_entities(
            pmc, "procedure", new=4, changed=1, cached=7, accepted=2, skipped=1, failed_items=failed, cache_errors=2
        )

        reg = pmc._registry
        assert [_value(reg, "entities_found", entity="procedure", state=s) for s in ("new", "changed", "cached")] == [
            4,
            1,
            7,
        ]
        assert _value(reg, "entities_submitted", entity="procedure", result="accepted", statuscode="") == 2
        assert _value(reg, "entities_submitted", entity="procedure", result="rejected", statuscode="422") == 2
        assert _value(reg, "entities_skipped", entity="procedure", reason="") == 1
        assert _value(reg, "entities_failed", entity="procedure", reason="exception") == 1
        assert _value(reg, "entities_failed", entity="procedure", reason="cache_error") == 2

    def test_scrapers_of_one_entity_add_up(self):
        """Vorgänge and Beteiligung both report procedures into the same series."""
        pmc = PazufaMetricsClient()
        record_entities(pmc, "procedure", new=3, cached=1, accepted=3, failed_items=[])
        record_entities(pmc, "procedure", new=2, cached=4, accepted=1, failed_items=[])

        assert _value(pmc._registry, "entities_found", entity="procedure", state="new") == 5
        assert _value(pmc._registry, "entities_found", entity="procedure", state="cached") == 5


def test_record_llm_maps_documents_and_vorgang_fields():
    llm = LLMMetrics()
    llm.success, llm.failed, llm.cache_hits = 3, 1, 5
    llm.kurztitel_generated, llm.kurztitel_fallback = 2, 1
    llm.ressort_classified, llm.ressort_null, llm.ressort_rejected, llm.ressort_failed = 2, 1, 1, 1
    pmc = PazufaMetricsClient()

    record_llm(pmc, llm, None)

    reg = pmc._registry
    document = [_value(reg, "llmenrichment", entity="document", result=r, field="") for r in ("enriched", "failed")]
    assert [*document, _value(reg, "llmenrichment", entity="document", result="cachehit", field="")] == [3, 1, 5]
    for field, enriched, failed in (("Kurztitel", 2, 1), ("Ressort", 3, 2)):  # a null Ressort is an answer
        assert _value(reg, "llmenrichment", entity="procedure", result="enriched", field=field) == enriched
        assert _value(reg, "llmenrichment", entity="procedure", result="failed", field=field) == failed


def _config(password: str | None) -> MagicMock:
    config = MagicMock()
    config.config_file = None
    config.collector_id = "uuid-bw"
    config.prometheus_url = "https://prom.example/"
    config.loki_url = "https://loki.example/"
    config.observability_username = "LogPush"
    config.observability_password = password
    config.section.return_value = {"wahlperiode": 18}
    return config


class TestPushRunMetrics:
    @pytest.mark.parametrize(("env", "label"), [("prod", "production"), ("staging", "staging"), ("", "development")])
    def test_one_group_per_deployment(self, monkeypatch, env, label):
        monkeypatch.setenv("ENVIRONMENT", env)
        monkeypatch.setenv("SCRAPER_VERSION", "1.2.3")
        scraper = MagicMock()

        config = _config("secret")
        with patch("pazufa_metrics_client.push_to_gateway") as push:
            push_run_metrics(config, [scraper], 26.0, success=True)

        scraper.record_metrics.assert_called_once()
        kwargs = push.call_args.kwargs
        assert kwargs["grouping_key"] == {
            "scraper_uuid": "uuid-bw",
            "parliament": "BW",
            "environment": label,
            "electionperiod": "18",
        }
        assert _value(kwargs["registry"], "runtime_seconds", entity="") == 26.0
        assert _value(kwargs["registry"], "info", version="1.2.3") == 1
        assert _value(kwargs["registry"], "last_success_timestamp_seconds") == pytest.approx(time.time(), abs=5)
        config.cache.store_raw.assert_called_once_with("metrics-last-success", ANY, "Last success")

    @pytest.mark.parametrize(
        ("stored", "heartbeat"),
        [("1700000000.0", 1700000000.0), (None, None), (RedisUnavailableError("down"), None)],
        ids=["stored", "never-succeeded", "redis-down"],
    )
    def test_failed_cycle_keeps_the_last_success(self, stored, heartbeat):
        """Each push replaces the whole group: without the stored value the heartbeat would vanish."""
        config = _config("secret")
        config.cache.get_raw.side_effect = [stored]
        with patch("pazufa_metrics_client.push_to_gateway") as push:
            push_run_metrics(config, [], 1.0, success=False)

        assert _value(push.call_args.kwargs["registry"], "last_success_timestamp_seconds") == heartbeat
        config.cache.store_raw.assert_not_called()

    @pytest.mark.parametrize("model", ["gpt-5-nano", "no-such-model"], ids=["priced", "unknown-model"])
    def test_llm_tokens_and_cost_of_all_scrapers_add_up(self, model, caplog):
        connector = MagicMock(model=model, prompt_tokens=100, completion_tokens=40, reasoning_tokens=10)
        scrapers = [MagicMock(), MagicMock(), MagicMock()]
        for scraper, conn in zip(scrapers, (connector, connector, None), strict=True):  # the last has LLM off
            scraper.record_metrics.side_effect = lambda pmc, conn=conn: record_llm(pmc, LLMMetrics(), conn)

        with patch("pazufa_metrics_client.push_to_gateway") as push:
            push_run_metrics(_config("secret"), scrapers, 1.0, success=True)

        reg = push.call_args.kwargs["registry"]
        assert [_value(reg, "llm_tokens", type=t) for t in ("prompt", "completion", "reasoning")] == [200, 80, 20]
        priced = model == "gpt-5-nano"
        # Price from litellm's table, not hardcoded; an unknown model gets no series rather than a cost of 0.
        expected = pytest.approx(2 * sum(litellm.cost_per_token(model, 100, 40))) if priced else None
        assert _value(reg, "llm_cost_usd", model=model) == expected
        assert ("No LLM price for no-such-model" in caplog.text) is not priced

    def test_nothing_is_pushed_without_password(self):
        with patch("pazufa_metrics_client.push_to_gateway") as push:
            push_run_metrics(_config(None), [], 1.0, success=True)
        push.assert_not_called()


class TestLokiHandler:
    PUSH_URL = "https://loki.example/loki/api/v1/push"

    @staticmethod
    def _logger(handler: logging.Handler) -> logging.Logger:
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logger = logging.getLogger("test.loki")
        logger.handlers, logger.propagate = [handler], False
        logger.setLevel(logging.INFO)
        return logger

    @responses_lib.activate
    def test_one_push_per_batch_with_level_as_label(self):
        responses_lib.add(responses_lib.POST, self.PUSH_URL, status=204)
        handler = LokiHandler("https://loki.example/", ("LogPush", "pw"), {"job": "pazufa_scraper"}, capacity=3)
        logger = self._logger(handler)

        logger.info("one")
        logger.error("two")
        assert len(responses_lib.calls) == 0
        logger.info("three")
        logger.info("four")
        handler.close()  # logging.shutdown at exit pushes the rest

        assert len(responses_lib.calls) == 2
        request = responses_lib.calls[0].request
        assert request.headers["Authorization"].startswith("Basic ")
        streams = json.loads(request.body)["streams"]
        assert {s["stream"]["level"]: [v[1] for v in s["values"]] for s in streams} == {
            "INFO": ["test.loki: one", "test.loki: three"],
            "ERROR": ["test.loki: two"],
        }
        assert all(s["stream"]["job"] == "pazufa_scraper" for s in streams)

    @responses_lib.activate
    def test_failed_push_stops_shipping_without_raising(self, capsys):
        responses_lib.add(responses_lib.POST, self.PUSH_URL, status=401)
        logger = self._logger(LokiHandler("https://loki.example", ("LogPush", "wrong"), {}, capacity=1))

        logger.info("a")
        logger.info("b")  # an unreachable Loki costs one timeout per run, not one per batch

        assert len(responses_lib.calls) == 1
        assert "Loki push failed" in capsys.readouterr().err

    @pytest.mark.parametrize("password", [None, "secret"])
    def test_added_only_with_password(self, monkeypatch, password):
        monkeypatch.setenv("ENVIRONMENT", "staging")
        root = logging.getLogger()
        before = list(root.handlers)
        listener = add_loki_handler(_config(password))
        try:
            if password is None:
                assert listener is None
                return
            [loki] = listener.handlers
            assert loki.labels == {
                "job": "pazufa_scraper",
                "scraper_uuid": "uuid-bw",
                "parliament": "BW",
                "environment": "staging",
            }
            # Records reach it through a queue, so the push runs off the logging thread.
            logging.getLogger("queue.test").warning("queued")
            listener.stop()
            assert [r.getMessage() for r in loki.buffer if r.name == "queue.test"] == ["queued"]
        finally:
            if listener:
                listener.stop()
                listener.handlers[0].buffer.clear()  # nothing for logging.shutdown to push
            root.handlers = before
