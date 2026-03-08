"""Tests for the BawueSitzungenScraper."""

import datetime
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bawue.bawue_sitzungen_scraper import BawueSitzungenScraper

FIXTURES_DIR = Path(__file__).parent / "fixtures"
ICS_BYTES = (FIXTURES_DIR / "sample_calendar.ics").read_bytes()

ICS_URL = "https://www.landtag-bw.de/resource/calendar/501552/download/terminkalender.ics"


def _make_scraper() -> BawueSitzungenScraper:
    """Create a BawueSitzungenScraper without calling __init__."""
    scraper = object.__new__(BawueSitzungenScraper)
    scraper._wahlperiode = 17
    scraper._events_by_date = {}
    scraper.listing_urls = [ICS_URL]
    scraper.session = MagicMock()
    scraper.scraper_id = "00000000-0000-0000-0000-000000000001"
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
    async def test_returns_datetime_and_sitzungen(self):
        scraper = _make_scraper()

        # Simulate events stored by listing_page_extractor
        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        scraper.session.get = MagicMock(return_value=mock_response)
        await scraper.listing_page_extractor(ICS_URL)

        result = await scraper.item_extractor("2026-02-25")

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
    async def test_deterministic_api_id(self):
        """Same UID should produce the same api_id across runs."""
        scraper = _make_scraper()

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        scraper.session.get = MagicMock(return_value=mock_response)
        await scraper.listing_page_extractor(ICS_URL)

        result1 = await scraper.item_extractor("2026-02-25")

        # Re-populate for second extraction
        scraper.session.get = MagicMock(return_value=mock_response)
        await scraper.listing_page_extractor(ICS_URL)

        result2 = await scraper.item_extractor("2026-02-25")

        assert result1[1][0].api_id == result2[1][0].api_id

    @pytest.mark.asyncio
    async def test_multiple_sitzungen_per_date(self):
        scraper = _make_scraper()

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        scraper.session.get = MagicMock(return_value=mock_response)
        await scraper.listing_page_extractor(ICS_URL)

        result = await scraper.item_extractor("2026-02-24")

        termin, sitzungen = result
        assert len(sitzungen) == 2
        gremium_names = {s.gremium.name for s in sitzungen}
        assert "Ausschusssitzungen" in gremium_names
        assert "Finanzausschuss" in gremium_names

    @pytest.mark.asyncio
    async def test_termin_is_utc(self):
        """Termin should be timezone-aware (UTC)."""
        scraper = _make_scraper()

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        scraper.session.get = MagicMock(return_value=mock_response)
        await scraper.listing_page_extractor(ICS_URL)

        result = await scraper.item_extractor("2026-02-25")
        termin = result[0]
        assert termin.tzinfo is not None

    @pytest.mark.asyncio
    async def test_sitzung_termin_is_utc(self):
        """Individual Sitzung termin should be UTC."""
        scraper = _make_scraper()

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=ICS_BYTES)
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        scraper.session.get = MagicMock(return_value=mock_response)
        await scraper.listing_page_extractor(ICS_URL)

        result = await scraper.item_extractor("2026-02-25")
        sitzung = result[1][0]
        assert sitzung.termin.tzinfo is not None


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
