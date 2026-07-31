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

# BW publishes on the order of a few hundred Gesetzblatt entries per year. This
# sanity cap is far above any real year and exists purely to bound the search:
# if the site ever soft-404s (HTTP 200 for a nonexistent detail page) instead of
# returning 404, an unbounded probe would loop forever and hang the whole run.
_MAX_ENTRIES_PER_YEAR = 2000


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
        """Rate-limited HEAD with automatic 429 retry. Does not raise for 404.

        Follows redirects (``requests`` defaults HEAD to ``allow_redirects=False``)
        so a canonicalising 3xx on a real detail page is not misread as missing.
        """
        kw.setdefault("allow_redirects", True)
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
        """Return True when the detail page for YYYY-N responds with HTTP 200.

        Raises for any error status other than 404 (e.g. a transient 5xx),
        mirroring ``fetch_detail``'s ``_get`` — otherwise a server error would
        be silently read as "not found" and bias the binary search downward.
        """
        url = f"{_DETAIL_BASE}/{year}-{num}"
        resp = self._head(url, timeout=15)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return resp.status_code == 200

    def find_max_number(self, year: int) -> int:
        """Binary-search for the highest entry number published in a given year.

        Uses HEAD-only requests. Returns 0 if the year has no entries at all.

        The search is bounded by ``_MAX_ENTRIES_PER_YEAR`` on both the probe and
        the bisection so it always terminates — a soft-404 (or a future off-by-one
        that stalls progress) can never hang the run; it caps out and warns instead.
        """
        if not self.entry_exists(year, 1):
            return 0
        # Exponential probe to find an upper bound (bounded by the sanity cap)
        hi = 1
        while hi * 2 <= _MAX_ENTRIES_PER_YEAR and self.entry_exists(year, hi * 2):
            hi *= 2
        # Binary search in [hi, min(hi*2 - 1, cap)]. The guard counter is ample for
        # a correct search (~log2(cap) steps) and only trips if progress stalls.
        lo = hi
        hi = min(hi * 2 - 1, _MAX_ENTRIES_PER_YEAR)
        guard = _MAX_ENTRIES_PER_YEAR.bit_length() + 2
        while lo < hi and guard > 0:
            guard -= 1
            mid = (lo + hi + 1) // 2
            if self.entry_exists(year, mid):
                lo = mid
            else:
                hi = mid - 1
        if lo >= _MAX_ENTRIES_PER_YEAR:
            logger.warning(
                "Gesetzblatt year %d hit the %d-entry sanity cap; the site may be "
                "soft-404ing (HTTP 200 for nonexistent entries)",
                year,
                _MAX_ENTRIES_PER_YEAR,
            )
        return lo
