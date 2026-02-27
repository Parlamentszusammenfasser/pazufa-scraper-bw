"""Tests for the PARLIS HTTP client."""

from datetime import date
from unittest.mock import patch

import pytest
import responses

from bawue.parlis_client import BASE_URL, BROWSE_URL, REPORT_URL, ParlisClient

SAMPLE_HTML_RECORD = """<html><body>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Gesetz zur Änderung des Landeshochschulgesetzes</a>
  <dl>
    <dt>Vorgangs-ID:</dt><dd>V-12345</dd>
    <dt>Vorgangstyp:</dt><dd>Gesetzgebung</dd>
    <dt>Initiative:</dt><dd>Fraktion GRÜNE</dd>
  </dl>
  <a class="fundstellenLinks" href="https://example.com/doc.pdf">
    Gesetzentwurf    Fraktion GRÜNE  04.02.2026 Drucksache 17/10266   (13 S.)
  </a>
  <script>var url = "/parlis/vorgang/V-12345";</script>
</div>
</body></html>"""


@pytest.fixture()
def client():
    """Create a ParlisClient with zero request delay for fast tests."""
    return ParlisClient(wahlperiode=17, request_delay_s=0.0)


def _mock_search(html, item_count=1):
    """Register standard session + search + report mocks for a single search call."""
    responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
    responses.add(responses.POST, BROWSE_URL, json={"report_id": "rpt-1", "item_count": item_count}, status=200)
    if item_count > 0:
        responses.add(responses.GET, REPORT_URL, body=html, status=200)


class TestSessionEstablishment:
    @responses.activate
    def test_establish_session_sets_cookies(self, client):
        responses.add(
            responses.GET,
            BASE_URL,
            body="<html>PARLIS</html>",
            status=200,
            headers={"Set-Cookie": "JSESSIONID=abc123; Path=/"},
        )
        client._establish_session()
        assert len(responses.calls) == 1
        assert responses.calls[0].request.url == BASE_URL


class TestSearchQueryConstruction:
    @responses.activate
    def test_search_sends_correct_post_body(self, client):
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={"report_id": "rpt-123", "item_count": 0},
            status=200,
        )

        client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))

        post_call = responses.calls[1]
        import json

        body = json.loads(post_call.request.body)
        assert body["action"] == "SearchAndDisplay"
        assert body["search"]["lines"]["l1"] == "17"
        assert body["search"]["lines"]["l2"] == "01.01.2026"
        assert body["search"]["lines"]["l3"] == "01.02.2026"
        assert body["search"]["lines"]["l4"] == "Gesetzgebung"
        assert body["search"]["serverrecordname"] == "vorgang"

    @responses.activate
    def test_search_without_dates_sends_empty_strings(self, client):
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={"report_id": "rpt-123", "item_count": 0},
            status=200,
        )

        client.search("Gesetzgebung")

        post_call = responses.calls[1]
        import json

        body = json.loads(post_call.request.body)
        assert body["search"]["lines"]["l2"] == ""
        assert body["search"]["lines"]["l3"] == ""

    @responses.activate
    def test_search_uses_configured_wahlperiode(self):
        client = ParlisClient(wahlperiode=18, request_delay_s=0.0)
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={"report_id": "rpt-123", "item_count": 0},
            status=200,
        )

        client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))
        import json

        body = json.loads(responses.calls[1].request.body)
        assert body["search"]["lines"]["l1"] == "18"


SAMPLE_HTML_MARCH_DATE = """<html><body>
<div class="efxRecordRepeater">
  <a class="efxZoomShort-Vorgang">Anfrage über Frühjahrsplanung</a>
  <dl>
    <dt>Vorgangs-ID:</dt><dd>V-99999</dd>
  </dl>
  <a class="fundstellenLinks" href="https://example.com/doc.pdf">
    Beschluss  18. März 2025 Drucksache 17/9999
  </a>
  <script>var url = "/parlis/vorgang/V-99999";</script>
</div>
</body></html>"""


