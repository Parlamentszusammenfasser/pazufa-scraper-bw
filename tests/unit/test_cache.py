"""BawueCache connection wiring — host/port vs. managed Redis URL (Upstash)."""

import logging
from unittest.mock import ANY, patch

import pytest
import redis

from bawue.cache import BawueCache, RedisUnavailableError

UPSTASH_URL = "rediss://default:secret-token@example-12345.upstash.io:6379"
UPSTASH_TARGET = "example-12345.upstash.io:6379"


@pytest.mark.parametrize("redis_url", [None, ""])
def test_host_port_used_when_no_url(redis_url):
    with patch("bawue.cache.redis.Redis") as mock_redis:
        cache = BawueCache("localhost", 6379, redis_url=redis_url)
    mock_redis.assert_called_once_with(host="localhost", port=6379, decode_responses=True, retry=ANY)
    mock_redis.from_url.assert_not_called()
    mock_redis.return_value.ping.assert_called_once()
    assert not cache.disabled


def test_url_takes_precedence_over_host_port():
    with patch("bawue.cache.redis.Redis") as mock_redis:
        BawueCache("localhost", 6379, redis_url=UPSTASH_URL)
    mock_redis.from_url.assert_called_once_with(UPSTASH_URL, decode_responses=True, retry=ANY)
    mock_redis.from_url.return_value.ping.assert_called_once()
    mock_redis.assert_not_called()


def test_url_alone_enables_cache_without_host_port():
    with patch("bawue.cache.redis.Redis"):
        cache = BawueCache(None, None, redis_url=UPSTASH_URL)
    assert not cache.disabled


def test_disabled_without_url_or_host():
    cache = BawueCache(None, None)
    assert cache.disabled


@pytest.mark.parametrize(
    ("url", "credential", "target"),
    [
        (UPSTASH_URL, "secret-token", UPSTASH_TARGET),
        # '@' inside the password — the host is what follows the *last* '@'.
        ("rediss://default:se@cr@et@example-12345.upstash.io:6379", "se@cr@et", UPSTASH_TARGET),
        # redis-py also accepts credentials as a query parameter.
        ("rediss://example-12345.upstash.io:6379?password=secret-token", "secret-token", UPSTASH_TARGET),
        # Unparseable (unclosed IPv6 bracket): a placeholder, never the raw URL.
        ("rediss://default:secret-token@[example-12345.upstash.io:6379", "secret-token", "<redis-url>"),
    ],
)
def test_credentials_are_never_logged(url, credential, target, caplog):
    with patch("bawue.cache.redis.Redis"), caplog.at_level(logging.INFO, logger="bawue.cache"):
        BawueCache(None, None, redis_url=url)
    assert credential not in caplog.text
    assert target in caplog.text


@pytest.mark.parametrize(
    "error",
    [redis.ConnectionError("refused"), redis.TimeoutError("Timeout connecting to server")],
    ids=["refused", "timeout"],  # TimeoutError is not a ConnectionError
)
def test_connection_failure_raises_naming_the_target(caplog, error):
    """DD-073: any failure to connect stops the run, not only a refused connection."""
    with patch("bawue.cache.redis.Redis") as mock_redis:
        mock_redis.from_url.return_value.ping.side_effect = error
        with pytest.raises(RedisUnavailableError) as exc, caplog.at_level(logging.ERROR, logger="bawue.cache"):
            BawueCache(None, None, redis_url=UPSTASH_URL)
    # The operator has to be able to tell *which* Redis refused, without the token.
    assert UPSTASH_TARGET in str(exc.value)
    assert UPSTASH_TARGET in caplog.text
    assert "secret-token" not in str(exc.value) + caplog.text


def test_issue105_read_error_raises_instead_of_reading_as_a_miss():
    with patch("bawue.cache.redis.Redis"):
        cache = BawueCache("localhost", 6379)
    cache.redis_client.get.side_effect = redis.ConnectionError("gone")
    with pytest.raises(RedisUnavailableError):
        cache.get_raw("vg2:V-1")


def test_issue105_disabled_cache_still_reads_as_a_miss():
    assert BawueCache(None, None).get_raw("vg2:V-1") is None


@pytest.mark.parametrize(
    ("set_result", "stored"),
    [(True, True), (None, False), (redis.ConnectionError("gone"), False)],
    ids=["written", "not-written", "error"],
)
def test_store_raw_reports_whether_the_write_happened(set_result, stored):
    """A failed write must not read as success, else the Wochenbericht is posted twice."""
    with patch("bawue.cache.redis.Redis"):
        cache = BawueCache("localhost", 6379)
    if isinstance(set_result, Exception):
        cache.redis_client.set.side_effect = set_result
    else:
        cache.redis_client.set.return_value = set_result
    assert cache.store_raw("woche", "{}") is stored


@pytest.mark.parametrize(("value", "read"), [("fingerprint", "fingerprint"), (None, None), ("", None)])
def test_get_raw_reads_a_miss_as_none(value, read):
    with patch("bawue.cache.redis.Redis"):
        cache = BawueCache("localhost", 6379)
    cache.redis_client.get.return_value = value
    assert cache.get_raw("vg2:V-1") == read
