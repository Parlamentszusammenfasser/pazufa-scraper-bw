"""Tests for the BawueSitzungenScraper."""

import datetime
import hashlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    scraper._fingerprints = {}
    scraper._agendas = {}
    scraper._retry_next_cycle = set()
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
    scraper._failed_items = []
    scraper._client = MagicMock()
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

        # ISO date strings for each unique date with included events
        assert sorted(date_keys) == ["2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27", "2026-03-03"]
        # Only FinA on 24 Feb — the unnamed committee block is filtered (DD-006).
        assert len(scraper._events_by_date["2026-02-24"]) == 1


class TestItemExtractor:
    """Test that item_extractor converts stored events into (datetime, List[Sitzung])."""

    @pytest.mark.asyncio
    async def test_returns_datetime_and_sitzungen(self, ics_scraper_with_events):
        termin, sitzungen = await ics_scraper_with_events.item_extractor("2026-02-25")

        assert isinstance(termin, datetime.datetime)
        assert len(sitzungen) == 1
        sitzung = sitzungen[0]
        assert sitzung.gremium.name == "plenum"
        assert sitzung.gremium.parlament.value == "BW"
        assert sitzung.gremium.wahlperiode == 17
        assert sitzung.nummer == 142
        assert sitzung.public is True
        assert sitzung.tops == []
        assert sitzung.titel == "Plenarsitzung: 142. Sitzung"
        # Tuple termin and Sitzung termin are both timezone-aware (UTC).
        assert termin.tzinfo is not None
        assert sitzung.termin.tzinfo is not None

    @pytest.mark.asyncio
    async def test_deterministic_api_id(self, ics_scraper_with_events):
        """Same UID on the same date should produce the same api_id across runs."""
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
        """On 2026-02-24 the sample feed has FinA + an unnamed committee
        block. The block is filtered out (DD-006 name-specificity), so
        only FinA remains."""
        result = await ics_scraper_with_events.item_extractor("2026-02-24")

        _termin, sitzungen = result
        assert len(sitzungen) == 1
        assert sitzungen[0].gremium.name == "Ausschuss für Finanzen"