class TestResultParsing:
    @responses.activate
    def test_utf8_fundstelle_date_decoded_correctly(self, client):
        """Serve UTF-8 bytes without charset; März must survive decoding."""
        html_bytes = SAMPLE_HTML_MARCH_DATE.encode("utf-8")
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(responses.POST, BROWSE_URL, json={"report_id": "rpt-1", "item_count": 1}, status=200)
        responses.add(responses.GET, REPORT_URL, body=html_bytes, status=200, content_type="text/html")

        results = client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))

        assert len(results) == 1
        fundstellen = results[0].get("fundstellen_parsed", [])
        assert len(fundstellen) == 1
        assert fundstellen[0].get("datum") == "18.03.2025"

    @responses.activate
    def test_parses_vorgang_from_html(self, client):
        _mock_search(SAMPLE_HTML_RECORD)

        results = client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))

        assert len(results) == 1
        assert results[0]["titel"] == "Gesetz zur Änderung des Landeshochschulgesetzes"
        assert results[0]["vorgangs_id"] == "V-12345"

    @responses.activate
    def test_zero_results_returns_empty_list(self, client):
        _mock_search(SAMPLE_HTML_RECORD, item_count=0)

        results = client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))
        assert results == []


class TestPagination:
    @responses.activate
    def test_fetches_all_pages(self, client):
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={"report_id": "rpt-123", "item_count": 60},
            status=200,
        )
        inner1 = "\n".join(
            f'<div class="efxRecordRepeater"><a class="efxZoomShort-Vorgang">G{i}</a>'
            f"<dl><dt>Vorgangs-ID:</dt><dd>V-{i:03d}</dd></dl></div>"
            for i in range(50)
        )
        inner2 = "\n".join(
            f'<div class="efxRecordRepeater"><a class="efxZoomShort-Vorgang">G{i}</a>'
            f"<dl><dt>Vorgangs-ID:</dt><dd>V-{i:03d}</dd></dl></div>"
            for i in range(50, 60)
        )
        responses.add(responses.GET, REPORT_URL, body=f"<html><body>{inner1}</body></html>", status=200)
        responses.add(responses.GET, REPORT_URL, body=f"<html><body>{inner2}</body></html>", status=200)

        results = client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))

        assert len(results) == 60
        report_calls = [c for c in responses.calls if REPORT_URL in c.request.url]
        assert len(report_calls) == 2


class TestDateSubdivision:
    @responses.activate
    def test_subdivides_on_running_status(self, client):
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={
                "report_id": "",
                "item_count": 0,
                "sources": {"Star": {"status": "running", "hits": "5000"}},
            },
            status=200,
        )
        # Sub-window 1 (Jan): search + report
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={"report_id": "rpt-jan", "item_count": 1},
            status=200,
        )
        record_jan = (
            '<html><body><div class="efxRecordRepeater">'
            '<a class="efxZoomShort-Vorgang">Anfrage Jan</a>'
            "<dl><dt>Vorgangs-ID:</dt><dd>V-100</dd></dl>"
            "</div></body></html>"
        )
        responses.add(responses.GET, REPORT_URL, body=record_jan, status=200)
        # Sub-window 2 (Feb): search (empty)
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={"report_id": "rpt-feb", "item_count": 0},
            status=200,
        )

        results = client.search("Kleine Anfrage", date(2026, 1, 1), date(2026, 2, 28))

        assert len(results) == 1
        assert results[0]["titel"] == "Anfrage Jan"

    @responses.activate
    def test_no_subdivision_on_normal_response(self, client):
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(
            responses.POST,
            BROWSE_URL,
            json={"report_id": "rpt-123", "item_count": 1},
            status=200,
        )
        record = (
            '<html><body><div class="efxRecordRepeater">'
            '<a class="efxZoomShort-Vorgang">Normal</a>'
            "<dl><dt>Vorgangs-ID:</dt><dd>V-001</dd></dl>"
            "</div></body></html>"
        )
        responses.add(responses.GET, REPORT_URL, body=record, status=200)

        results = client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))

        assert len(results) == 1
        post_calls = [c for c in responses.calls if c.request.method == "POST"]
        assert len(post_calls) == 1


    def test_falls_back_to_wahlperiode_when_no_dates(self):
        """When overflow occurs with no date range, fall back to Wahlperiode start → today."""
        wp_start = date(2021, 4, 26)
        client = ParlisClient(wahlperiode=17, request_delay_s=0.0, wahlperiode_start_date=wp_start)

        raw_result = [{"titel": "Anfrage WP Start", "vorgangs_id": "V-WP1", "fundstellen_parsed": []}]

        call_args: list[tuple] = []

        def fake_search_single(vorgangstyp, date_from, date_to):
            call_args.append((vorgangstyp, date_from, date_to))
            if date_from is None and date_to is None:
                return None  # overflow on unbounded search
            # First monthly window returns one result, rest return empty
            if date_from == wp_start:
                return raw_result
            return []

        with patch.object(client, "_establish_session"), patch.object(
            client, "_search_single", side_effect=fake_search_single
        ):
            results = client.search("Kleine Anfrage")

        assert len(results) == 1
        assert results[0]["vorgangs_id"] == "V-WP1"

        # First call was unbounded
        assert call_args[0] == ("Kleine Anfrage", None, None)
        # Subsequent calls use monthly windows starting from wp_start
        assert call_args[1][1] == wp_start
        assert call_args[1][2] == date(2021, 4, 30)  # end of April 2021

    def test_no_date_no_wahlperiode_start_returns_empty(self, client):
        """Without wahlperiode_start_date, overflow on unbounded search returns empty list."""
        with patch.object(client, "_establish_session"), patch.object(
            client, "_search_single", return_value=None
        ):
            results = client.search("Kleine Anfrage")

        assert results == []


