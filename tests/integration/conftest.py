"""Fixtures for integration tests: mock PARLIS + mock PaZuFa backend."""

import json
import uuid
from pathlib import Path

import aiohttp
import pytest
import responses
from werkzeug.wrappers import Response as WerkzeugResponse

# NOTE: `collector` / `openapi_client` are no longer runtime deps (removed in the
# Phase 3 migration). They are imported lazily inside the fixtures that still use
# them so that unit-test *collection* (which imports every conftest) does not fail
# once those packages are gone. The full rewrite onto bawue.config / bawue.cache is
# tracked as Phase 4.2; until then these integration tests still require the old
# packages to be installed to actually run (they are deselected by default).
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.parlis_client import BASE_URL, BROWSE_URL, REPORT_URL

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "parlis"
FIXED_COLLECTOR_ID = "00000000-0000-0000-0000-000000000099"


# ---------------------------------------------------------------------------
# Fixture file loader
# ---------------------------------------------------------------------------
@pytest.fixture()
def parlis_fixtures():
    """Load HTML/JSON fixture files from tests/fixtures/parlis/."""

    def _load(slug: str) -> dict:
        search_path = FIXTURES_DIR / f"{slug}_search.json"
        results_path = FIXTURES_DIR / f"{slug}_results.html"
        data = {}
        if search_path.exists():
            data["search_json"] = json.loads(search_path.read_text())
        if results_path.exists():
            data["results_html"] = results_path.read_text(encoding="utf-8")
        return data

    return _load


# ---------------------------------------------------------------------------
# PARLIS HTTP mocks (via `responses` library)
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_parlis():
    """Register PARLIS HTTP mocks for a given Vorgangstyp.

    Usage:
        mock_parlis("gesetzgebung", search_json, results_html)
    """

    def _register(search_json: dict, results_html: str | None = None, *, session_html: str = "<html></html>"):
        # Session establishment (GET BASE_URL) — allow multiple calls
        responses.add(responses.GET, BASE_URL, body=session_html, status=200)

        # Search POST
        responses.add(responses.POST, BROWSE_URL, json=search_json, status=200)

        # Report GET (only if there are results)
        item_count = int(search_json.get("item_count", 0) or 0)
        if item_count > 0 and results_html:
            responses.add(responses.GET, REPORT_URL, body=results_html, status=200)

    return _register


# ---------------------------------------------------------------------------
# PaZuFa backend mock (via pytest-httpserver)
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_backend(httpserver):
    """Configure httpserver to accept PUT /api/v2/vorgang and capture requests."""
    received = []

    def _handler(request):
        body = json.loads(request.data)
        received.append(body)
        return WerkzeugResponse(status=200, content_type="application/json", response=json.dumps(body))

    httpserver.expect_request("/api/v2/vorgang", method="PUT").respond_with_handler(_handler)

    class BackendCapture:
        @property
        def vorgaenge(self):
            return list(received)

        @property
        def call_count(self):
            return len(received)

    return BackendCapture()


# ---------------------------------------------------------------------------
# CollectorConfiguration stub
# ---------------------------------------------------------------------------
@pytest.fixture()
def collector_config(mock_backend, httpserver):
    """Build a CollectorConfiguration that talks to the httpserver mock."""
    from collector.config import CollectorConfiguration
    from collector.scrapercache import ScraperCache
    from openapi_client import Configuration

    config = object.__new__(CollectorConfiguration)
    config.collector_id = FIXED_COLLECTOR_ID
    config.linearize = True
    config.config_file = None
    config.api_obj_log = None
    config.dry_run = False
    config.cache = ScraperCache(redis_host=None, redis_port=None, disabled=True)

    # Point the OpenAPI client at the local httpserver
    config.oapiconfig = Configuration(host=httpserver.url_for(""))
    config.oapiconfig.api_key = {"apiKey": "test-api-key"}

    return config


# ---------------------------------------------------------------------------
# Scraper instance (with reduced listing_urls)
# ---------------------------------------------------------------------------
@pytest.fixture()
def scraper(collector_config):
    """Create a BawueVorgaengeScraper wired to mock infrastructure.

    The scraper is created via __new__ to bypass the __init__ which requires
    a TOML config file and aiohttp session. We manually set all required attributes.
    """

    async def _make_scraper(listing_urls: list[str], lookback_days: int = 7) -> BawueVorgaengeScraper:
        session = aiohttp.ClientSession()
        s = object.__new__(BawueVorgaengeScraper)

        # Framework base class attributes (Scraper.__init__)
        s.config = collector_config
        s.scraper_id = uuid.UUID(FIXED_COLLECTOR_ID)
        s.listing_urls = listing_urls
        s.session = session
        s.session_headers = {}
        s.item_count = 0
        s.items_done = 0

        # BawueVorgaengeScraper-specific attributes
        from bawue.parlis_client import ParlisClient

        s._wahlperiode = 17
        s._lookback_days = lookback_days
        s._parlis = ParlisClient(wahlperiode=17, request_delay_s=0.0)
        s._raw_cache = {}
        s._llm_enabled = False
        s._llm = None

        return s

    return _make_scraper
