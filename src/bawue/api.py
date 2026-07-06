"""Status-aware wrapper around the corelib httpx API client (migration Phase 1.2a).

The generated ``pazufa_corelib.api_client`` endpoint wrappers default to
``raise_on_unexpected_status=False`` and return ``parsed=None`` *silently* on
4xx/5xx — the call appears to succeed. BaWue's upload retry/branch logic depends
on the HTTP status (429 → retry, 422 → log + skip, 401 → fatal), so this module
turns any non-success response into a :class:`BawueApiError` carrying ``.status``,
mirroring the old ``openapi_client.ApiException`` contract that the rest of the
code (``upload_throttle``, ``run_report``) is built on.
"""

import logging
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pazufa_corelib.api_client import AuthenticatedClient
from pazufa_corelib.api_client.api.sitzung import kal_date_put as _kal_date_put
from pazufa_corelib.api_client.api.vorgang import vorgang_put as _vorgang_put

from bawue.types import Parlament, Sitzung, Vorgang

logger = logging.getLogger(__name__)


@dataclass
class BawueApiError(Exception):
    """Raised when the backend returns a non-success status for an upload.

    Exposes ``.status`` so ``upload_throttle.with_upload_retry`` and
    ``run_report.api_exception_reason`` can branch on 429/422/401 exactly as
    they did against the old ``openapi_client.ApiException``.
    """

    status: int
    body: bytes
    method: str

    def __str__(self) -> str:
        return f"HTTP {self.status} from {self.method}: {self.body!r}"


def build_client(database_url: str, api_key: str) -> AuthenticatedClient:
    """Construct an AuthenticatedClient pointed at the backend.

    The backend authenticates via the ``X-API-Key`` header with no bearer
    prefix; ``prefix=""`` makes the client send the raw token under that header.
    """
    return AuthenticatedClient(
        base_url=database_url,
        token=api_key,
        prefix="",
        auth_header_name="X-API-Key",
    )


def put_vorgang(client: AuthenticatedClient, scraper_id: UUID, item: Vorgang) -> None:
    """PUT a Vorgang; raise :class:`BawueApiError` on any non-201 status."""
    r = _vorgang_put.sync_detailed(client=client, body=item, x_scraper_id=str(scraper_id))
    if r.status_code != 201:
        raise BawueApiError(int(r.status_code), r.content, "vorgang_put")


def put_kalender(
    client: AuthenticatedClient,
    scraper_id: UUID,
    parlament: Parlament,
    datum: date,
    sitzungen: list[Sitzung],
) -> None:
    """PUT all sessions for a date; raise :class:`BawueApiError` on a non-201/204 status."""
    r = _kal_date_put.sync_detailed(
        parlament,
        datum,
        client=client,
        body=sitzungen,
        x_scraper_id=str(scraper_id),
    )
    if r.status_code not in (201, 204):
        raise BawueApiError(int(r.status_code), r.content, "kal_date_put")
