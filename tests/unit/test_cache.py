"""BawueCache connection wiring — host/port vs. managed Redis URL (Upstash)."""

import logging
from unittest.mock import ANY, patch

import pytest
import redis

from bawue.cache import BawueCache
from bawue.config import BawueConfig

UPSTASH_URL = "rediss://default:secret-token@example-12345.upstash.io:6379"
UPSTASH_TARGET = "example-12345.upstash.io:6379"


def test_host_port_used_when_no_url():
    with patch("bawue.cache.redis.Redis") as mock_redis:
        cache = BawueCache("localhost", 6379)
    mock_redis.assert_called_once_with(host="localhost", port=6379, decode_responses=True, retry=ANY)
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


def test_empty_url_falls_back_to_host_port():
    with patch("bawue.cache.redis.Redis") as mock_redis:
        BawueCache("localhost", 6379, redis_url="")
    mock_redis.assert_called_once()
    mock_redis.from_url.assert_not_called()


def test_disabled_wins_over_url():
    with patch("bawue.cache.redis.Redis") as mock_redis:
        cache = BawueCache(None, None, disabled=True, redis_url=UPSTASH_URL)
    assert cache.disabled
    mock_redis.from_url.assert_not_called()


def test_disabled_without_url_or_host():
    cache = BawueCache(None, None)
    assert cache.disabled


@pytest.mark.parametrize(
    "url,credential",
    [
        (UPSTASH_URL, "secret-token"),
        # '@' inside the password — the host is what follows the *last* '@'.
        ("rediss://default:se@cr@et@example-12345.upstash.io:6379", "se@cr@et"),
        # redis-py also accepts credentials as a query parameter.
        ("rediss://example-12345.upstash.io:6379?password=secret-token", "secret-token"),
    ],
)
def test_credentials_are_never_logged(url, credential, caplog):
    with patch("bawue.cache.redis.Redis"), caplog.at_level(logging.INFO, logger="bawue.cache"):
        BawueCache(None, None, redis_url=url)
    assert credential not in caplog.text
    assert UPSTASH_TARGET in caplog.text


def test_connection_failure_exits_naming_the_target(caplog):
    with patch("bawue.cache.redis.Redis") as mock_redis:
        mock_redis.from_url.return_value.ping.side_effect = redis.ConnectionError("refused")
        with pytest.raises(SystemExit) as exc, caplog.at_level(logging.ERROR, logger="bawue.cache"):
            BawueCache(None, None, redis_url=UPSTASH_URL)
    assert exc.value.code == 1
    # The operator has to be able to tell *which* Redis refused, without the token.
    assert UPSTASH_TARGET in caplog.text
    assert "secret-token" not in caplog.text


def test_config_forwards_redis_url_to_cache(monkeypatch, tmp_path):
    """REDIS_URL must survive the trip through BawueConfig into BawueCache."""
    monkeypatch.chdir(tmp_path)  # no config.toml here — env-only resolution
    monkeypatch.setattr("sys.argv", ["bawue"])
    monkeypatch.setenv("REDIS_URL", UPSTASH_URL)
    monkeypatch.setenv("COLLECTOR_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("LTZF_API_KEY", "test-key")

    with patch("bawue.cache.redis.Redis") as mock_redis:
        BawueConfig().load()

    mock_redis.from_url.assert_called_once_with(UPSTASH_URL, decode_responses=True, retry=ANY)


def test_dump_config_redacts_secrets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["bawue"])
    monkeypatch.setenv("REDIS_URL", UPSTASH_URL)
    monkeypatch.setenv("COLLECTOR_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("LTZF_API_KEY", "test-key")

    with patch("bawue.cache.redis.Redis"):
        config = BawueConfig()
        config.load()

    dumped = str(config)
    assert "secret-token" not in dumped
    assert "test-key" not in dumped