class TestIssue65SessionNumber:
    """Issue #65: a Sitzung without a session number stays 0 and is logged (DD-064)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("date_key", "gremium"),
        [
            ("2026-02-24", "Ausschuss für Finanzen"),
            ("2026-02-27", "plenum"),  # bare "Plenarsitzung:", as in the live feed
        ],
    )
    async def test_missing_number_stays_zero_and_is_logged(self, ics_scraper_with_events, caplog, date_key, gremium):
        with caplog.at_level(logging.WARNING, logger="bawue.bawue_sitzungen_scraper"):
            _termin, sitzungen = await ics_scraper_with_events.item_extractor(date_key)

        assert sitzungen[0].nummer == 0
        assert f"No session number for {gremium} on {date_key}" in caplog.text

    @pytest.mark.asyncio
    async def test_known_number_is_not_logged(self, ics_scraper_with_events, caplog):
        with caplog.at_level(logging.WARNING, logger="bawue.bawue_sitzungen_scraper"):
            await ics_scraper_with_events.item_extractor("2026-02-25")

        assert not caplog.records


_ADDED_EVENT = (
    b"BEGIN:VEVENT\nDTSTART:20260226T140000\nDTEND:20260226T160000\nUID:evt-fina-002@landtag-bw.de\n"
    b"SUMMARY:Fraktions- und Ausschusssitzungen: FinA\nEND:VEVENT\n"
)
_ICS_ADDED_LAST = ICS_BYTES.replace(b"END:VCALENDAR", _ADDED_EVENT + b"END:VCALENDAR")
_ICS_ADDED_FIRST = ICS_BYTES.replace(b"PRODID:-//Test//Test//EN\n", b"PRODID:-//Test//Test//EN\n" + _ADDED_EVENT)


class TestSitzungenRefreshIssue85:
    """Issue #85 / DD-066: a cached date is re-uploaded once its events change, and skipped otherwise.

    Runs the real listing → cache check → extract → store pipeline; only the ICS
    download and the upload are stubbed.
    """

    @staticmethod
    def _scraper() -> BawueSitzungenScraper:
        from tests.unit.test_bawue_scraper import _InMemoryCache

        scraper = _make_scraper()
        scraper.config = MagicMock(linearize=True, max_concurrency=1, api_obj_log=None, cache=_InMemoryCache())
        scraper.items_done = 0

        async def _send(item):
            scraper.uploaded.append(item[0].date().isoformat())
            return item

        scraper.send_result = _send
        return scraper

    @staticmethod
    async def _run_cycle(scraper, ics: bytes) -> list[str]:
        response = AsyncMock(read=AsyncMock(return_value=ics))
        response.__aenter__ = AsyncMock(return_value=response)
        scraper.session.get = MagicMock(return_value=response)
        scraper.uploaded = []
        await scraper.process_items(await scraper.process_lpurls(scraper.listing_urls))
        return scraper.uploaded

    @pytest.mark.asyncio
    async def test_unchanged_feed_is_skipped(self):
        scraper = self._scraper()
        assert len(await self._run_cycle(scraper, ICS_BYTES)) == 5
        assert await self._run_cycle(scraper, ICS_BYTES) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            (ICS_BYTES, ICS_BYTES.replace(b"143. Sitzung", b"144. Sitzung")),
            (ICS_BYTES, ICS_BYTES.replace(b"DTSTART:20260226T090000", b"DTSTART:20260226T100000")),
            (ICS_BYTES, _ICS_ADDED_LAST),
            (_ICS_ADDED_LAST, ICS_BYTES),
        ],
        ids=["summary", "time", "added", "removed"],
    )
    async def test_changed_date_is_reuploaded(self, first, second):
        scraper = self._scraper()
        await self._run_cycle(scraper, first)
        assert await self._run_cycle(scraper, second) == ["2026-02-26"]

    @pytest.mark.asyncio
    async def test_parser_change_is_reuploaded(self):
        """Parsed values are hashed too: a parser fix must reach cached dates."""
        scraper = self._scraper()
        await self._run_cycle(scraper, ICS_BYTES)
        with patch("bawue.ics_parser.extract_session_number", return_value=7):
            assert len(await self._run_cycle(scraper, ICS_BYTES)) == 5

    @pytest.mark.asyncio
    async def test_wahlperiode_change_is_reuploaded(self):
        scraper = self._scraper()
        await self._run_cycle(scraper, ICS_BYTES)
        scraper._wahlperiode = 18
        assert len(await self._run_cycle(scraper, ICS_BYTES)) == 5

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            (ICS_BYTES, ICS_BYTES.replace(b"DTEND:20260226T180000", b"DTEND:20260226T190000")),
            # DTSTAMP is the download time: hashing it would re-upload every date on every run
            (ICS_BYTES, ICS_BYTES.replace(b"UID:evt-plenar-002", b"DTSTAMP:20261010T075346\nUID:evt-plenar-002")),
            (_ICS_ADDED_LAST, _ICS_ADDED_FIRST),
        ],
        ids=["dtend", "dtstamp", "order"],
    )
    async def test_irrelevant_change_is_skipped(self, first, second):
        scraper = self._scraper()
        await self._run_cycle(scraper, first)
        assert await self._run_cycle(scraper, second) == []

    @pytest.mark.asyncio
    async def test_legacy_entry_is_reuploaded_once(self):
        """Entries written before DD-066 hold the uploaded item, not a fingerprint."""
        scraper = self._scraper()
        key = await scraper.make_cache_key("2026-02-26")
        scraper.config.cache.store_raw(key, "(datetime.datetime(2026, 2, 26, 0, 0), [Sitzung(...)])")

        assert "2026-02-26" in await self._run_cycle(scraper, ICS_BYTES)
        assert await self._run_cycle(scraper, ICS_BYTES) == []


class TestIssue116MovedSession:
    """Issue #116 / DD-069: a session moved to another date keeps its UID but must not keep its api_id.

    The backend's `sitzung.api_id` is UNIQUE and `PUT kalender` only clears the target date,
    so the old date's row would make the new date's upload fail.
    """

    @staticmethod
    async def _api_id(ics: bytes, date_key: str):
        scraper = _make_scraper()
        response = AsyncMock(read=AsyncMock(return_value=ics))
        response.__aenter__ = AsyncMock(return_value=response)
        scraper.session.get = MagicMock(return_value=response)
        await scraper.listing_page_extractor(ICS_URL)
        _termin, sitzungen = await scraper.item_extractor(date_key)
        return sitzungen[0].api_id

    @pytest.mark.asyncio
    async def test_moved_session_gets_new_api_id(self):
        moved = ICS_BYTES.replace(b"DTSTART:20260226T090000", b"DTSTART:20260305T090000")

        assert await self._api_id(moved, "2026-03-05") != await self._api_id(ICS_BYTES, "2026-02-26")

    @pytest.mark.asyncio
    async def test_retimed_session_keeps_api_id(self):
        retimed = ICS_BYTES.replace(b"DTSTART:20260226T090000", b"DTSTART:20260226T100000")

        assert await self._api_id(retimed, "2026-02-26") == await self._api_id(ICS_BYTES, "2026-02-26")


class TestIssue117NamedCommittees:
    """Issue #117: an ICS entry naming several committees yields one Sitzung per committee (DD-006)."""

    @pytest.mark.asyncio
    async def test_combined_entry_yields_one_sitzung_per_committee(self):
        combined = ICS_BYTES.replace(
            b"SUMMARY:Fraktions- und Ausschusssitzungen: FinA\n",
            b"SUMMARY:Fraktions- und Ausschusssitzungen: FinA, BildungsA, VerkA\n",
        )
        scraper = _make_scraper()
        response = AsyncMock(read=AsyncMock(return_value=combined))
        response.__aenter__ = AsyncMock(return_value=response)
        scraper.session.get = MagicMock(return_value=response)
        await scraper.listing_page_extractor(ICS_URL)

        _termin, sitzungen = await scraper.item_extractor("2026-02-24")

        assert [s.gremium.name for s in sitzungen] == [
            "Ausschuss für Finanzen",
            "Ausschuss für Kultus",
            "Ausschuss für Verkehr",
        ]
        # One ICS UID, three Sitzungen: the backend's api_id is UNIQUE.
        assert len({s.api_id for s in sitzungen}) == 3


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

    def test_load_toml_section_returns_empty_on_no_file(self):
        from bawue.config_loader import load_toml_section

        mock_config = MagicMock()
        mock_config.config_file = None

        assert load_toml_section(mock_config, "bawue") == {}

    def test_load_toml_section_returns_empty_on_bad_file(self, tmp_path, caplog):
        from bawue.config_loader import load_toml_section

        mock_config = MagicMock()
        mock_config.config_file = str(tmp_path / "nonexistent.toml")

        with caplog.at_level(logging.WARNING, logger="bawue.config_loader"):
            result = load_toml_section(mock_config, "bawue")

        assert result == {}
        assert any("Could not load" in msg for msg in caplog.messages)


