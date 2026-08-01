"""Tests for the Gesetzblatt (Jahr, Nr.) → Publikationsdatum lookup (DD-047)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import requests

from bawue.gesetzblatt_lookup import GesetzblattDateLookup, parse_german_date

DETAIL_HTML = """<html><body>
<div class="tx-rsmbwlawsheet"><h1>Gesetz für Teilhabe- und Pflegequalität</h1></div>
<p class="page-title__category">Gesetzblatt-Nr. 11</p>
<time class="article-title__date" datetime="2026-02-27">27.02.2026</time>
<div class="rsmbwlawsheet_show_text">Ausfertigung: 10.02.2026 Gesetzblatt-Typ: Gesetz
Federführung: Sozialministerium</div>
</body></html>"""


def _client(html=DETAIL_HTML, exc: Exception | None = None) -> MagicMock:
    client = MagicMock()
    client.base_url = "https://www.baden-wuerttemberg.de"
    if exc is not None:
        client.fetch_detail_for.side_effect = exc
    else:
        client.fetch_detail_for.return_value = html
    return client


class TestParseGermanDate:
    def test_parses_dd_mm_yyyy_as_utc(self):
        assert parse_german_date("27.02.2026") == datetime(2026, 2, 27, tzinfo=UTC)

    @pytest.mark.parametrize("value", [None, "", "27/02/2026", "nonsense"])
    def test_returns_none_for_unparseable(self, value):
        assert parse_german_date(value) is None


class TestPublikationsdatum:
    @pytest.mark.asyncio
    async def test_returns_publikationsdatum_not_ausfertigung(self):
        """Issue #9: the Ausgabedatum (27.02.) is wanted, not the Ausfertigung (10.02.)."""
        lookup = GesetzblattDateLookup(_client())
        assert await lookup.publikationsdatum(2026, 11) == datetime(2026, 2, 27, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_requests_the_cited_entry(self):
        client = _client()
        await GesetzblattDateLookup(client).publikationsdatum(2026, 11)
        client.fetch_detail_for.assert_called_once_with(2026, 11)

    @pytest.mark.asyncio
    async def test_caches_repeated_lookups(self):
        """GBl 2023 Nr. 21 is cited by five WP17 Vorgänge — fetch it once."""
        client = _client()
        lookup = GesetzblattDateLookup(client)
        for _ in range(5):
            await lookup.publikationsdatum(2023, 21)
        assert client.fetch_detail_for.call_count == 1

    @pytest.mark.asyncio
    async def test_caches_misses_too(self):
        client = _client(exc=requests.HTTPError("404"))
        lookup = GesetzblattDateLookup(client)
        assert await lookup.publikationsdatum(2022, 41) is None
        assert await lookup.publikationsdatum(2022, 41) is None
        assert client.fetch_detail_for.call_count == 1

    @pytest.mark.asyncio
    async def test_unresolvable_entry_returns_none(self):
        """Pre-2024 citations are not digitally available — the caller keeps its PARLIS date."""
        lookup = GesetzblattDateLookup(_client(exc=requests.HTTPError("404")))
        assert await lookup.publikationsdatum(2022, 41) is None

    @pytest.mark.asyncio
    async def test_page_without_date_returns_none(self):
        lookup = GesetzblattDateLookup(_client(html="<html><body>nothing</body></html>"))
        assert await lookup.publikationsdatum(2026, 11) is None

    @pytest.mark.asyncio
    async def test_distinct_entries_are_cached_separately(self):
        client = _client()
        lookup = GesetzblattDateLookup(client)
        await lookup.publikationsdatum(2026, 11)
        await lookup.publikationsdatum(2026, 12)
        assert client.fetch_detail_for.call_count == 2
