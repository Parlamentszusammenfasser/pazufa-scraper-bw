"""BawueCache connection wiring — host/port vs. managed Redis URL (Upstash)."""

import logging
from unittest.mock import MagicMock, patch

from bawue.cache import BawueCache

UPSTASH_URL = "rediss://default:secret-token@example-12345.upstash.io:6379"


def test_host_port_used_when_no_url():
    with patch("bawue.cache.redis.Redis") as mock_redis:
        BawueCache("localhost", 6379)
    mock_redis.assert_called_once_with(host="localhost", port=6379, decode_responses=True)


def test_url_takes_precedence_over_host_port():
    with patch("bawue.cache.redis.Redis") as mock_redis:
        mock_redis.from_url = MagicMock()
        BawueCache("localhost", 6379, redis_url=UPSTASH_URL)
    mock_redis.from_url.assert_called_once_with(UPSTASH_URL, decode_responses=True)
    mock_redis.assert_not_called()


def test_url_alone_enables_cache_without_host_port():
    with patch("bawue.cache.redis.Redis") as mock_redis:
        mock_redis.from_url = MagicMock()
        cache = BawueCache(None, None, redis_url=UPSTASH_URL)
    assert not cache.disabled


def test_token_is_not_logged(caplog):
    with patch("bawue.cache.redis.Redis") as mock_redis:
        mock_redis.from_url = MagicMock()
        with caplog.at_level(logging.INFO, logger="bawue.cache"):
            BawueCache(None, None, redis_url=UPSTASH_URL)
    assert "secret-token" not in caplog.text
    assert "example-12345.upstash.io:6379" in caplog.text


def test_disabled_without_url_or_host():
    cache = BawueCache(None, None)
    assert cache.disabled
