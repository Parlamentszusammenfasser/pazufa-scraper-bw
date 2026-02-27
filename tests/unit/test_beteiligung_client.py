"""Tests for the Beteiligungsportal HTTP client."""

import time

import pytest
import responses

from bawue.beteiligung_client import BeteiligungClient

BASE_URL = "https://beteiligungsportal.baden-wuerttemberg.de"
LP17_URL = f"{BASE_URL}/de/mitmachen/lp-17"
DETAIL_URL = f"{BASE_URL}/de/mitmachen/lp-17/entbuerokratisierung"

SAMPLE_INDEX_HTML = """<html><body>
<article class="teaser tx-bw_textimageteaser_pi1">
    <div class="teaser__image">
        <div class="teaser__badge"><div class="teaser__badge-wrap"><div class="teaser__badge-inner">
            <span class="teaser__badge-text">Abgeschlossen</span>
        </div></div></div>
    </div>
    <div class="teaser__content"><div class="teaser__content-inner">
        <div class="teaser__header">
            <div class="teaser__headline">
                <h2 class="headline headline--3">Test Gesetz</h2>
            </div>
        </div>
        <div class="teaser__footer">
            <a class="icon-link teaser__overlay-link" href="/de/mitmachen/lp-17/test-gesetz">Mehr</a>
        </div>
    </div></div>
</article>
</body></html>"""

SAMPLE_DETAIL_HTML = "<html><body><h1>Detail</h1></body></html>"


@pytest.fixture()
def client():
    return BeteiligungClient(wahlperiode=17, request_delay_s=0.0)


class TestFetchProcessList:
    @responses.activate
    def test_returns_processes(self, client):
        responses.add(responses.GET, LP17_URL, body=SAMPLE_INDEX_HTML, status=200)

        processes = client.fetch_process_list()

        assert len(processes) == 1
        assert processes[0].title == "Test Gesetz"
        assert processes[0].slug == "test-gesetz"

    @responses.activate
    def test_uses_correct_url_for_wahlperiode(self):
        client = BeteiligungClient(wahlperiode=18, request_delay_s=0.0)
        lp18_url = f"{BASE_URL}/de/mitmachen/lp-18"
        responses.add(responses.GET, lp18_url, body=SAMPLE_INDEX_HTML, status=200)

        client.fetch_process_list()

        assert responses.calls[0].request.url == lp18_url


class TestFetchProcessDetail:
    @responses.activate
    def test_utf8_response_decoded_correctly(self, client):
        """Serve UTF-8 bytes without charset; German umlauts must survive decoding."""
        html_with_umlauts = "<html><body><h1>Bürgerschaftliches Engagement</h1><p>März 2025</p></body></html>"
        html_bytes = html_with_umlauts.encode("utf-8")
        responses.add(responses.GET, DETAIL_URL, body=html_bytes, status=200, content_type="text/html")

        html = client.fetch_process_detail("/de/mitmachen/lp-17/entbuerokratisierung")

        assert "März" in html
        assert "Bürgerschaftliches" in html

    @responses.activate
    def test_returns_html(self, client):
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)

        html = client.fetch_process_detail("/de/mitmachen/lp-17/entbuerokratisierung")

        assert "<h1>Detail</h1>" in html

    @responses.activate
    def test_constructs_absolute_url(self, client):
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)

        client.fetch_process_detail("/de/mitmachen/lp-17/entbuerokratisierung")

        assert responses.calls[0].request.url == DETAIL_URL


class TestSessionHeaders:
    @responses.activate
    def test_user_agent(self, client):
        responses.add(responses.GET, LP17_URL, body=SAMPLE_INDEX_HTML, status=200)

        client.fetch_process_list()

        assert "PaZuFa-BaWue-Scraper" in responses.calls[0].request.headers["User-Agent"]

    @responses.activate
    def test_accept_language(self, client):
        responses.add(responses.GET, LP17_URL, body=SAMPLE_INDEX_HTML, status=200)

        client.fetch_process_list()

        assert "de-DE" in responses.calls[0].request.headers["Accept-Language"]


class TestRequestDelay:
    @responses.activate
    def test_delay_between_calls(self):
        client = BeteiligungClient(wahlperiode=17, request_delay_s=0.1)
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)
        responses.add(responses.GET, DETAIL_URL, body=SAMPLE_DETAIL_HTML, status=200)

        start = time.monotonic()
        client.fetch_process_detail("/de/mitmachen/lp-17/entbuerokratisierung")
        client.fetch_process_detail("/de/mitmachen/lp-17/entbuerokratisierung")
        elapsed = time.monotonic() - start

        assert elapsed >= 0.1
