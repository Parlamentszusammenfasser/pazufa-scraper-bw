"""Run metrics to the Pushgateway, logs to Loki (issue #188, DD-082).

Metric names and labels come from the shared pazufa-metrics-client, as agreed on
https://wiki.pazufa.de/de/Organisation/Tools/Observability. Both push only with a password,
so local runs log the metrics (dry run) and ship nothing.
"""

import atexit
import logging
import os
import sys
import time
from collections import Counter
from logging.handlers import BufferingHandler, QueueHandler, QueueListener
from queue import SimpleQueue

import requests
from pazufa_corelib.llm import LLMConnector
from pazufa_metrics_client import PazufaMetricsClient

from bawue.bawue_dok import LLMMetrics
from bawue.cache import LAST_SUCCESS_KEY, RedisUnavailableError
from bawue.config import BawueConfig
from bawue.notifications import extract_environment
from bawue.run_report import FailedItem
from bawue.wahlperiode import CURRENT_WAHLPERIODE

M = PazufaMetricsClient
# The Cloud Run job says "prod"; the wiki's label value is "production". Local runs are development.
_ENVIRONMENTS = {"prod": M.ENVIRONMENT_PRODUCTION, "staging": M.ENVIRONMENT_STAGING}


def _environment(config: BawueConfig) -> str:
    return _ENVIRONMENTS.get(extract_environment(config), M.ENVIRONMENT_DEVELOPMENT)


def record_entities(
    pmc: PazufaMetricsClient,
    entity: str,
    *,
    new: int,
    cached: int,
    accepted: int,
    failed_items: list[FailedItem],
    changed: int = 0,
    skipped: int = 0,
    cache_errors: int = 0,
) -> None:
    """One scraper's counts. *new* includes retried ones: a cache miss can't tell them apart."""
    pmc.increaseMetricEntitiesFound(new, entity, M.STATE_NEW)
    pmc.increaseMetricEntitiesFound(changed, entity, M.STATE_CHANGED)
    pmc.increaseMetricEntitiesFound(cached, entity, M.STATE_CACHED)
    pmc.increaseMetricEntitiesSubmitted(accepted, entity, M.RESULT_ACCEPTED)
    pmc.increaseMetricEntitiesSkipped(skipped, entity)
    for status, count in Counter(item.status for item in failed_items).items():
        if status is None:  # no HTTP answer: build error or connection failure
            pmc.increaseMetricEntitiesFailed(count, entity, "exception")
        else:
            pmc.increaseMetricEntitiesSubmitted(count, entity, M.RESULT_REJECTED, statuscode=status)
    pmc.increaseMetricEntitiesFailed(cache_errors, entity, "cache_error")


def record_llm(pmc: PazufaMetricsClient, llm: LLMMetrics, connector: LLMConnector | None) -> None:
    if connector is not None:
        # reasoning is part of completion: sum only prompt + completion for the total.
        for kind in ("prompt", "completion", "reasoning"):
            pmc.increaseMetric("llm_tokens", getattr(connector, f"{kind}_tokens"), {"type": kind})
    doc, proc = M.ENTITY_DOCUMENT, M.ENTITY_PROCEDURE
    pmc.increaseMetricLLMEnrichment(llm.success, doc, M.RESULT_ENRICHED)
    pmc.increaseMetricLLMEnrichment(llm.failed, doc, M.RESULT_FAILED)
    pmc.increaseMetricLLMEnrichment(llm.cache_hits, doc, M.RESULT_CACHEHIT)
    pmc.increaseMetricLLMEnrichment(llm.kurztitel_generated, proc, M.RESULT_ENRICHED, "Kurztitel")
    pmc.increaseMetricLLMEnrichment(llm.kurztitel_fallback, proc, M.RESULT_FAILED, "Kurztitel")
    # A null Ressort is an answer (no ministry fits), not a failure.
    pmc.increaseMetricLLMEnrichment(llm.ressort_classified + llm.ressort_null, proc, M.RESULT_ENRICHED, "Ressort")
    pmc.increaseMetricLLMEnrichment(llm.ressort_rejected + llm.ressort_failed, proc, M.RESULT_FAILED, "Ressort")


