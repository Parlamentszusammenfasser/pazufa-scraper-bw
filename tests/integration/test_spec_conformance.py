from datetime import date
from unittest.mock import patch

import pytest
import responses
from pazufa_corelib.api_model import Vorgang as SpecVorgang

from tests.integration.test_scraper_pipeline import _mock_parlis_for_types

# The attrs-based api_client serialises whatever it is handed; only the backend
# (and corelib's hand-hardened Pydantic mirror of the same spec) actually
# validates it. This pins the wire payload against that stricter model so a
# corelib/spec bump surfaces here rather than as an HTTP 422 in production.


@pytest.mark.integration
@responses.activate
@pytest.mark.asyncio
async def test_payload_validates_against_spec_model(scraper, mock_backend, parlis_fixtures):
    """The Vorgang the scraper PUTs must satisfy the spec's Pydantic model (DD-051)."""
    fx = parlis_fixtures("gesetzgebung")
    _mock_parlis_for_types({"Gesetzgebung": (fx["search_json"], fx["results_html"])})
    s = await scraper(["Gesetzgebung"])
    try:
        with patch("bawue.bawue_vorgaenge_scraper.date") as mock_date:
            mock_date.today.return_value = date(2026, 1, 31)
            mock_date.side_effect = lambda *a, **k: date(*a, **k)
            await s.run()
    finally:
        await s.session.close()

    assert mock_backend.call_count == 1
    SpecVorgang.model_validate(mock_backend.vorgaenge[0])
