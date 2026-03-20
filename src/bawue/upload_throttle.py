"""Retry wrapper for API uploads with adaptive rate limiting."""

import logging
from collections.abc import Callable

from bawue.rate_limiter import AdaptiveRateLimiter

logger = logging.getLogger(__name__)


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
