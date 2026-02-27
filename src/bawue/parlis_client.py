"""PARLIS HTTP client: session management, search, pagination, and date subdivision."""

import calendar
import logging
import time
from datetime import date

import requests

from bawue.parlis_parser import parse_results
from bawue.types import RawVorgang

logger = logging.getLogger(__name__)

BASE_URL = "https://parlis.landtag-bw.de/parlis/"
BROWSE_URL = BASE_URL + "browse.tt.json"
REPORT_URL = BASE_URL + "report.tt.html"
CHUNKSIZE = 50


class ParlisClient:
    """Handles HTTP communication with the PARLIS system."""

    def __init__(self, wahlperiode: int = 17, request_delay_s: float = 1.0) -> None:
        self._wahlperiode = wahlperiode
        self._request_delay_s = request_delay_s
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": "PaZuFa-BaWue-Scraper/0.1",
                "Accept-Language": "de-DE,de;q=0.9",
            }
        )

    def _establish_session(self) -> None:
        """Load the PARLIS main page to establish session cookies."""
        logger.info("Establishing PARLIS session...")
        resp = self._session.get(BASE_URL, timeout=30)
        resp.raise_for_status()
        logger.info("Session established.")

    def _build_query(self, vorgangstyp: str, date_from: date | None, date_to: date | None) -> dict:
        return {
            "action": "SearchAndDisplay",
            "report": {
                "rhl": "main",
                "rhlmode": "add",
                "format": "suchergebnis-vorgang-full",
                "mime": "html",
                "sort": "SORT01/D SORT02/D SORT03",
            },
            "search": {
                "lines": {
                    "l1": str(self._wahlperiode),
                    "l2": date_from.strftime("%d.%m.%Y") if date_from else "",
                    "l3": date_to.strftime("%d.%m.%Y") if date_to else "",
                    "l4": vorgangstyp,
                },
                "serverrecordname": "vorgang",
            },
            "sources": ["Star"],
        }

    def _fetch_page(self, report_id: str, start: int) -> str:
        params = {
            "report_id": report_id,
            "start": start,
            "chunksize": CHUNKSIZE,
        }
        resp = self._session.get(REPORT_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _monthly_windows(date_from: date, date_to: date) -> list[tuple[date, date]]:
        """Split a date range into monthly windows."""
        windows = []
        current = date_from
        while current <= date_to:
            last_day = calendar.monthrange(current.year, current.month)[1]
            window_end = date(current.year, current.month, last_day)
            if window_end > date_to:
                window_end = date_to
            windows.append((current, window_end))
            current = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
        return windows

    def _search_single(self, vorgangstyp: str, date_from: date | None, date_to: date | None) -> list[RawVorgang] | None:
        """Execute a single search against PARLIS.

        Returns:
            A list of results, or None if the search was too large (status=running).
        """
        query = self._build_query(vorgangstyp, date_from, date_to)
        logger.info(
            "Searching PARLIS: WP=%s, type=%s, dates=%s-%s",
            self._wahlperiode,
            vorgangstyp,
            date_from or "any",
            date_to or "any",
        )

        resp = self._session.post(
            BROWSE_URL,
            json=query,
            headers={"Content-Type": "application/json", "Referer": BASE_URL},
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        report_id = data.get("report_id", "")
        item_count = int(data.get("item_count", 0) or 0)

        if not report_id:
            sources = data.get("sources", {})
            star = sources.get("Star", {})
            if star.get("status") == "running" and int(star.get("hits", 0)) > 0:
                logger.warning(
                    "Search too large (%d hits, still running). Subdividing into monthly windows.",
                    int(star["hits"]),
                )
                return None
            return []

        if item_count == 0:
            return []

        all_results: list[RawVorgang] = []
        for start in range(0, item_count, CHUNKSIZE):
            if start > 0:
                time.sleep(self._request_delay_s)
            html_content = self._fetch_page(report_id, start)
            page_results = parse_results(html_content)
            all_results.extend(page_results)
            logger.info("Fetched page start=%d, got %d records", start, len(page_results))

        return all_results

    def search(
        self, vorgangstyp: str, date_from: date | None = None, date_to: date | None = None
    ) -> list[RawVorgang]:
        """Search PARLIS for Vorgänge matching the given criteria.

        If PARLIS indicates the result set is too large (status=running), automatically
        subdivides the date range into monthly windows and retries. Subdivision requires
        concrete dates — if both are None, returns empty list on overflow.
        """
        self._establish_session()
        results = self._search_single(vorgangstyp, date_from, date_to)
        if results is not None:
            return results

        # Subdivision requires concrete dates — skip if None
        if date_from is None or date_to is None:
            logger.warning("Search too large but no date range to subdivide. Returning empty.")
            return []

        # Subdivide into monthly windows
        all_results: list[RawVorgang] = []
        for window_from, window_to in self._monthly_windows(date_from, date_to):
            time.sleep(self._request_delay_s)
            window_results = self._search_single(vorgangstyp, window_from, window_to)
            if window_results is None:
                logger.warning(
                    "Monthly window %s-%s still too large, skipping.",
                    window_from,
                    window_to,
                )
                continue
            all_results.extend(window_results)

        return all_results
