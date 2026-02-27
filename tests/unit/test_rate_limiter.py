"""Tests for AdaptiveRateLimiter."""

import logging
from unittest.mock import patch

import pytest

from bawue.rate_limiter import AdaptiveRateLimiter

_logger = logging.getLogger("test")


@pytest.fixture()
def limiter():
    return AdaptiveRateLimiter(
        initial_delay=1.0,
        min_delay=0.1,
        success_factor=0.9,
        backoff_multiplier=30.0,
        recovery_factor=0.5,
    )


class TestWait:
    def test_initial_delay_is_used_on_first_wait(self, limiter):
        """First wait records time; second immediate wait sleeps the full initial_delay."""
        with patch("bawue.rate_limiter.time") as mock_time:
            # First wait: monotonic=100.0 → _last_request_time=100.0
            # Second wait: monotonic=100.0 → elapsed=0 → sleep(1.0)
            mock_time.monotonic.side_effect = [100.0, 100.0, 100.0]
            limiter.wait()  # first call: set _last_request_time=100.0
            limiter.wait()  # second call immediately: should sleep full initial_delay
            mock_time.sleep.assert_called_once_with(pytest.approx(1.0))

    def test_no_sleep_if_enough_time_has_elapsed(self, limiter):
        """No sleep if elapsed time already exceeds current_delay."""
        with patch("bawue.rate_limiter.time") as mock_time:
            # First wait: monotonic=100.0 → _last_request_time=100.0
            # Second wait: monotonic=102.0 → elapsed=2.0 > delay=1.0 → no sleep
            mock_time.monotonic.side_effect = [100.0, 102.0, 102.0]
            limiter.wait()  # first call: records time=100.0
            limiter.wait()  # second call: elapsed=2.0 > delay=1.0, no sleep
            mock_time.sleep.assert_not_called()


class TestOnSuccess:
    def test_success_decreases_delay(self, limiter):
        initial = limiter.current_delay
        limiter.on_success()
        assert limiter.current_delay < initial

    def test_success_multiplies_by_factor(self, limiter):
        limiter.on_success()
        assert limiter.current_delay == pytest.approx(1.0 * 0.9)

    def test_delay_does_not_go_below_min(self, limiter):
        for _ in range(200):
            limiter.on_success()
        assert limiter.current_delay == pytest.approx(0.1)


class TestOnRateLimit:
    def test_rate_limit_triggers_backoff_pause(self, limiter):
        with patch("bawue.rate_limiter.time") as mock_time:
            limiter.on_rate_limit(_logger)
            # pause = current_delay * backoff_multiplier = 1.0 * 30 = 30s
            mock_time.sleep.assert_called_once_with(pytest.approx(30.0))

    def test_delay_after_rate_limit_is_recovery_fraction_of_pause(self, limiter):
        with patch("bawue.rate_limiter.time"):
            limiter.on_rate_limit(_logger)
        # pause = 1.0 * 30 = 30s, new delay = 30 * 0.5 = 15s
        assert limiter.current_delay == pytest.approx(15.0)

    def test_recovery_gradually_decreases_on_subsequent_success(self, limiter):
        with patch("bawue.rate_limiter.time"):
            limiter.on_rate_limit(_logger)

        delay_after_rl = limiter.current_delay
        limiter.on_success()
        assert limiter.current_delay == pytest.approx(delay_after_rl * 0.9)

        limiter.on_success()
        assert limiter.current_delay == pytest.approx(delay_after_rl * 0.9 * 0.9)
