"""Shared test fixtures for the BaWue scraper test suite."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def mock_parlis_client():
    """A mock ParlisClient."""
    return MagicMock()
