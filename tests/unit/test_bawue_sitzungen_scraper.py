"""Tests for the BawueSitzungenScraper."""

import datetime
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import openapi_client
import pytest
import pytest_asyncio

from bawue.bawue_sitzungen_scraper import DEFAULT_ICS_URL, BawueSitzungenScraper

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ICS_BYTES = (FIXTURES_DIR / "sample_calendar.ics").read_bytes()

ICS_URL = "https://www.landtag-bw.de/resource/calendar/501552/download/terminkalender.ics"


def _make_scraper() -> BawueSitzungenScraper:
    """Create a BawueSitzungenScraper without calling __init__."""
    from bawue.rate_limiter import AdaptiveRateLimiter

    scraper = object.__new__(BawueSitzungenScraper)
    scraper._wahlperiode = 17
    scraper._events_by_date = {}
    scraper.listing_urls = [ICS_URL]
    scraper.session = MagicMock()
    scraper.scraper_id = "00000000-0000-0000-0000-000000000001"
    scraper._upload_limiter = AdaptiveRateLimiter(
        initial_delay=0.2, min_delay=0.05, backoff_multiplier=10.0, recovery_factor=0.5
    )
    scraper._total_events = 0
    scraper._total_dates = 0
    scraper._published_dates = 0
    scraper._failed_dates = 0
    scraper._published_sitzungen = 0
    return scraper


@pytest_asyncio.fixture
async def ics_scraper_with_events():
    """Return a scraper with ICS events already loaded via listing_page_extractor."""
    scraper = _make_scraper()
    mock_response = AsyncMock()
    mock_response.read = AsyncMock(return_value=ICS_BYTES)
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    scraper.session.get = MagicMock(return_value=mock_response)
    await scraper.listing_page_extractor(ICS_URL)
    return scraper


class TestListingPageExtractor:
    """Test that listing_page_extractor fetches ICS and returns date keys."""

    @pytest.mark.asyncio
    async def test_returns_date_keys(self):
        scraper = _make_scraper()

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        scraper.session.get = MagicMock(return_value=mock_response)

        date_keys = await scraper.listing_page_extractor(ICS_URL)

        # Should return ISO date strings for each unique date with included events
        assert isinstance(date_keys, list)
        assert "2026-02-24" in date_keys
        assert "2026-02-25" in date_keys
        assert "2026-02-26" in date_keys
        assert "2026-03-03" in date_keys
        assert len(date_keys) == 4

    @pytest.mark.asyncio
    async def test_populates_events_by_date(self):
        scraper = _make_scraper()

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        scraper.session.get = MagicMock(return_value=mock_response)

        await scraper.listing_page_extractor(ICS_URL)

        # events_by_date should have entries for all included dates
        assert "2026-02-24" in scraper._events_by_date
        assert len(scraper._events_by_date["2026-02-24"]) == 2  # Ausschuesse + FinA


class TestItemExtractor:
    """Test that item_extractor converts stored events into (datetime, List[Sitzung])."""

    @pytest.mark.asyncio
    async def test_returns_datetime_and_sitzungen(self, ics_scraper_with_events):
        result = await ics_scraper_with_events.item_extractor("2026-02-25")

        termin, sitzungen = result
        assert isinstance(termin, datetime.datetime)
        assert len(sitzungen) == 1

        sitzung = sitzungen[0]
        assert sitzung.gremium.name == "Plenum"
        assert sitzung.gremium.parlament.value == "BW"
        assert sitzung.gremium.wahlperiode == 17
        assert sitzung.nummer == 142
        assert sitzung.public is True
        assert sitzung.tops == []
        assert sitzung.titel == "Plenarsitzung: 142. Sitzung"

    @pytest.mark.asyncio
    async def test_deterministic_api_id(self, ics_scraper_with_events):
        """Same UID should produce the same api_id across runs."""
        result1 = await ics_scraper_with_events.item_extractor("2026-02-25")

        # Re-populate for second extraction
        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        ics_scraper_with_events.session.get = MagicMock(return_value=mock_response)
        await ics_scraper_with_events.listing_page_extractor(ICS_URL)

        result2 = await ics_scraper_with_events.item_extractor("2026-02-25")

        assert result1[1][0].api_id == result2[1][0].api_id

    @pytest.mark.asyncio
    async def test_multiple_sitzungen_per_date(self, ics_scraper_with_events):
        result = await ics_scraper_with_events.item_extractor("2026-02-24")

        _termin, sitzungen = result
        assert len(sitzungen) == 2
        gremium_names = {s.gremium.name for s in sitzungen}
        assert "Ausschusssitzungen" in gremium_names
        assert "Finanzausschuss" in gremium_names

    @pytest.mark.asyncio
    async def test_datetimes_are_utc(self, ics_scraper_with_events):
        """Tuple termin and individual Sitzung termins must both be timezone-aware (UTC)."""
        result = await ics_scraper_with_events.item_extractor("2026-02-25")
        termin, sitzungen = result
        assert termin.tzinfo is not None
        assert sitzungen[0].termin.tzinfo is not None


