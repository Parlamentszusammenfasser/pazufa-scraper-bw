"""Tests for the scraping cycle in bawue.__main__."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from bawue.__main__ import load_config, load_scrapers, log_startup, main, run
from bawue.bawue_dok import _hash_cache


class _FakeScraper:
    def __init__(self, title: str, errors: tuple[str, ...] = (), fail: bool = False):
        self.title = title
        self.errors = list(errors)
        self._fail = fail

    async def run(self) -> None:
        if self._fail:
            raise RuntimeError(f"{self.title} boom")


def _config(linearize: bool) -> MagicMock:
    config = MagicMock()
    config.linearize = linearize
    return config


@pytest.fixture(autouse=True)
def _push():
    with patch("bawue.__main__.push_run_metrics") as push:
        yield push


class TestIssue52OneRunReport:
    """Issue #52: a cycle posts one Mattermost message; since DD-082 only with the scrapers' errors."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("linearize", [False, True])
    async def test_one_report_with_errors_in_scraper_order(self, linearize):
        scrapers = [_FakeScraper("Vorgänge", ["  - V-1"]), _FakeScraper("Beteiligung"), _FakeScraper("S", ["  - x"])]
        config = _config(linearize)

        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report") as send,
        ):
            await main(config)

        send.assert_called_once_with(config, [("Vorgänge", ["  - V-1"]), ("S", ["  - x"])])

    @pytest.mark.asyncio
    # linearize leaves the scrapers after a failure un-awaited (unchanged behavior).
    @pytest.mark.filterwarnings("ignore:coroutine '_FakeScraper.run' was never awaited")
    async def test_failing_scraper_still_reports_when_linearized(self):
        scrapers = [_FakeScraper("Vorgänge", ["  - V-1"], fail=True), _FakeScraper("Sitzungen")]

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
        """A scraper that raises is reported, with or without errors of its own."""
        scrapers = [_FakeScraper("Vorgänge", fail=True), _FakeScraper("Sitzungen", ["  - x"], fail=True)]

        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report") as send,
        ):
            await main(_config(False))

        assert send.call_args.args[1] == [
            ("Vorgänge", ["FAILED: RuntimeError: Vorgänge boom"]),
            ("Sitzungen", ["  - x", "FAILED: RuntimeError: Sitzungen boom"]),
        ]


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
                patch("bawue.__main__.load_scrapers", return_value=[_Probe("Probe")]),
                patch("bawue.__main__.send_run_report"),
            ):
                await main(_config(False))
        finally:
            _hash_cache.clear()

        assert seen == [{}]


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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("fail", "failed"), [(False, False), (True, True)])
    async def test_cycle_reports_failed_scrapers(self, fail, failed, _push):
        """Metrics only after a cycle without failed scrapers: their push time is the heartbeat (issue #188)."""
        scrapers = [_FakeScraper("Vorgänge", fail=fail), _FakeScraper("Sitzungen")]
        config = _config(False)
        with (
            patch("bawue.__main__.load_scrapers", return_value=scrapers),
            patch("bawue.__main__.send_run_report"),
        ):
            assert await main(config) is failed

        if failed:
            _push.assert_not_called()
        else:
            _push.assert_called_once_with(config, scrapers, pytest.approx(0, abs=5))

    @pytest.mark.parametrize(("failed", "code"), [(False, None), (True, 1)])
    def test_failed_scraper_fails_the_execution(self, failed, code):
        async def cycle(_config):
            return failed

        with patch("bawue.__main__.main", new=cycle), patch("bawue.__main__.send_alert") as alert:
            if code is None:
                run(_config(False))
            else:
                with pytest.raises(SystemExit) as exc:
                    run(_config(False))
                assert exc.value.code == code
        alert.assert_not_called()  # the run report already names the failure


class TestLoadScrapers:
    NAMES = ("BawueVorgaengeScraper", "BawueBeteiligungScraper", "BawueSitzungenScraper")

    @pytest.mark.parametrize(
        ("selected", "expected"),
        [
            ([], list(NAMES)),  # production: every scraper, in registry order
            (["bawuesitzungen", "BAWUEVORGAENGE"], ["BawueVorgaengeScraper", "BawueSitzungenScraper"]),
        ],
        ids=["all", "prefix-any-case"],
    )
    def test_selects_by_class_name_prefix(self, monkeypatch, selected, expected):
        fakes = [type(name, (), {"__init__": lambda self, config, session: None}) for name in self.NAMES]
        monkeypatch.setattr("bawue.__main__.SCRAPERS", fakes)

        scrapers = load_scrapers(MagicMock(scrapers=selected), MagicMock())

        assert [type(s).__name__ for s in scrapers] == expected
