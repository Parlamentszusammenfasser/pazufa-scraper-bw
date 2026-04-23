"""Retry wrapper for API uploads with adaptive rate limiting."""

import logging
from collections.abc import Callable
from typing import NamedTuple
from uuid import UUID

import openapi_client
import openapi_client.api
import openapi_client.api.collector_schnittstellen_api
from openapi_client.models import Vorgang

from bawue.rate_limiter import AdaptiveRateLimiter

logger = logging.getLogger(__name__)


class UploadOutcome(NamedTuple):
    """Result of an upload attempt.

    ``vorgang`` is the item on success, None on failure.
    ``error`` is a short human-readable reason on failure (e.g.
    ``"HTTP 422"``, ``"ConnectionError: ..."``), used for run reports.
    """

    vorgang: Vorgang | None
    error: str | None = None


def with_upload_retry[T](
    api_call: Callable[[], T],
    rate_limiter: AdaptiveRateLimiter,
    *,
    max_retries: int = 5,
    exception_type: type[Exception] = Exception,
) -> T:
    """Execute api_call with adaptive throttling and 429 retry.

    Args:
        api_call: Zero-arg callable that performs the API request.
        rate_limiter: Shared AdaptiveRateLimiter instance for pacing.
        max_retries: Maximum number of retries on 429 responses.
        exception_type: The API exception type that has a `.status` attribute.

    Returns:
        The result of api_call() on success.

    Raises:
        The original exception on non-429 errors or after max_retries exhausted.
    """
    for attempt in range(max_retries + 1):
        rate_limiter.wait()
        try:
            result = api_call()
            rate_limiter.on_success()
            return result
        except exception_type as e:
            if getattr(e, "status", None) == 429 and attempt < max_retries:
                rate_limiter.on_rate_limit(logger)
                continue
            raise


def upload_vorgang(
    oapiconfig: openapi_client.Configuration,
    scraper_id: UUID,
    upload_limiter: AdaptiveRateLimiter,
    item: Vorgang,
    *,
    dry_run: bool = False,
    log_item: Callable | None = None,
) -> UploadOutcome:
    """Upload a Vorgang to the PaZuFa API with retry and error handling.

    Returns an ``UploadOutcome`` with the item on success, or a short
    error reason on failure. Raises nothing — errors are logged and the
    reason is returned so the caller can report it.
    """
    logger.info("Sending Vorgang '%s' (id=%s) to API", item.titel, item.api_id)
    if log_item:
        log_item(item)

    if dry_run:
        logger.info("[DRY RUN] Would send Vorgang '%s' — skipping API call", item.titel)
        return UploadOutcome(vorgang=item)

    try:
        with openapi_client.ApiClient(oapiconfig) as api_client:
            api_instance = openapi_client.api.collector_schnittstellen_api.CollectorSchnittstellenApi(api_client)
            with_upload_retry(
                lambda: api_instance.vorgang_put(str(scraper_id), item),
                upload_limiter,
                exception_type=openapi_client.ApiException,
            )
        return UploadOutcome(vorgang=item)
    except openapi_client.ApiException as e:
        logger.error("API Exception: %s", e)
        reason = _api_exception_reason(e)
        if e.status == 422:
            logger.error("Unprocessable Entity for Vorgang '%s'", item.titel)
            if log_item:
                log_item(item, True)
        elif e.status == 401:
            logger.critical("Authentication failed. Check your API key.")
        return UploadOutcome(vorgang=None, error=reason)
    except Exception as e:
        logger.error("Unexpected error sending Vorgang to API: %s", e)
        return UploadOutcome(vorgang=None, error=f"{type(e).__name__}: {e}")


def _api_exception_reason(exc: Exception) -> str:
    """Build a short ``HTTP <status> <reason>`` tag for a run summary."""
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None) or ""
    if status is None:
        return f"{type(exc).__name__}: {exc}"
    return f"HTTP {status} {reason}".rstrip()
