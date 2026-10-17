"""Tests for the Gesetzblatt (Jahr, Nr.) → Publikationsdatum lookup (DD-047)."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from bawue.gesetzblatt_lookup import GesetzblattDateLookup, GesetzblattUnavailable, parse_german_date

DETAIL_HTML = """<html><body>
<div class="tx-rsmbwlawsheet"><h1>Gesetz für Teilhabe- und Pflegequalität</h1></div>
<p class="page-title__category">Gesetzblatt-Nr. 11</p>
<time class="article-title__date" datetime="2026-02-27">27.02.2026</time>
<div class="rsmbwlawsheet_show_text">Ausfertigung: 10.02.2026 Gesetzblatt-Typ: Gesetz
Federführung: Sozialministerium</div>
<a href="/index.php?eID=dumpFile&amp;f=1&amp;fn=GBl2026011.pdf">PDF</a>
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


_LNRSCHG = (Path(__file__).parent.parent / "fixtures" / "gesetzblatt" / "inkrafttreten" / "GBl-2026-14.txt").read_text()


def _pdf_client(**kwargs) -> MagicMock:
    client = _client(**kwargs)
    client.fetch_pdf.return_value = b"%PDF"
    return client


def _extracted(text: str):
    return patch("bawue.gesetzblatt_lookup.extract_pdf_text", return_value=(text, "hash"))


class TestInkrafttretenIssue98:
    """GitHub issue #98 / DD-071: the latest date the law's Inkrafttreten clause names."""

    @pytest.mark.asyncio
    async def test_reads_the_cited_pdf(self):
        client = _pdf_client()
        with _extracted(_LNRSCHG):
            assert await GesetzblattDateLookup(client).inkrafttreten(2026, 14) == datetime(2026, 6, 1, tzinfo=UTC)
        client.fetch_pdf.assert_called_once_with(
            "https://www.baden-wuerttemberg.de/index.php?eID=dumpFile&f=1&fn=GBl2026011.pdf"
        )

    @pytest.mark.asyncio
    async def test_staggered_clause_yields_the_latest_date(self):
        """The date the whole law is in force, as the BU scraper does it (DD-071)."""
        text = "Artikel 3\nDieses Gesetz tritt am 1. März 2026 in Kraft. Artikel 2 tritt am 1. Januar 2028 in Kraft.\n"
        with _extracted(text):
            assert await GesetzblattDateLookup(_pdf_client()).inkrafttreten(2026, 11) == datetime(
                2028, 1, 1, tzinfo=UTC
            )

    @pytest.mark.asyncio
    async def test_caches_the_result(self):
        client = _pdf_client()
        lookup = GesetzblattDateLookup(client)
        with _extracted(_LNRSCHG):
            await lookup.inkrafttreten(2026, 14)
            await lookup.inkrafttreten(2026, 14)
            await lookup.publikationsdatum(2026, 14)
        assert client.fetch_detail_for.call_count == 1
        assert client.fetch_pdf.call_count == 1

    @pytest.mark.asyncio
    async def test_unparseable_clause_is_logged_not_guessed(self, caplog):
        with _extracted("Artikel 2\nDieses Gesetz tritt in Kraft, sobald alle Länder zugestimmt haben.\n"):
            assert await GesetzblattDateLookup(_pdf_client()).inkrafttreten(2026, 11) is None
        assert "Inkrafttreten" in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["fetch_pdf", "fetch_detail_for"])
    async def test_fetch_failure_is_reported_for_a_retry(self, failing):
        """Review of PR #125: a 503 must not leave the law without its station for good."""
        client = _pdf_client()
        getattr(client, failing).side_effect = requests.HTTPError("503")
        with pytest.raises(GesetzblattUnavailable):
            await GesetzblattDateLookup(client).inkrafttreten(2026, 11)

    @pytest.mark.asyncio
    async def test_unresolvable_entry_returns_none(self):
        """Pre-2024: no electronic Gesetzblatt, no K station (like DD-047)."""
        client = _pdf_client(exc=requests.HTTPError("404", response=MagicMock(status_code=404)))
        assert await GesetzblattDateLookup(client).inkrafttreten(2022, 41) is None
        client.fetch_pdf.assert_not_called()


class TestPdfUrl:
    """The Landtag's copy of the Gesetzblatt is incomplete (2026 Nr. 81: 404); the official PDF is the same file."""

    @pytest.mark.asyncio
    async def test_official_pdf_of_the_entry(self):
        url = await GesetzblattDateLookup(_client()).pdf_url(2026, 11)
        assert url == "https://www.baden-wuerttemberg.de/index.php?eID=dumpFile&f=1&fn=GBl2026011.pdf"

    @pytest.mark.asyncio
    async def test_none_without_an_entry(self):
        err = requests.HTTPError("404", response=MagicMock(status_code=404))
        assert await GesetzblattDateLookup(_client(exc=err)).pdf_url(2023, 21) is None
