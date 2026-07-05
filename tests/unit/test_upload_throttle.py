"""Tests for upload_throttle retry wrapper."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from bawue.rate_limiter import AdaptiveRateLimiter
from bawue.upload_throttle import with_upload_retry

_logger = logging.getLogger("test")


class FakeApiException(Exception):
    """Minimal stand-in for a status-carrying API error (like bawue.api.BawueApiError)."""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"({status})")


@pytest.fixture()
def limiter():
    return AdaptiveRateLimiter(
        initial_delay=0.2,
        min_delay=0.05,
        backoff_multiplier=10.0,
        recovery_factor=0.5,
    )


class TestRetryOn429:
    def test_succeeds_after_transient_429(self, limiter):
        """A single 429 followed by success should return the result."""
        api_call = MagicMock(side_effect=[FakeApiException(429), "ok"])
        with patch("bawue.rate_limiter.time.sleep"):
            result = with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        assert result == "ok"
        assert api_call.call_count == 2

    def test_succeeds_after_multiple_429s(self, limiter):
        """Multiple 429s followed by success should eventually return."""
        api_call = MagicMock(side_effect=[FakeApiException(429), FakeApiException(429), FakeApiException(429), "done"])
        with patch("bawue.rate_limiter.time.sleep"):
            result = with_upload_retry(api_call, limiter, max_retries=5, exception_type=FakeApiException)
        assert result == "done"
        assert api_call.call_count == 4


class TestMaxRetriesExhausted:
    def test_raises_after_max_retries(self, limiter):
        """After max_retries 429s, the exception should propagate."""
        api_call = MagicMock(side_effect=FakeApiException(429))
        with patch("bawue.rate_limiter.time.sleep"), pytest.raises(FakeApiException):
            with_upload_retry(api_call, limiter, max_retries=2, exception_type=FakeApiException)
        assert api_call.call_count == 3  # initial + 2 retries


class TestNon429NotRetried:
    def test_500_raises_immediately(self, limiter):
        """Non-429 API errors should not be retried."""
        api_call = MagicMock(side_effect=FakeApiException(500))
        with pytest.raises(FakeApiException):
            with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        assert api_call.call_count == 1

    def test_422_raises_immediately(self, limiter):
        """422 Unprocessable Entity should not be retried."""
        api_call = MagicMock(side_effect=FakeApiException(422))
        with pytest.raises(FakeApiException):
            with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        assert api_call.call_count == 1

    def test_non_api_exception_raises_immediately(self, limiter):
        """Non-API exceptions should propagate without retry."""
        api_call = MagicMock(side_effect=ValueError("bad"))
        with pytest.raises(ValueError, match="bad"):
            with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        assert api_call.call_count == 1


class TestRateLimiterIntegration:
    def test_on_rate_limit_called_on_429(self, limiter):
        """The rate limiter's on_rate_limit should be called on 429."""
        api_call = MagicMock(side_effect=[FakeApiException(429), "ok"])
        with patch("bawue.rate_limiter.time.sleep"):
            with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        # After 429 + recovery, delay should be higher than min
        assert limiter.current_delay > limiter._min_delay

    def test_on_success_called_after_success(self, limiter):
        """The rate limiter's on_success should decrease delay after success."""
        api_call = MagicMock(return_value="ok")
        initial_delay = limiter.current_delay
        with patch("bawue.rate_limiter.time.sleep"):
            with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        assert limiter.current_delay < initial_delay

    def test_wait_called_before_each_attempt(self, limiter):
        """rate_limiter.wait() should be called before each API call attempt."""
        api_call = MagicMock(side_effect=[FakeApiException(429), "ok"])
        with patch.object(limiter, "wait") as mock_wait, patch("bawue.rate_limiter.time.sleep"):
            with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        assert mock_wait.call_count == 2


class TestSuccessOnFirstTry:
    def test_returns_result_on_first_success(self, limiter):
        """Happy path: no retry needed."""
        api_call = MagicMock(return_value={"id": "123"})
        with patch("bawue.rate_limiter.time.sleep"):
            result = with_upload_retry(api_call, limiter, exception_type=FakeApiException)
        assert result == {"id": "123"}
        assert api_call.call_count == 1
