"""Tests for the Phase 1.2a status-handling shim (bawue.api).

The whole point of this module is that the corelib client does *not* raise on
4xx/5xx by default — it returns ``parsed=None`` silently. These tests pin the
shim's contract: every non-success status becomes a ``BawueApiError`` carrying
the right ``.status`` (Risk #1).
"""

from datetime import date
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from bawue.api import BawueApiError, put_kalender, put_vorgang
from bawue.types import Parlament


def _resp(status: int) -> MagicMock:
    return MagicMock(status_code=status, content=b"body")


class TestPutVorgang:
    @pytest.mark.parametrize("status", [200, 400, 401, 403, 409, 422, 429, 500, 503])
    def test_raises_bawue_api_error_on_non_201(self, status):
        with (
            patch("bawue.api._vorgang_put.sync_detailed", return_value=_resp(status)),
            pytest.raises(BawueApiError) as excinfo,
        ):
            put_vorgang(MagicMock(), uuid4(), MagicMock())
        assert excinfo.value.status == status
        assert excinfo.value.method == "vorgang_put"

    def test_201_does_not_raise(self):
        with patch("bawue.api._vorgang_put.sync_detailed", return_value=_resp(201)):
            put_vorgang(MagicMock(), uuid4(), MagicMock())  # no exception

    def test_passes_scraper_id_as_str_header(self):
        scraper_id = uuid4()
        item = MagicMock()
        client = MagicMock()
        with patch("bawue.api._vorgang_put.sync_detailed", return_value=_resp(201)) as mock_put:
            put_vorgang(client, scraper_id, item)
        mock_put.assert_called_once_with(client=client, body=item, x_scraper_id=str(scraper_id))


class TestPutKalender:
    @pytest.mark.parametrize("status", [200, 400, 401, 403, 422, 429, 500, 503])
    def test_raises_bawue_api_error_on_non_2xx(self, status):
        with (
            patch("bawue.api._kal_date_put.sync_detailed", return_value=_resp(status)),
            pytest.raises(BawueApiError) as excinfo,
        ):
            put_kalender(MagicMock(), uuid4(), Parlament.BW, date(2026, 2, 25), [])
        assert excinfo.value.status == status
        assert excinfo.value.method == "kal_date_put"

    @pytest.mark.parametrize("status", [201, 204])
    def test_success_statuses_do_not_raise(self, status):
        with patch("bawue.api._kal_date_put.sync_detailed", return_value=_resp(status)):
            put_kalender(MagicMock(), uuid4(), Parlament.BW, date(2026, 2, 25), [])

    def test_path_params_positional_body_and_scraper_id_kwargs(self):
        scraper_id = uuid4()
        client = MagicMock()
        sitzungen = [MagicMock()]
        datum = date(2026, 2, 25)
        with patch("bawue.api._kal_date_put.sync_detailed", return_value=_resp(201)) as mock_put:
            put_kalender(client, scraper_id, Parlament.BW, datum, sitzungen)
        mock_put.assert_called_once_with(
            Parlament.BW, datum, client=client, body=sitzungen, x_scraper_id=str(scraper_id)
        )
