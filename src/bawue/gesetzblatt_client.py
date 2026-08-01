"""HTTP client for the Gesetzblatt Baden-Württemberg.

Fetches individual detail pages at detail/YYYY-N. The site has no API and no RSS,
so this is plain HTTP scraping with rate limiting. Pages are requested on demand
for a citation found in PARLIS (DD-047); the client no longer enumerates a year.
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

    @property
    def base_url(self) -> str:
        """Site root, used to absolutise links found on a detail page."""
        return BASE_URL

    def fetch_detail(self, detail_url: str) -> str:
        """Fetch a single Gesetzblatt detail page, return HTML."""
        logger.info("Fetching Gesetzblatt detail: %s", detail_url)
        resp = self._get(detail_url, timeout=30)
        return resp.text

    def fetch_detail_for(self, year: int, num: int) -> str:
        """Fetch the detail page for entry ``YYYY-N``. Raises on any error status."""
        return self.fetch_detail(f"{_DETAIL_BASE}/{year}-{num}")
