"""Tests for the scraping cycle in bawue.__main__."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from bawue.__main__ import load_config, log_startup, main, run
from bawue.bawue_dok import _hash_cache


class _FakeScraper:
    def __init__(self, title: str | None, fail: bool = False):
        self._title = title
        self._fail = fail
        self.summary = None

    async def run(self) -> None:
        if self._title is not None:
            self.summary = (self._title, [f"{self._title} line"])
        if self._fail:
            raise RuntimeError(f"{self._title} boom")


def _config(linearize: bool) -> MagicMock:
    config = MagicMock()
    config.linearize = linearize
    return config


@pytest.fixture(autouse=True)
def _no_weekly_section():
    with patch("bawue.__main__.weekly_section", return_value=None) as weekly:
        yield weekly


class TestIssue52OneRunReport:
    """Issue #52: a cycle posts one Mattermost message with every scraper's summary."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("linearize", [False, True])
    async def test_one_report_with_all_summaries_in_scraper_order(self, linearize):
        scrapers = [_FakeScraper("Vorgänge"), _FakeScraper("Beteiligung"), _FakeScraper("Sitzungen")]
        config = _config(linearize)

        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report") as send,
        ):
            await main(config)

        send.assert_called_once_with(
            config,
            [
                ("Vorgänge", ["Vorgänge line"]),
                ("Beteiligung", ["Beteiligung line"]),
                ("Sitzungen", ["Sitzungen line"]),
            ],
        )

    @pytest.mark.asyncio
    async def test_failing_scraper_still_reports(self):
        """A scraper that raises still fills its summary (run's finally); the report must go out."""
        scrapers = [_FakeScraper("Vorgänge", fail=True), _FakeScraper("Sitzungen")]

        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report") as send,
        ):
            await main(_config(False))

        assert [title for title, _ in send.call_args.args[1]] == ["Vorgänge", "Sitzungen"]

    @pytest.mark.asyncio
    # linearize leaves the scrapers after a failure un-awaited (unchanged behavior).
    @pytest.mark.filterwarnings("ignore:coroutine '_FakeScraper.run' was never awaited")
    async def test_failing_scraper_still_reports_when_linearized(self):
        scrapers = [_FakeScraper("Vorgänge", fail=True), _FakeScraper("Sitzungen")]

        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report") as send,
            pytest.raises(RuntimeError, match="Vorgänge boom"),
        ):
            await main(_config(True))

        send.assert_called_once()
        assert [title for title, _ in send.call_args.args[1]] == ["Vorgänge"]

    @pytest.mark.asyncio
    async def test_failure_is_named_in_the_report(self):
        """A crash before run() sets its summary (e.g. in check_for_newer_wahlperiode) must
        not vanish from the report; a crash after it is marked in its section."""
        scrapers = [_FakeScraper(None, fail=True), _FakeScraper("Sitzungen", fail=True)]

        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report") as send,
        ):
            await main(_config(False))

        assert send.call_args.args[1] == [
            ("_FakeScraper", ["FAILED: RuntimeError: None boom"]),
            ("Sitzungen", ["Sitzungen line", "FAILED: RuntimeError: Sitzungen boom"]),
        ]

    @pytest.mark.asyncio
    async def test_scraper_without_summary_is_left_out(self):
        scrapers = [_FakeScraper(None), _FakeScraper("Sitzungen")]

        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report") as send,
        ):
            await main(_config(False))

        assert send.call_args.args[1] == [("Sitzungen", ["Sitzungen line"])]


class TestIssue101HashCachePerCycle:
    """Issue #101: the cycle, not each scraper, clears the shared semantics cache."""

    @pytest.mark.asyncio
    async def test_cycle_starts_with_empty_hash_cache(self):
        _hash_cache["last-cycle"] = {}
        seen = []

        class _Probe(_FakeScraper):
            async def run(self) -> None:
                seen.append(dict(_hash_cache))

        try:
            with (
                patch("bawue.__main__.load_scrapers", return_value=[_Probe(None)]),
                patch("bawue.__main__.send_run_report"),
            ):
                await main(_config(False))
        finally:
            _hash_cache.clear()

        assert seen == [{}]


class TestIssue82WeeklySection:
    @pytest.mark.asyncio
    async def test_weekly_section_ends_the_report(self, _no_weekly_section):
        _no_weekly_section.return_value = ("Wochenbericht", ["🆕 A: Eingebracht"])

        with (
            patch("bawue.__main__.load_scrapers", return_value=[_FakeScraper("Vorgänge")]),
            patch("bawue.__main__.send_run_report") as send,
        ):
            await main(_config(False))

        assert send.call_args.args[1] == [("Vorgänge", ["Vorgänge line"]), ("Wochenbericht", ["🆕 A: Eingebracht"])]


class TestIssue84StartupVersion:
    """Issue #84: the run log names the image's version (Dockerfile ARG/ENV SCRAPER_VERSION)."""

    @pytest.mark.parametrize(
        ("env", "version"),
        [
            ("2.1.1", "2.1.1"),
            ("", "dev"),  # an empty build-arg
            (None, "dev"),  # local run, no image
        ],
    )
    def test_startup_line_names_the_version(self, monkeypatch, caplog, env, version):
        if env is None:
            monkeypatch.delenv("SCRAPER_VERSION", raising=False)
        else:
            monkeypatch.setenv("SCRAPER_VERSION", env)
        with caplog.at_level(logging.INFO, logger="bawue"):
            log_startup()
        assert caplog.messages == [f"Starting BaWue scraper manager (version {version})."]


class TestRedisUnavailableAtStartup:
    def test_alerts_and_exits(self):
        from bawue.cache import RedisUnavailableError

        with (
            patch("bawue.__main__.BawueConfig.load", side_effect=RedisUnavailableError("redis.example:6379")),
            patch("bawue.__main__.send_alert") as alert,
            pytest.raises(SystemExit) as exc,
        ):
            load_config()
        assert exc.value.code == 1
        assert "redis.example:6379" in alert.call_args.args[1]


class TestCycleFailure:
    """A cycle that raises must reach Mattermost and fail the Cloud Run execution (exit 1)."""

    def test_raising_cycle_alerts_and_propagates(self):
        async def boom(_config):
            raise RuntimeError("constructor\n  boom")

        config = _config(False)
        with (
            patch("bawue.__main__.main", new=boom),
            patch("bawue.__main__.send_alert") as alert,
            pytest.raises(RuntimeError),
        ):
            run(config)
        alert.assert_called_once_with(config, "Scraping cycle failed: RuntimeError: constructor boom")
