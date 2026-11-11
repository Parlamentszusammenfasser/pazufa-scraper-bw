"""Tests for the Gesetzblatt Baden-Württemberg HTTP client."""

import re
import time
from unittest.mock import patch

import pytest
import requests
import responses

from bawue.gesetzblatt_client import BASE_URL, GesetzblattClient

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


class TestFetchPdf:
    @responses.activate
    def test_issue182_pdf_is_capped_at_max_pdf_bytes(self, client, monkeypatch):
        monkeypatch.setattr("bawue.gesetzblatt_client.MAX_PDF_BYTES", 16)
        url = f"{BASE_URL}/index.php?eID=dumpFile&f=1"
        responses.add(responses.GET, url, body=b"x" * 16)
        responses.add(responses.GET, url, body=b"x" * 17)

        assert client.fetch_pdf(url) == b"x" * 16
        with pytest.raises(ValueError, match=r"larger than 16 bytes"):
            client.fetch_pdf(url)