class TestInit:
    def test_init_reads_ics_url_from_config(self, tmp_path):
        config_file = tmp_path / "config.toml"
        config_file.write_text('[bawue]\nics-url = "https://custom.example.com/cal.ics"\n')

        mock_config = MagicMock()
        mock_config.config_file = str(config_file)
        mock_config.collector_id = "00000000-0000-0000-0000-000000000001"

        with patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.__init__", return_value=None) as mock_super:
            BawueSitzungenScraper(mock_config, MagicMock())

        # super().__init__(config, collector_id, [ics_url], session) → args[2] is listing_urls
        assert mock_super.call_args.args[2] == ["https://custom.example.com/cal.ics"]

    def test_init_uses_default_ics_url(self):
        mock_config = MagicMock()
        mock_config.config_file = None
        mock_config.collector_id = "00000000-0000-0000-0000-000000000001"

        with patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.__init__", return_value=None) as mock_super:
            BawueSitzungenScraper(mock_config, MagicMock())

        assert mock_super.call_args.args[2] == [DEFAULT_ICS_URL]

    def test_load_bawue_config_returns_empty_on_no_file(self):
        mock_config = MagicMock()
        mock_config.config_file = None

        assert BawueSitzungenScraper._load_bawue_config(mock_config) == {}

    def test_load_bawue_config_returns_empty_on_bad_file(self, tmp_path, caplog):
        mock_config = MagicMock()
        mock_config.config_file = str(tmp_path / "nonexistent.toml")

        with caplog.at_level(logging.WARNING, logger="bawue.bawue_sitzungen_scraper"):
            result = BawueSitzungenScraper._load_bawue_config(mock_config)

        assert result == {}
        assert any("Could not load" in msg for msg in caplog.messages)


