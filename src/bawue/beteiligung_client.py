"""HTTP client for the Beteiligungsportal Baden-Württemberg."""

import logging
from datetime import date
from email.utils import parsedate_to_datetime

import requests

from bawue.beteiligung_parser import BERLIN_TZ, RawBeteiligungProcess, parse_process_list
from bawue.rate_limiter import AdaptiveRateLimiter
from bawue.wahlperiode import CURRENT_WAHLPERIODE

logger = logging.getLogger(__name__)

BASE_URL = "https://beteiligungsportal.baden-wuerttemberg.de"


class BeteiligungClient:
    """Handles HTTP communication with the Beteiligungsportal."""

    def __init__(self, wahlperiode: int = CURRENT_WAHLPERIODE, request_delay_s: float = 1.0) -> None:
        self._wahlperiode = wahlperiode
        self._rate_limiter = AdaptiveRateLimiter(
            initial_delay=request_delay_s,
            min_delay=min(0.1, request_delay_s),
        )
        self._session = requests.Session()
        self._session.hooks["response"].append(lambda r, *a, **kw: setattr(r, "encoding", "utf-8"))
        self._session.headers.update(
            {
                "User-Agent": "PaZuFa-BaWue-Scraper/0.1",
                "Accept-Language": "de-DE,de;q=0.9",
            }
        )

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        """Rate-limited request with automatic 429 retry."""
        while True:
            self._rate_limiter.wait()
            resp = self._session.request(method, url, **kw)
            if resp.status_code == 429:
                self._rate_limiter.on_rate_limit(logger)
                continue
            resp.raise_for_status()
            self._rate_limiter.on_success()
            return resp

    def fetch_process_list(self) -> list[RawBeteiligungProcess]:
        """Fetch the LP index page and return parsed process entries.

        A 403/404 means the index page for this Wahlperiode does not exist
        (yet): early in WP18 the portal serves lp-18 process pages while the
        lp-18 index itself returns 403. Fall back to the parent /de/mitmachen
        listing, which links current processes across Wahlperioden — the
        lp-prefix filter below keeps only this Wahlperiode's entries.
        """
        url = f"{BASE_URL}/de/mitmachen/lp-{self._wahlperiode}"
        logger.info("Fetching Beteiligungsportal index: %s", url)
        try:
            resp = self._request("GET", url, timeout=30)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in (403, 404):
                raise
            fallback_url = f"{BASE_URL}/de/mitmachen"
            logger.warning(
                "Beteiligungsportal index for lp-%d unavailable (HTTP %s); falling back to %s",
                self._wahlperiode,
                status,
                fallback_url,
            )
            resp = self._request("GET", fallback_url, timeout=30)
        all_processes = parse_process_list(resp.text)

        lp_prefix = f"/de/mitmachen/lp-{self._wahlperiode}/"
        processes = [p for p in all_processes if p.url.startswith(lp_prefix)]

        if len(processes) < len(all_processes):
            logger.warning(
                "Filtered %d processes not matching lp-%d (index page may have redirected to broader listing)",
                len(all_processes) - len(processes),
                self._wahlperiode,
            )
        return processes

    def fetch_process_detail(self, process_path: str) -> str:
        """Fetch a single process detail page, return HTML."""
        url = f"{BASE_URL}{process_path}"
        logger.info("Fetching Beteiligungsportal detail: %s", url)
        resp = self._request("GET", url, timeout=30)
        return resp.text

    def fetch_last_modified(self, pdf_url: str) -> date | None:
        """Upload day of a portal file, from its ``Last-Modified`` header (issue #123)."""
        try:
            resp = self._request("HEAD", pdf_url, timeout=30, allow_redirects=True)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (404, 410):
                return None  # dead link: the file name may still date it
            raise
        try:
            return parsedate_to_datetime(resp.headers["Last-Modified"]).astimezone(BERLIN_TZ).date()
        except (KeyError, ValueError):  # no or malformed header: the file name may still date it
            return None