class TestSendResult:
    """Test that send_result uses Parlament.BW instead of BY."""

    @pytest.mark.asyncio
    async def test_send_result_uses_bw(self):
        scraper = _make_scraper()
        scraper.config = MagicMock()
        scraper.config.api_obj_log = None

        with patch("bawue.bawue_sitzungen_scraper.put_kalender") as mock_put:
            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            await scraper.send_result(item)

            # put_kalender(client, scraper_id, parlament, datum, sitzungen) — positional.
            from bawue.types import Parlament

            call = mock_put.call_args
            assert call.args[2] == Parlament.BW
            # datum is passed as a date (not a datetime).
            assert call.args[3] == datetime.date(2026, 2, 25)

    @pytest.mark.asyncio
    async def test_send_result_422_returns_none_and_logs(self, caplog):
        from bawue.api import BawueApiError

        scraper = _make_scraper()
        scraper.config = MagicMock()
        scraper.log_item = MagicMock()

        with (
            patch(
                "bawue.bawue_sitzungen_scraper.put_kalender",
                side_effect=BawueApiError(422, b"Unprocessable Entity", "kal_date_put"),
            ),
            caplog.at_level(logging.ERROR, logger="bawue.bawue_sitzungen_scraper"),
        ):
            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            result = await scraper.send_result(item)

        assert result is None
        assert any("422" in msg or "Unprocessable" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_send_result_401_logs_critical(self, caplog):
        from bawue.api import BawueApiError

        scraper = _make_scraper()
        scraper.config = MagicMock()
        scraper.log_item = MagicMock()

        with (
            patch(
                "bawue.bawue_sitzungen_scraper.put_kalender",
                side_effect=BawueApiError(401, b"Unauthorized", "kal_date_put"),
            ),
            caplog.at_level(logging.CRITICAL, logger="bawue.bawue_sitzungen_scraper"),
        ):
            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            result = await scraper.send_result(item)

        assert result is None
        assert any("Authentication failed" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_send_result_records_failed_item_with_reason(self):
        from bawue.api import BawueApiError

        scraper = _make_scraper()
        scraper.config = MagicMock()
        scraper.log_item = MagicMock()

        with patch(
            "bawue.bawue_sitzungen_scraper.put_kalender",
            side_effect=BawueApiError(500, b"Internal Server Error", "kal_date_put"),
        ):
            item = (datetime.datetime(2026, 3, 12, 10, 0, tzinfo=datetime.UTC), [])
            await scraper.send_result(item)

        assert len(scraper._failed_items) == 1
        failed = scraper._failed_items[0]
        assert failed.item_id == "2026-03-12"
        assert "500" in failed.reason

    @pytest.mark.asyncio
    async def test_send_result_unexpected_exception_returns_none(self, caplog):
        scraper = _make_scraper()
        scraper.config = MagicMock()

        with (
            patch(
                "bawue.bawue_sitzungen_scraper.put_kalender",
                side_effect=Exception("boom"),
            ),
            caplog.at_level(logging.ERROR, logger="bawue.bawue_sitzungen_scraper"),
        ):
            item = (datetime.datetime(2026, 2, 25, 10, 0, tzinfo=datetime.UTC), [])
            result = await scraper.send_result(item)

        assert result is None
        assert any("Unexpected error" in msg for msg in caplog.messages)


class TestIssue52RunReport:
    """Issue #52: cached dates are visible, and the summary goes into the cycle's
    single Mattermost message instead of its own."""

    @pytest.mark.asyncio
    async def test_found_dates_split_into_cached_and_the_rest(self):
        """The cache key is the date alone: a date stays cached once uploaded."""
        scraper = _make_scraper()
        scraper._total_dates = 50
        scraper.cached_count = 48
        scraper.item_count = 2

        with (
            patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()),
            patch("bawue.notifications.load_toml_section", return_value={"mattermost-hook": "https://hook.example"}),
            patch("bawue.notifications.requests.post") as post,
        ):
            await scraper.run()

        post.assert_not_called()
        title, lines = scraper.summary
        assert title == "Sitzungen"
        assert "Dates found:      50  (new or retried 2, cached 48)" in lines


class TestRunSummary:
    @pytest.mark.asyncio
    async def test_summary_printed_to_stdout(self, capsys, caplog):
        scraper = _make_scraper()
        scraper._total_dates = 5
        scraper._published_dates = 3
        scraper._failed_dates = 2
        scraper._published_sitzungen = 7

        with (
            patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()),
            caplog.at_level(logging.INFO, logger="bawue.bawue_sitzungen_scraper"),
        ):
            await scraper.run()

        out = capsys.readouterr().out
        assert "=== BaWue Sitzungen Run Summary ===" in out

        def line(label):
            return next(row for row in out.splitlines() if label in row)

        assert "3" in line("Dates published:")
        assert "2" in line("Dates failed:")
        assert "7" in line("Total sitzungen:")
        assert "Duration: 0m 00s" in line("Duration")  # human readable, no raw seconds
        assert any("Completed in" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_summary_still_printed_on_run_failure(self, capsys, caplog):
        scraper = _make_scraper()

        mock_run = AsyncMock(side_effect=RuntimeError("boom"))
        with (
            patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=mock_run),
            caplog.at_level(logging.INFO, logger="bawue.bawue_sitzungen_scraper"),
            pytest.raises(RuntimeError),
        ):
            await scraper.run()

        captured = capsys.readouterr()
        assert "=== BaWue Sitzungen Run Summary ===" in captured.out
        assert scraper.summary[0] == "Sitzungen"  # issue #52: the cycle report still gets it
        assert any("Completed in" in msg for msg in caplog.messages)

    @pytest.mark.asyncio
    async def test_summary_lists_failed_dates_with_reason(self, capsys):
        from bawue.run_report import FailedItem

        scraper = _make_scraper()
        scraper._failed_dates = 1
        scraper._failed_items = [
            FailedItem(item_id="2026-03-12", titel=None, reason="HTTP 500 Internal Server Error"),
        ]

        with patch("bawue.bawue_sitzungen_scraper.SitzungsScraper.run", new=AsyncMock()):
            await scraper.run()

        captured = capsys.readouterr()
        assert "Failed Sitzung dates" in captured.out
        failed_block = captured.out.split("Failed Sitzung dates", 1)[1]
        assert "2026-03-12" in failed_block
        assert "500" in failed_block


