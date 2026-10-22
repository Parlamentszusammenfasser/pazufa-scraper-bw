"""Tests for the Wahlperiode update check."""

import logging

import pytest
import requests
import responses

from bawue.wahlperiode_check import check_for_newer_wahlperiode

BASE_URL = "https://beteiligungsportal.baden-wuerttemberg.de"


class TestCheckForNewerWahlperiode:
    @responses.activate
    def test_warns_when_next_wahlperiode_exists(self, caplog):
        """HTTP 200 for lp-{n+1} triggers a warning with the WP number."""
        responses.add(responses.GET, f"{BASE_URL}/de/mitmachen/lp-18", status=200)

        with caplog.at_level(logging.WARNING, logger="bawue.wahlperiode_check"):
            check_for_newer_wahlperiode(17)

        assert any("18" in msg for msg in caplog.messages)

    @responses.activate
    @pytest.mark.parametrize("wahlperiode", [17, 19])
    def test_no_warning_when_404(self, caplog, wahlperiode):
        """HTTP 404 for lp-{n+1} means no new period — no warning emitted."""
        url = f"{BASE_URL}/de/mitmachen/lp-{wahlperiode + 1}"
        responses.add(responses.GET, url, status=404)

        with caplog.at_level(logging.WARNING, logger="bawue.wahlperiode_check"):
            check_for_newer_wahlperiode(wahlperiode)

        assert not caplog.records
        assert [c.request.url for c in responses.calls] == [url]  # lp-{n+1} only

    @responses.activate
    def test_no_exception_on_network_error(self, caplog):
        """A network error must not propagate — function returns normally."""
        responses.add(
            responses.GET,
            f"{BASE_URL}/de/mitmachen/lp-18",
            body=requests.ConnectionError("unreachable"),
        )

        with caplog.at_level(logging.WARNING, logger="bawue.wahlperiode_check"):
            check_for_newer_wahlperiode(17)  # must not raise

        assert any("lp-18" in msg for msg in caplog.messages)
