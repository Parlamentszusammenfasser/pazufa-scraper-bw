"""Issue #180 (DD-079) end to end: the Beteiligung scraper uploads and registers a draft,
the PARLIS scraper's next run joins the Landesregierung bill to it.

Portal client and PDF texts are mocked, PARLIS answers from its fixtures, the backend is
pytest-httpserver. One in-memory cache stands in for Redis and is shared by both scrapers.
"""

import gzip
import json
import re
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest
import responses
from werkzeug.wrappers import Response as WerkzeugResponse

from bawue.api import build_client
from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_dok import LLMMetrics
from bawue.beteiligung_parser import RawBeteiligungProcess
from bawue.rate_limiter import create_upload_limiter
from bawue.types import Stationstyp
from tests.factories import make_scraper
from tests.integration.conftest import FIXED_COLLECTOR_ID
from tests.integration.test_scraper_pipeline import _mock_parlis_for_types
from tests.unit.test_bawue_scraper import _InMemoryCache

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SLUG = "entbuerokratisierung"
ENTWURF_PDF = (
    "https://beteiligungsportal.baden-wuerttemberg.de/fileadmin/redaktion/beteiligungsportal/IM/"
    "251015_Entwurf_Zweites_Gesetz_Abbau_verzichtbare_Formerfordernisse_weitere_Gesetze.pdf"
)
# V-98010 in gesetzgebung_landesregierung: the Landesregierung's Gesetzentwurf of 05.01.2026.
GESETZENTWURF_PDF = "https://www.landtag-bw.de/files/gg-lr-entwurf.pdf"
BILL_API_ID = str(uuid5(NAMESPACE_URL, "V-98010"))
DRAFT_API_ID = str(uuid5(NAMESPACE_URL, f"beteiligung-{SLUG}"))


def _text(name: str) -> str:
    return gzip.decompress((FIXTURES / "entwuerfe" / f"{name}.txt.gz").read_bytes()).decode()


# Real texts of one law (Gewalthilfeausführungsgesetz): the pipeline is under test here, not the match.
PDF_TEXTS = {ENTWURF_PDF: _text("entwurf_gewhag"), GESETZENTWURF_PDF: _text("drucksache_18_692")}


@pytest.fixture()
def backend(httpserver, mock_backend, bawue_config):
    """The PUT capture plus `GET /vorgang/{id}`: the uploaded Vorgänge, and the bill once `has_bill` is set."""
    bawue_config.cache = _InMemoryCache()
    state = {"has_bill": False, "asked": []}

    def _get(request):
        api_id = request.path.rsplit("/", 1)[1]
        state["asked"].append(api_id)
        known = {v["api_id"] for v in mock_backend.vorgaenge} | ({BILL_API_ID} if state["has_bill"] else set())
        if api_id not in known:
            return WerkzeugResponse(status=404)
        # Any valid Vorgang will do: the scraper only asks whether there is one.
        bill = {**mock_backend.vorgaenge[0], "api_id": BILL_API_ID}
        return WerkzeugResponse(status=200, content_type="application/json", response=json.dumps(bill))

    httpserver.expect_request(re.compile(r"^/api/v2/vorgang/[0-9a-f-]+$"), method="GET").respond_with_handler(_get)
    return state


def _beteiligung(config, status: str = "closed") -> BawueBeteiligungScraper:
    s = make_scraper(BawueBeteiligungScraper)
    s.config = config
    s.scraper_id = uuid.UUID(FIXED_COLLECTOR_ID)
    s.listing_urls = ["lp-17"]
    s.items_done = 0
    s._wahlperiode = 17
    s._client = MagicMock()
    s._client.fetch_process_list.return_value = [
        RawBeteiligungProcess(title="Entbürokratisierung", url=f"/de/mitmachen/lp-17/{SLUG}", slug=SLUG, status=status)
    ]
    s._client.fetch_process_detail.return_value = (FIXTURES / "beteiligung" / f"{SLUG}_detail.html").read_text()
    s._client.fetch_last_modified.return_value = date(2025, 10, 16)
    s._api_client = build_client(config.database_url, config.api_key)
    s._raw_cache, s._details, s._fingerprints, s._fristen = {}, {}, {}, {}
    s._upload_limiter = create_upload_limiter()
    s._published = s._failed = s._skipped = 0
    s._failed_items = []
    s._llm_enabled, s._llm, s._llm_metrics, s._llm_model = False, None, LLMMetrics(), "gpt-5-nano"
    return s


async def _parlis_run(scraper, parlis_fixtures):
    fx = parlis_fixtures("gesetzgebung_landesregierung")
    _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
    s = await scraper(["Gesetzgebung"])
    read = AsyncMock(side_effect=lambda _session, url: PDF_TEXTS[url])
    try:
        with (
            patch("bawue.bawue_vorgaenge_scraper.date") as mock_date,
            patch("bawue.bawue_vorgaenge_scraper.read_pdf", read),
        ):
            mock_date.today.return_value = date(2026, 1, 31)
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
            mock_date.fromisoformat = date.fromisoformat
            await s.run()
    finally:
        await s.session.close()
    return s


@responses.activate
@pytest.mark.asyncio
async def test_new_bill_is_uploaded_into_the_drafts_vorgang(
    scraper, mock_backend, backend, parlis_fixtures, bawue_config
):
    await _beteiligung(bawue_config).run()
    await _parlis_run(scraper, parlis_fixtures)

    draft, bill = mock_backend.vorgaenge
    assert draft["api_id"] == bill["api_id"] == DRAFT_API_ID
    assert bill["stationen"][0]["typ"] == Stationstyp.PREPARL_REGBSL.value
    assert draft["links"][0] in bill["links"]
    assert backend["asked"] == [BILL_API_ID]

    # From now on PARLIS writes the Vorgang: a changed portal page is not uploaded again.
    again = _beteiligung(bawue_config, status="open")
    await again.run()
    assert mock_backend.call_count == 2
    assert again.cached_count == 1


@responses.activate
@pytest.mark.asyncio
async def test_bill_the_backend_has_gains_the_draft_station(
    scraper, mock_backend, backend, parlis_fixtures, bawue_config
):
    backend["has_bill"] = True
    await _beteiligung(bawue_config).run()
    s = await _parlis_run(scraper, parlis_fixtures)

    _, bill = mock_backend.vorgaenge
    assert bill["api_id"] == BILL_API_ID
    entwurf = bill["stationen"][0]
    assert entwurf["typ"] == Stationstyp.PREPARL_REGENT.value
    assert entwurf["dokumente"][0]["link"] == ENTWURF_PDF
    assert f"V-98010 ← {SLUG} (0.51)" in s._entwurf_joins  # logged with the run summary
    assert f"  - {DRAFT_API_ID} ({SLUG}), joined to V-98010" in s.errors  # to Mattermost (DD-082)
    assert backend["asked"] == [BILL_API_ID, DRAFT_API_ID]