def _last_success(config: BawueConfig, success: bool) -> float | None:
    """Now after a successful cycle, else the stored time: each push replaces the whole group."""
    if success:
        now = time.time()
        config.cache.store_raw(LAST_SUCCESS_KEY, str(now), "Last success")
        return now
    try:
        stored = config.cache.get_raw(LAST_SUCCESS_KEY, "Last success")
    except RedisUnavailableError:
        return None
    return float(stored) if stored else None


def push_run_metrics(config: BawueConfig, scrapers: list, duration: float, success: bool) -> None:
    """Push after every cycle; last_success_timestamp_seconds is the heartbeat (DD-082)."""
    pmc = M(host=config.prometheus_url, username=config.observability_username, password=config.observability_password)
    pmc.setScraperUUID(config.collector_id).setParliament(M.PARLIAMENT_BW).setEnvironment(_environment(config))
    pmc.setElectionPeriod(config.section("bawue").get("wahlperiode", CURRENT_WAHLPERIODE))
    pmc.registerMetric("llm_tokens", ["type"])  # once: both LLM scrapers add into it (record_llm)
    for scraper in scrapers:
        scraper.record_metrics(pmc)
    pmc.increaseMetricRuntime(duration)
    pmc.registerMetric("info", ["version"])
    pmc.increaseMetric("info", 1, {"version": os.environ.get("SCRAPER_VERSION") or "dev"})
    if (last_success := _last_success(config, success)) is not None:
        pmc.registerMetric("last_success_timestamp_seconds")
        pmc.increaseMetric("last_success_timestamp_seconds", last_success)
    pmc.pushMetrics(dryrun=not config.observability_password)


class LokiHandler(BufferingHandler):
    """Pushes buffered records to Loki, one request per batch, the level as a label.

    After a failed push it stops shipping: an unreachable Loki then costs one timeout per run,
    not one per batch. stdout still reaches Cloud Logging either way.
    """

    def __init__(self, url: str, auth: tuple[str, str], labels: dict[str, str], capacity: int = 500) -> None:
        super().__init__(capacity)
        self.labels = labels
        self._url = url.rstrip("/") + "/loki/api/v1/push"
        self._auth = auth
        self._broken = False

    def flush(self) -> None:
        with self.lock:
            records, self.buffer = self.buffer, []
            if not records or self._broken:
                return
            streams: dict[str, list[list[str]]] = {}
            for record in records:
                streams.setdefault(record.levelname, []).append([str(int(record.created * 1e9)), self.format(record)])
            body = {"streams": [{"stream": {**self.labels, "level": lvl}, "values": v} for lvl, v in streams.items()]}
            try:
                requests.post(self._url, json=body, auth=self._auth, timeout=10).raise_for_status()
            except Exception as e:
                self._broken = True
                # Not via logging: this handler would receive it. Only the type: no URL or credentials.
                print(f"Loki push failed, log shipping stopped: {type(e).__name__}", file=sys.stderr)


def add_loki_handler(config: BawueConfig) -> QueueListener | None:
    """Ship this process's logs to Loki; None without a password.

    The push runs on the listener's thread: a slow Loki must not stall the event loop or
    the threads that log.

    shortcut: records still buffered when Cloud Run kills the job (timeout, OOM) are lost;
    Cloud Logging keeps them.
    """
    if not config.observability_password:
        return None
    labels = {
        "job": "pazufa_scraper",
        "scraper_uuid": config.collector_id,
        "parliament": M.PARLIAMENT_BW,
        "environment": _environment(config),
    }
    handler = LokiHandler(config.loki_url, (config.observability_username, config.observability_password), labels)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    queue: SimpleQueue = SimpleQueue()
    listener = QueueListener(queue, handler)
    listener.start()
    # atexit runs last-registered first: the queue is drained before logging.shutdown flushes.
    atexit.register(listener.stop)
    logging.getLogger().addHandler(QueueHandler(queue))
    return listener