_AGENDA_PDF = (
    Path(__file__).parent.parent / "fixtures" / "tagesordnung" / "2026-10-21_14_Plenarsitzung.pdf"
).read_bytes()
# The real agenda of 21.10.2026, linked for the sample feed's bare Plenarsitzung on 27.02.2026.
_AGENDA_HTML = b'<a href="/resource/blob/656162/f6ca/2026-02-27_14_Plenarsitzung.pdf">Tagesordnung</a>'
_DRUCKSACHEN = "https://www.landtag-bw.de/files/live/sites/LTBW/files/dokumente/WP18/Drucksachen/0000/"


def _route(pages: dict[str, bytes]) -> MagicMock:
    """session.get stub: the first URL prefix in *pages* answers, anything else is HTTP 404."""

    def get(url, **_kwargs):
        body = next((b for prefix, b in pages.items() if url.startswith(prefix)), None)
        response = AsyncMock(status=200 if body is not None else 404, read=AsyncMock(return_value=body))
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        return response

    return MagicMock(side_effect=get)


def _site(agenda_html: bytes = _AGENDA_HTML, **overrides: bytes) -> dict[str, bytes]:
    pages = {
        ICS_URL: ICS_BYTES,
        "https://www.landtag-bw.de/de/aktuelles/tagesordnungen": agenda_html,
        "https://www.landtag-bw.de/resource/blob/656162/f6ca/2026-02-27_14_Plenarsitzung.pdf": _AGENDA_PDF,
    }
    pages.update(overrides)
    return {url: body for url, body in pages.items() if body is not None}


