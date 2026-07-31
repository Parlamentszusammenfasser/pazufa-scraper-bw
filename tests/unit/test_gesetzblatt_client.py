"""Tests for the Gesetzblatt Baden-Württemberg HTTP client."""

import re
import time
from unittest.mock import patch

import pytest
import requests
import responses

from bawue.gesetzblatt_client import _MAX_ENTRIES_PER_YEAR, BASE_URL, GesetzblattClient

DETAIL_URL = f"{BASE_URL}/de/service/gesetze-und-verordnungen/gesetzblatt/detail/2026-19"
SAMPLE_DETAIL_HTML = "<html><body><h1>Gesetz zur Detail</h1></body></html>"

DETAIL_URL_PATTERN = re.compile(
    r"https://www\.baden-wuerttemberg\.de/de/service/gesetze-und-verordnungen/gesetzblatt/detail/\d+-\d+"
)


def _detail_url(year: int, num: int) -> str:
    return f"{BASE_URL}/de/service/gesetze-und-verordnungen/gesetzblatt/detail/{year}-{num}"


@pytest.fixture()
def client() -> GesetzblattClient:
    return GesetzblattClient(request_delay_s=0.0)


class TestFetchDetail:
    @responses.activate
    def test_returns_html(self, client):
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)

        html = client.fetch_detail(DETAIL_URL)

        assert "<h1>Gesetz zur Detail</h1>" in html

    @responses.activate
    def test_retries_on_429(self, client):
        responses.add(responses.GET, DETAIL_URL, status=429)
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)

        with patch("bawue.rate_limiter.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            html = client.fetch_detail(DETAIL_URL)

        assert "<h1>Gesetz zur Detail</h1>" in html
        assert len(responses.calls) == 2

    @responses.activate
    def test_raises_on_server_error(self, client):
        responses.add(responses.GET, DETAIL_URL, status=500)

        with pytest.raises(requests.HTTPError):
            client.fetch_detail(DETAIL_URL)

    @responses.activate
    def test_utf8_response_decoded_correctly(self, client):
        html_with_umlauts = "<html><body><h1>Bürgerschaft</h1><p>März 2026</p></body></html>"
        responses.add(
            responses.GET,
            DETAIL_URL,
            body=html_with_umlauts.encode("utf-8"),
            status=200,
            content_type="text/html",
        )

        html = client.fetch_detail(DETAIL_URL)

        assert "März" in html
        assert "Bürgerschaft" in html


class TestEntryExists:
    @responses.activate
    def test_returns_true_for_200(self, client):
        responses.add(responses.HEAD, _detail_url(2026, 19), status=200)

        assert client.entry_exists(2026, 19) is True

    @responses.activate
    def test_returns_false_for_404(self, client):
        responses.add(responses.HEAD, _detail_url(2026, 99), status=404)

        assert client.entry_exists(2026, 99) is False

    @responses.activate
    def test_raises_on_server_error_instead_of_reading_as_not_found(self, client):
        """A transient 5xx must not be silently read as "not found" — that would
        bias find_max_number's binary search downward with no indication."""
        responses.add(responses.HEAD, _detail_url(2026, 19), status=500)

        with pytest.raises(requests.HTTPError):
            client.entry_exists(2026, 19)

    @responses.activate
    def test_retries_on_429(self, client):
        responses.add(responses.HEAD, _detail_url(2026, 19), status=429)
        responses.add(responses.HEAD, _detail_url(2026, 19), status=200)

        with patch("bawue.rate_limiter.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            result = client.entry_exists(2026, 19)

        assert result is True
        assert len(responses.calls) == 2

    @responses.activate
    def test_uses_correct_url(self, client):
        responses.add(responses.HEAD, _detail_url(2025, 7), status=200)

        client.entry_exists(2025, 7)

        assert responses.calls[0].request.url == _detail_url(2025, 7)

    @responses.activate
    def test_user_agent_set(self, client):
        responses.add(responses.HEAD, _detail_url(2026, 1), status=200)

        client.entry_exists(2026, 1)

        assert "PaZuFa-BaWue-Scraper" in responses.calls[0].request.headers["User-Agent"]


class TestFindMaxNumber:
    @responses.activate
    def test_returns_0_when_year_has_no_entries(self, client):
        responses.add(responses.HEAD, _detail_url(2099, 1), status=404)

        assert client.find_max_number(2099) == 0

    @responses.activate
    def test_finds_correct_max_via_binary_search(self, client):
        max_n = 5

        def handler(request):
            m = re.search(r"/detail/\d+-(\d+)", request.url)
            n = int(m.group(1))
            return (200, {}, "") if n <= max_n else (404, {}, "")

        responses.add_callback(responses.HEAD, DETAIL_URL_PATTERN, callback=handler)

        assert client.find_max_number(2026) == max_n

    @responses.activate
    def test_works_for_single_entry(self, client):
        def handler(request):
            m = re.search(r"/detail/\d+-(\d+)", request.url)
            return (200, {}, "") if int(m.group(1)) == 1 else (404, {}, "")

        responses.add_callback(responses.HEAD, DETAIL_URL_PATTERN, callback=handler)

        assert client.find_max_number(2026) == 1

    @responses.activate
    def test_larger_range(self, client):
        max_n = 119

        def handler(request):
            m = re.search(r"/detail/\d+-(\d+)", request.url)
            n = int(m.group(1))
            return (200, {}, "") if n <= max_n else (404, {}, "")

        responses.add_callback(responses.HEAD, DETAIL_URL_PATTERN, callback=handler)

        assert client.find_max_number(2024) == max_n

    # Boundary values: powers of two (2, 4, 8) sit exactly on the exponential-probe
    # stop and the binary-search lower edge (lo = hi); 3 and 7 sit on the upper edge
    # of their range — the only inputs that exercise the `+1` in (lo+hi+1)//2. The
    # 5/119 cases above are mid-range and would stay green through an off-by-one, so
    # these guard find_max_number against a "slight change" reintroducing a boundary bug.
    @responses.activate
    @pytest.mark.parametrize("max_n", [2, 3, 4, 6, 7, 8, 16])
    def test_boundary_values(self, client, max_n):
        def handler(request):
            m = re.search(r"/detail/\d+-(\d+)", request.url)
            n = int(m.group(1))
            return (200, {}, "") if n <= max_n else (404, {}, "")

        responses.add_callback(responses.HEAD, DETAIL_URL_PATTERN, callback=handler)

        assert client.find_max_number(2026) == max_n

    @responses.activate
    def test_soft_404_terminates_at_cap_without_hanging(self):
        """If the site ever answers 200 for every entry (soft-404), the search must
        still terminate at the sanity cap instead of probing forever and hanging."""
        client = GesetzblattClient(request_delay_s=0.0)

        def always_200(request):
            return (200, {}, "")

        responses.add_callback(responses.HEAD, DETAIL_URL_PATTERN, callback=always_200)

        # Bounded call count proves the binary search terminates (no linear/infinite scan).
        assert client.find_max_number(2026) == _MAX_ENTRIES_PER_YEAR
        assert len(responses.calls) < 100


class TestRequestDelay:
    @responses.activate
    def test_delay_between_calls(self):
        client = GesetzblattClient(request_delay_s=0.1)
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)

        start = time.monotonic()
        client.fetch_detail(DETAIL_URL)
        client.fetch_detail(DETAIL_URL)
        elapsed = time.monotonic() - start

        assert elapsed >= 0.1
