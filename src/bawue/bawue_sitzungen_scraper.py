"""BaWue Sitzungen scraper: SitzungsScraper subclass for Baden-Württemberg ICS calendar."""

import datetime
import logging
import time
import uuid
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import aiohttp
import openapi_client
import openapi_client.api
import openapi_client.api.collector_schnittstellen_api
import toml
from collector.config import CollectorConfiguration
from collector.interface import SitzungsScraper
from openapi_client.models import Gremium, Parlament, Sitzung

from bawue.ics_parser import group_events_by_date, parse_ics_feed

logger = logging.getLogger(__name__)

DEFAULT_WAHLPERIODE = 17
DEFAULT_ICS_URL = "https://www.landtag-bw.de/resource/calendar/501552/download/terminkalender.ics"
BERLIN_TZ = ZoneInfo("Europe/Berlin")


class BawueSitzungenScraper(SitzungsScraper):
    """Scrapes session data from the Baden-Württemberg ICS calendar feed.

    Auto-discovered by the framework when placed in the scrapers directory.
    """

    def __init__(self, config: CollectorConfiguration, session: aiohttp.ClientSession) -> None:
        bawue_config = self._load_bawue_config(config)
        self._wahlperiode = bawue_config.get("wahlperiode", DEFAULT_WAHLPERIODE)
        ics_url = bawue_config.get("ics-url", DEFAULT_ICS_URL)

        super().__init__(config, uuid.UUID(config.collector_id), [ics_url], session)

        self._events_by_date: dict[str, list] = {}

    @staticmethod
    def _load_bawue_config(config: CollectorConfiguration) -> dict:
        """Load [bawue] section from the collector config file."""
        config_file = getattr(config, "config_file", None)
        if config_file:
            try:
                loaded = toml.load(config_file)
                return loaded.get("bawue", {})
            except Exception:
                logger.warning("Could not load [bawue] section from config file: %s", config_file, exc_info=True)
        return {}

    async def run(self) -> None:
        start = time.monotonic()
        try:
            await super().run()
        finally:
            duration = time.monotonic() - start
            logger.info("Completed in %.1fs", duration)

    async def listing_page_extractor(self, url: str) -> list[str]:
        """Fetch the ICS feed and return ISO date strings as listing keys."""
        async with self.session.get(url) as response:
            ics_data = await response.read()

        events = parse_ics_feed(ics_data)
        grouped = group_events_by_date(events)

        date_keys = []
        for dt, evts in sorted(grouped.items()):
            key = dt.isoformat()
            self._events_by_date[key] = evts
            date_keys.append(key)

        logger.info("Parsed %d events across %d dates from ICS feed", len(events), len(date_keys))
        return date_keys

    async def item_extractor(self, date_key: str) -> Any:
        """Convert stored events for a date into (datetime, List[Sitzung])."""
        events = self._events_by_date.pop(date_key, [])

        sitzungen = []
        for event in events:
            # Treat naive datetimes as Europe/Berlin, convert to UTC
            dtstart = event.dtstart
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=BERLIN_TZ)
            termin_utc = dtstart.astimezone(datetime.UTC)

            api_id = uuid5(NAMESPACE_URL, event.uid)

            gremium = Gremium(
                parlament=Parlament.BW,
                wahlperiode=self._wahlperiode,
                name=event.gremium_name,
            )

            sitzung = Sitzung(
                api_id=api_id,
                titel=event.summary,
                termin=termin_utc,
                gremium=gremium,
                nummer=0,
                public=True,
                tops=[],
            )
            sitzungen.append(sitzung)

        # The framework expects (datetime, List[Sitzung]) — use the date as a datetime at midnight UTC
        date_dt = datetime.date.fromisoformat(date_key)
        result_datetime = datetime.datetime(
            date_dt.year, date_dt.month, date_dt.day, tzinfo=datetime.UTC
        )

        return (result_datetime, sitzungen)

    async def send_result(self, item: tuple[datetime.datetime, list[Sitzung]]) -> tuple | None:
        """Override to use Parlament.BW instead of the hardcoded Parlament.BY."""
        logger.info("Sending Item with Date `%s` to Database", item[0])
        logger.debug("Collector ID: %s", self.scraper_id)

        self.log_item(item)

        with openapi_client.ApiClient(self.config.oapiconfig) as api_client:
            api_instance = openapi_client.api.collector_schnittstellen_api.CollectorSchnittstellenApi(
                api_client
            )
            try:
                ret = api_instance.kal_date_put(
                    x_scraper_id=str(self.scraper_id),
                    parlament=Parlament.BW,
                    datum=item[0],
                    sitzung=item[1],
                )
                logger.info("API Response: %s", ret)
                return item
            except openapi_client.ApiException as e:
                logger.error("API Exception: %s", e)
                if e.status == 422:
                    logger.error("Unprocessable Entity for date %s", item[0])
                    self.log_item(item, True)
                elif e.status == 401:
                    logger.critical("Authentication failed. Check your API key.")
                return None
            except Exception as e:
                logger.error("Unexpected error sending item to API: %s", e)
                return None
