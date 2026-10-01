"""Tests for the scraping cycle in bawue.__main__."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from bawue.__main__ import log_startup, main


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
