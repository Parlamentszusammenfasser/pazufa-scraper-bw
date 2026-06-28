"""Shared test fixtures for the BaWue scraper test suite."""

from typing import Any
from unittest.mock import MagicMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from bawue.types import Autor, Vorgang, Vorgangstyp


@pytest.fixture()
def mock_parlis_client():
    """A mock ParlisClient."""
    return MagicMock()


def make_vorgang(**overrides: Any) -> Vorgang:
    """Construct a valid ``Vorgang`` with sensible defaults, overridable per-field.

    Migration helper (Phase 0.4): hides the generated-model constructor flavour
    behind a single call site so the Phase 1 client swap
    (``openapi_client`` → ``pazufa_corelib.api_client``) is a search-and-replace
    for ``Vorgang(...)`` literals rather than a per-test rewrite. When the model
    flips, adapt the construction here once (e.g. ``UNSET`` vs ``None`` defaults).
    """
    fields: dict[str, Any] = {
        "api_id": str(uuid5(NAMESPACE_URL, "test-vorgang")),
        "titel": "Test Gesetz",
        "wahlperiode": 17,
        "verfassungsaendernd": False,
        "typ": Vorgangstyp.SONSTIG,
        "initiatoren": [Autor(organisation="Fraktion GRÜNE")],
        "stationen": [],
    }
    fields.update(overrides)
    return Vorgang(**fields)