def _hash(drucksnr: str) -> str:
    return hashlib.sha256(drucksnr.encode()).hexdigest()


def _uploaded(cache, drucksachen=("18/700", "18/369", "18/659", "18/692")):
    """What the Vorgänge scraper registers after uploading these Drucksachen (DD-070)."""
    for nr in drucksachen:
        cache.store_raw(f"drucks-hash:{nr}", _hash(nr))
    return cache


class TestIssue66Tops:
    """Issue #66 / DD-070: the Plenarsitzung carries the TOPs of its published agenda.

    A Gesetzentwurf TOP references its Drucksachen by the PDF hash the Vorgänge scraper
    uploaded, so the backend links the TOP to the Vorgang.
    """

    @staticmethod
    async def _extract(pages: dict[str, bytes], date_key: str = "2026-02-27", uploaded=None):
        from tests.unit.test_bawue_scraper import _InMemoryCache

        scraper = _make_scraper()
        scraper.config = MagicMock(cache=_uploaded(_InMemoryCache(), *([uploaded] if uploaded is not None else [])))
        scraper.session.get = _route(pages)
        await scraper.listing_page_extractor(ICS_URL)
        _termin, sitzungen = await scraper.item_extractor(date_key)
        return scraper, sitzungen

    @pytest.mark.asyncio
    async def test_plenarsitzung_carries_the_agenda(self, caplog):
        with caplog.at_level(logging.WARNING, logger="bawue.bawue_sitzungen_scraper"):
            _scraper, sitzungen = await self._extract(_site())

        tops = sitzungen[0].tops
        assert [t.nummer for t in tops] == [1, 2, 3, 4, 5, 6, 7, 8]
        # The real extraction runs: macOS keeps a space after the slash, Linux does not.
        assert tops[1].titel.replace("/ ", "/") == "Aktuelle Debatte/vorgezogene Initiative"
        # The feed's bare "Plenarsitzung:" has no number; the agenda's file name has (DD-064).
        assert sitzungen[0].nummer == 14
        assert "No session number" not in caplog.text

    @pytest.mark.asyncio
    async def test_gesetzentwurf_top_references_the_uploaded_hash(self):
        _scraper, sitzungen = await self._extract(_site())
        tops = sitzungen[0].tops

        assert [d.drucksnr for d in tops[4].dokumente] == ["18/369", "18/659"]
        dok = tops[3].dokumente[0]
        assert dok.hash_ == _hash("18/700")
        assert dok.link == _DRUCKSACHEN + "18_0700.pdf"
        # Anträge have no Vorgang to link to; their Dokument would be an orphan.
        assert tops[0].dokumente == []

    @pytest.mark.asyncio
    async def test_drucksache_not_uploaded_yet_is_left_out_and_retried(self):
        """A TOP Dokument the backend cannot match exactly would become its own row; next to a
        Vorgang's placeholder row it makes the Vorgang's merge ambiguous (review of PR #124)."""
        scraper, sitzungen = await self._extract(_site(), uploaded=("18/700", "18/659", "18/692"))
        key = await scraper.make_cache_key("2026-02-27")

        assert [d.drucksnr for d in sitzungen[0].tops[4].dokumente] == ["18/659"]
        await scraper.store_extracted_result(key, None)
        assert scraper.config.cache.get_raw(key) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "pages",
        [
            _site(agenda_html=None),
            _site(**{"https://www.landtag-bw.de/resource/blob/656162/f6ca/2026-02-27_14_Plenarsitzung.pdf": None}),
        ],
        ids=["page", "pdf"],
    )
    async def test_unavailable_agenda_leaves_the_kalender_intact(self, pages, caplog):
        with caplog.at_level(logging.WARNING, logger="bawue.bawue_sitzungen_scraper"):
            _scraper, sitzungen = await self._extract(pages)

        assert sitzungen[0].tops == []
        assert "Tagesordnung" in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_date_in_a_file_name_leaves_the_kalender_intact(self, caplog):
        """The link regex accepts 2026-02-30: only that link is skipped (review of PR #124)."""
        html = b'<a href="/resource/blob/1/ab/2026-02-30_15_Plenarsitzung.pdf">x</a>' + _AGENDA_HTML
        with caplog.at_level(logging.WARNING):
            _scraper, sitzungen = await self._extract(_site(agenda_html=html))

        assert len(sitzungen[0].tops) == 8
        assert "2026-02-30" in caplog.text

    @pytest.mark.asyncio
    async def test_the_feeds_number_wins(self):
        pdf = "https://www.landtag-bw.de/resource/blob/656162/f6ca/2026-02-26_14_Plenarsitzung.pdf"
        pages = _site(agenda_html=_AGENDA_HTML.replace(b"2026-02-27", b"2026-02-26"), **{pdf: _AGENDA_PDF})
        _scraper, sitzungen = await self._extract(pages, date_key="2026-02-26")

        assert sitzungen[0].nummer == 143
        assert sitzungen[0].tops

    @pytest.mark.asyncio
    async def test_agenda_for_a_date_without_plenum_is_ignored(self):
        html = b'<a href="/resource/blob/656162/f6ca/2026-02-24_14_Plenarsitzung.pdf">x</a>'
        _scraper, sitzungen = await self._extract(_site(agenda_html=html), date_key="2026-02-24")

        assert sitzungen[0].tops == []

    @pytest.mark.asyncio
    async def test_new_agenda_reuploads_only_its_date(self):
        """DD-066: the TOPs are part of the fingerprint; dates without an agenda keep theirs."""
        scraper = TestSitzungenRefreshIssue85._scraper()
        _uploaded(scraper.config.cache)

        async def cycle(pages):
            scraper.session.get = _route(pages)
            scraper.uploaded = []
            await scraper.process_items(await scraper.process_lpurls(scraper.listing_urls))
            return scraper.uploaded

        assert len(await cycle(_site(agenda_html=b""))) == 5
        assert await cycle(_site()) == ["2026-02-27"]
        assert await cycle(_site()) == []
        with patch("bawue.bawue_sitzungen_scraper.parse_tops", return_value=[]):
            assert await cycle(_site()) == ["2026-02-27"]

    @pytest.mark.asyncio
    async def test_agenda_taken_off_the_page_keeps_the_uploaded_tops(self):
        """The page shows the next session only: once the following one is up, a re-upload
        without the agenda would strip the TOPs from the backend (DD-070)."""
        scraper = TestSitzungenRefreshIssue85._scraper()
        _uploaded(scraper.config.cache)

        async def cycle(pages):
            scraper.session.get = _route(pages)
            scraper.uploaded = []
            await scraper.process_items(await scraper.process_lpurls(scraper.listing_urls))
            return scraper.uploaded

        assert len(await cycle(_site())) == 5
        assert await cycle(_site(agenda_html=b"")) == []
        # A change to the date's events still re-uploads it.
        moved = ICS_BYTES.replace(b"DTSTART:20260227T090000", b"DTSTART:20260227T100000")
        assert await cycle(_site(agenda_html=b"", **{ICS_URL: moved})) == ["2026-02-27"]
