"""Fixtures for integration tests: mock PARLIS + mock PaZuFa backend."""

import json
import uuid
from pathlib import Path

import aiohttp
import pytest
import responses
from werkzeug.wrappers import Response as WerkzeugResponse

from bawue.api import build_client
from bawue.bawue_dok import LLMMetrics
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper
from bawue.cache import BawueCache
from bawue.config import BawueConfig
from bawue.parlis_client import BASE_URL, BROWSE_URL, REPORT_URL, ParlisClient
from bawue.rate_limiter import create_upload_limiter

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "parlis"
FIXED_COLLECTOR_ID = "00000000-0000-0000-0000-000000000099"


@pytest.fixture(autouse=True)
def _no_wahlperiode_probe(monkeypatch):
    """Neutralize the Wahlperiode update check.

    BawueVorgaengeScraper.run() probes the Beteiligungsportal for a newer
    Wahlperiode via a bare requests.get — an unrelated startup side-effect that
    would otherwise hit (and pollute) the `responses` mock registry.
    """
    monkeypatch.setattr("bawue.bawue_vorgaenge_scraper.check_for_newer_wahlperiode", lambda *a, **kw: None)


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
        # The backend returns 201 Created on a successful vorgang PUT; bawue.api.put_vorgang
        # treats anything other than 201 as a BawueApiError, so the mock must match.
        return WerkzeugResponse(status=201, content_type="application/json", response=json.dumps(body))

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
# BawueConfig stub
# ---------------------------------------------------------------------------
@pytest.fixture()
def bawue_config(mock_backend, httpserver):
    """Build a BawueConfig (via __new__) that talks to the httpserver mock.

    Only the attributes the scraper/pipeline actually read at runtime are set:
    the config loader (`BawueConfig.load`) needs a TOML file we don't have here.
    """
    config = object.__new__(BawueConfig)
    config.collector_id = FIXED_COLLECTOR_ID
    config.linearize = True
    config.max_concurrency = 3
    config.config_file = None
    config.api_obj_log = None
    config.dry_run = False
    config.cache = BawueCache(redis_host=None, redis_port=None, disabled=True)

    # Point the API client at the local httpserver mock (bawue.api.build_client).
    config.database_url = httpserver.url_for("").rstrip("/")
    config.api_key = "test-api-key"

    return config


# ---------------------------------------------------------------------------
# Scraper instance (with reduced listing_urls)
# ---------------------------------------------------------------------------
@pytest.fixture()
def scraper(bawue_config):
    """Create a BawueVorgaengeScraper wired to mock infrastructure.

    The scraper is created via __new__ to bypass __init__ (which loads a TOML
    config file and builds an aiohttp session). We set exactly the instance
    attributes BawueVorgaengeScraper.__init__ would, LLM enrichment disabled.
    """

    async def _make_scraper(listing_urls: list[str], *, filter_sonstig: bool = True) -> BawueVorgaengeScraper:
        session = aiohttp.ClientSession()
        s = object.__new__(BawueVorgaengeScraper)

        # Scraper base class attributes (Scraper.__init__)
        s.config = bawue_config
        s.scraper_id = uuid.UUID(FIXED_COLLECTOR_ID)
        s.listing_urls = listing_urls
        s.session = session
        s.item_count = 0
        s.items_done = 0

        # BawueVorgaengeScraper-specific attributes
        s._wahlperiode = 17
        s._wahlperiode_start_date = None
        s._enabled_vorgangstypen = frozenset(listing_urls)
        s._filter_sonstig = filter_sonstig
        s._parlis = ParlisClient(wahlperiode=17, request_delay_s=0.0)
        s._raw_cache = {}
        s._pending_pdf_downloads = set()
        s._upload_limiter = create_upload_limiter()
        s._client = build_client(bawue_config.database_url, bawue_config.api_key)

        # Run-report counters
        s._published = 0
        s._failed = 0
        s._skipped = 0
        s._by_type = {}
        s._failed_items = []
        s._parlis_errors = []

        # LLM enrichment (disabled in tests)
        s._llm_enabled = False
        s._llm = None
        s._llm_metrics = LLMMetrics()
        s._llm_model = "gpt-5-nano"

        return s

    return _make_scraper
