"""Adaptive rate limiter using an AIMD-inspired algorithm."""

import logging
import time


class AdaptiveRateLimiter:
    """Elastic delay manager for HTTP clients.

    On success, the delay shrinks toward ``min_delay`` by multiplying with
    ``success_factor``.  On a 429 response, the client pauses for
    ``current_delay * backoff_multiplier`` seconds, then resumes at
    ``pause_duration * recovery_factor`` — much shorter than the pause but
    longer than the pre-429 fast pace.
    """

    def __init__(
        self,
        initial_delay: float = 1.0,
        min_delay: float = 0.1,
        success_factor: float = 0.9,
        backoff_multiplier: float = 30.0,
        recovery_factor: float = 0.5,
    ) -> None:
        self._current_delay = initial_delay
        self._min_delay = min_delay
        self._success_factor = success_factor
        self._backoff_multiplier = backoff_multiplier
        self._recovery_factor = recovery_factor
        self._last_request_time: float = 0.0

    @property
    def current_delay(self) -> float:
        return self._current_delay

    def wait(self) -> None:
        """Elastic wait: sleep only the time remaining since the last request."""
        if self._last_request_time > 0:
            elapsed = time.monotonic() - self._last_request_time
            remaining = self._current_delay - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_time = time.monotonic()

    def on_success(self) -> None:
        """Decrease delay toward ``min_delay`` after a successful request."""
        self._current_delay = max(self._min_delay, self._current_delay * self._success_factor)

    def on_rate_limit(self, logger: logging.Logger) -> None:
        """Handle a 429 response: long pause, then set a cautious recovery delay."""
        pause_duration = self._current_delay * self._backoff_multiplier
        logger.warning("Rate limited (429). Pausing for %.1fs before retry.", pause_duration)
        time.sleep(pause_duration)
        self._current_delay = pause_duration * self._recovery_factor


def create_upload_limiter() -> AdaptiveRateLimiter:
    """Create a rate limiter pre-configured for API uploads."""
    return AdaptiveRateLimiter(
        initial_delay=0.2,
        min_delay=0.05,
        backoff_multiplier=10.0,
        recovery_factor=0.5,
    )
