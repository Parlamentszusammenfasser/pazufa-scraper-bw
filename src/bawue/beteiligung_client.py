"""HTTP client for the Beteiligungsportal Baden-Württemberg."""

import logging
import time

import requests

from bawue.beteiligung_parser import RawBeteiligungProcess, parse_process_list

logger = logging.getLogger(__name__)

BASE_URL = "https://beteiligungsportal.baden-wuerttemberg.de"


class BeteiligungClient:
    """Handles HTTP communication with the Beteiligungsportal."""

    def __init__(self, wahlperiode: int = 17, request_delay_s: float = 1.0) -> None:
        self._wahlperiode = wahlperiode
        self._request_delay_s = request_delay_s
        self._last_request_time: float = 0.0
        self._session = requests.Session()
        self._session.hooks["response"].append(lambda r, *a, **kw: setattr(r, "encoding", "utf-8"))
        self._session.headers.update(
            {
                "User-Agent": "PaZuFa-BaWue-Scraper/0.1",
                "Accept-Language": "de-DE,de;q=0.9",
            }
        )

    def _wait_for_delay(self) -> None:
        """Enforce minimum delay between requests."""
        if self._last_request_time > 0:
            elapsed = time.monotonic() - self._last_request_time
            remaining = self._request_delay_s - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_time = time.monotonic()

    def fetch_process_list(self) -> list[RawBeteiligungProcess]:
        """Fetch the LP index page and return parsed process entries."""
        url = f"{BASE_URL}/de/mitmachen/lp-{self._wahlperiode}"
        logger.info("Fetching Beteiligungsportal index: %s", url)
        self._wait_for_delay()
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return parse_process_list(resp.text)

    def fetch_process_detail(self, process_path: str) -> str:
        """Fetch a single process detail page, return HTML."""
        url = f"{BASE_URL}{process_path}"
        logger.info("Fetching Beteiligungsportal detail: %s", url)
        self._wait_for_delay()
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text
