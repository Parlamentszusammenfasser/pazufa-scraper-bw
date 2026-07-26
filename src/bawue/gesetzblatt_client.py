"""HTTP client for the Gesetzblatt Baden-Württemberg.

Iterates individual detail pages at detail/YYYY-N via HEAD probes and GET fetches.
The site has no API and no RSS, so this is plain HTTP scraping with rate limiting.
"""

import logging

import requests

from bawue.rate_limiter import AdaptiveRateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://www.baden-wuerttemberg.de"
_DETAIL_BASE = f"{BASE_URL}/de/service/gesetze-und-verordnungen/gesetzblatt/detail"


class GesetzblattClient:
    """HTTP client for the Gesetzblatt detail pages."""

    def __init__(self, request_delay_s: float = 1.0) -> None:
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

    def _get(self, url: str, **kw) -> requests.Response:
        """Rate-limited GET with automatic 429 retry."""
        while True:
            self._rate_limiter.wait()
            resp = self._session.get(url, **kw)
            if resp.status_code == 429:
                self._rate_limiter.on_rate_limit(logger)
                continue
            resp.raise_for_status()
            self._rate_limiter.on_success()
            return resp

    def _head(self, url: str, **kw) -> requests.Response:
        """Rate-limited HEAD with automatic 429 retry. Does not raise for 404."""
        while True:
            self._rate_limiter.wait()
            resp = self._session.head(url, **kw)
            if resp.status_code == 429:
                self._rate_limiter.on_rate_limit(logger)
                continue
            self._rate_limiter.on_success()
            return resp

    def fetch_detail(self, detail_url: str) -> str:
        """Fetch a single Gesetzblatt detail page, return HTML."""
        logger.info("Fetching Gesetzblatt detail: %s", detail_url)
        resp = self._get(detail_url, timeout=30)
        return resp.text

    def entry_exists(self, year: int, num: int) -> bool:
        """Return True when the detail page for YYYY-N responds with HTTP 200."""
        url = f"{_DETAIL_BASE}/{year}-{num}"
        resp = self._head(url, timeout=15)
        return resp.status_code == 200

    def find_max_number(self, year: int) -> int:
        """Binary-search for the highest entry number published in a given year.

        Uses HEAD-only requests. Returns 0 if the year has no entries at all.
        """
        if not self.entry_exists(year, 1):
            return 0
        # Exponential probe to find an upper bound
        hi = 1
        while self.entry_exists(year, hi * 2):
            hi *= 2
        # Binary search in [hi, hi*2 - 1]
        lo = hi
        hi = hi * 2 - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.entry_exists(year, mid):
                lo = mid
            else:
                hi = mid - 1
        return lo