class TestSendResult:
    """Test that send_result uses Parlament.BW instead of BY."""

    @pytest.mark.asyncio
    async def test_send_result_uses_bw(self):
        scraper = _make_scraper()
        scraper.config = MagicMock()
        scraper.config.api_obj_log = None

        mock_api_instance = MagicMock()
        mock_api_instance.kal_date_put = MagicMock(return_value="ok")

        with (
            patch("bawue.bawue_sitzungen_scraper.openapi_client.ApiClient") as mock_client_cls,
            patch(
                "bawue.bawue_sitzungen_scraper.openapi_client.api.collector_schnittstellen_api.CollectorSchnittstellenApi",
                return_value=mock_api_instance,
            ),
        ):
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            await scraper.send_result(item)

            call_kwargs = mock_api_instance.kal_date_put.call_args
            # Verify Parlament.BW is used, not BY
            from openapi_client.models import Parlament

            assert call_kwargs.kwargs.get("parlament") or call_kwargs[1].get("parlament")
            # Check the actual value
            args = call_kwargs[1] if call_kwargs[1] else {}
            kwargs = call_kwargs.kwargs if hasattr(call_kwargs, "kwargs") else {}
            all_args = {**args, **kwargs}
            assert all_args["parlament"] == Parlament.BW


    @pytest.mark.asyncio
    async def test_send_result_422_returns_none_and_logs(self, caplog):
        scraper = _make_scraper()
        scraper.config = MagicMock()
        scraper.log_item = MagicMock()

        exc = openapi_client.ApiException(status=422, reason="Unprocessable Entity")
        mock_api_instance = MagicMock()
        mock_api_instance.kal_date_put = MagicMock(side_effect=exc)

        with (
            patch("bawue.bawue_sitzungen_scraper.openapi_client.ApiClient") as mock_client_cls,
            patch(
                "bawue.bawue_sitzungen_scraper.openapi_client.api.collector_schnittstellen_api.CollectorSchnittstellenApi",
                return_value=mock_api_instance,
            ),
            caplog.at_level(logging.ERROR, logger="bawue.bawue_sitzungen_scraper"),
        ):
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            result = await scraper.send_result(item)

        assert result is None
        assert any("422" in msg or "Unprocessable" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_send_result_401_logs_critical(self, caplog):
        scraper = _make_scraper()
        scraper.config = MagicMock()
        scraper.log_item = MagicMock()

        exc = openapi_client.ApiException(status=401, reason="Unauthorized")
        mock_api_instance = MagicMock()
        mock_api_instance.kal_date_put = MagicMock(side_effect=exc)

        with (
            patch("bawue.bawue_sitzungen_scraper.openapi_client.ApiClient") as mock_client_cls,
            patch(
                "bawue.bawue_sitzungen_scraper.openapi_client.api.collector_schnittstellen_api.CollectorSchnittstellenApi",
                return_value=mock_api_instance,
            ),
            caplog.at_level(logging.CRITICAL, logger="bawue.bawue_sitzungen_scraper"),
        ):
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            result = await scraper.send_result(item)

        assert result is None
        assert any("Authentication failed" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_send_result_unexpected_exception_returns_none(self, caplog):
        scraper = _make_scraper()
        scraper.config = MagicMock()

        mock_api_instance = MagicMock()
        mock_api_instance.kal_date_put = MagicMock(side_effect=Exception("boom"))

        with (
            patch("bawue.bawue_sitzungen_scraper.openapi_client.ApiClient") as mock_client_cls,
            patch(
                "bawue.bawue_sitzungen_scraper.openapi_client.api.collector_schnittstellen_api.CollectorSchnittstellenApi",
                return_value=mock_api_instance,
            ),
            caplog.at_level(logging.ERROR, logger="bawue.bawue_sitzungen_scraper"),
        ):
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client_cls.return_value)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            result = await scraper.send_result(item)

        assert result is None
        assert any("Unexpected error" in msg for msg in caplog.messages)


class TestRunSummary:
    @pytest.mark.asyncio
    async def test_summary_printed_to_stdout(self, capsys):
        scraper = _make_scraper()

        with patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Sitzungen Run Summary ===" in captured.out

    @pytest.mark.asyncio
    async def test_summary_shows_published_dates(self, capsys):
        scraper = _make_scraper()
        scraper._total_dates = 5
        scraper._published_dates = 3

        with patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Dates published:" in captured.out
        assert "3" in captured.out

    @pytest.mark.asyncio
    async def test_summary_shows_failed_dates(self, capsys):
        scraper = _make_scraper()
        scraper._failed_dates = 2

        with patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Dates failed:" in captured.out
        assert "2" in captured.out

    @pytest.mark.asyncio
    async def test_summary_shows_total_sitzungen(self, capsys):
        scraper = _make_scraper()
        scraper._published_sitzungen = 7

        with patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Total sitzungen:" in captured.out
        assert "7" in captured.out

    @pytest.mark.asyncio
    async def test_summary_still_printed_on_run_failure(self, capsys):
        scraper = _make_scraper()

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=mock_run),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Sitzungen Run Summary ===" in captured.out


class TestRunDurationLog:
    @pytest.mark.asyncio
    async def test_logs_completed_in_on_success(self, caplog):
        scraper = _make_scraper()

        with (
            patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()),
            caplog.at_level(logging.INFO, logger="bawue.bawue_sitzungen_scraper"),
        ):
            await scraper.run()

        assert any("Completed in" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_logs_completed_in_on_failure(self, caplog):
        scraper = _make_scraper()

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=mock_run),
            caplog.at_level(logging.INFO, logger="bawue.bawue_sitzungen_scraper"),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        assert any("Completed in" in msg for msg in caplog.messages)