class TestRateLimiting:
    @responses.activate
    def test_post_retries_on_429(self, client):
        """A 429 on the search POST triggers a retry and eventually succeeds."""
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(responses.POST, BROWSE_URL, status=429)
        responses.add(responses.POST, BROWSE_URL, json={"report_id": "", "item_count": 0}, status=200)

        with patch("bawue.rate_limiter.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            results = client.search("Gesetzgebung")

        assert results == []
        post_calls = [c for c in responses.calls if c.request.method == "POST"]
        assert len(post_calls) == 2

    @responses.activate
    def test_get_retries_on_429(self, client):
        """A 429 on a report page GET triggers a retry and eventually succeeds."""
        responses.add(responses.GET, BASE_URL, body="<html></html>", status=200)
        responses.add(responses.POST, BROWSE_URL, json={"report_id": "rpt-1", "item_count": 1}, status=200)
        responses.add(responses.GET, REPORT_URL, status=429)
        responses.add(responses.GET, REPORT_URL, body=SAMPLE_HTML_RECORD, status=200)

        with patch("bawue.rate_limiter.time") as mock_time:
            mock_time.monotonic.return_value = 0.0
            results = client.search("Gesetzgebung", date(2026, 1, 1), date(2026, 2, 1))

        assert len(results) == 1
        report_calls = [c for c in responses.calls if REPORT_URL in c.request.url]
        assert len(report_calls) == 2


class TestMonthlyWindows:
    def test_single_month(self):
        windows = ParlisClient._monthly_windows(date(2026, 1, 1), date(2026, 1, 31))
        assert len(windows) == 1
        assert windows[0] == (date(2026, 1, 1), date(2026, 1, 31))

    def test_multiple_months(self):
        windows = ParlisClient._monthly_windows(date(2026, 1, 15), date(2026, 3, 20))
        assert len(windows) == 3
        assert windows[0] == (date(2026, 1, 15), date(2026, 1, 31))
        assert windows[1] == (date(2026, 2, 1), date(2026, 2, 28))
        assert windows[2] == (date(2026, 3, 1), date(2026, 3, 20))

    def test_cross_year_boundary(self):
        windows = ParlisClient._monthly_windows(date(2025, 12, 1), date(2026, 1, 15))
        assert len(windows) == 2
        assert windows[0] == (date(2025, 12, 1), date(2025, 12, 31))
        assert windows[1] == (date(2026, 1, 1), date(2026, 1, 15))
