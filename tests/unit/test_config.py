"""BawueConfig.load(): CLI > env > config file > default, as documented in config.py."""

from unittest.mock import ANY, patch

import pytest

from bawue.config import BawueConfig

COLLECTOR = '[main]\ncollector-uuid = "00000000-0000-0000-0000-000000000001"\n'
UPSTASH_URL = "rediss://default:secret-token@example-12345.upstash.io:6379"
ENV = ("LTZF_API_KEY", "COLLECTOR_ID", "MAX_CONCURRENCY", "REDIS_PORT", "REDIS_HOST", "REDIS_URL")


@pytest.fixture
def redis_mock():
    with patch("bawue.cache.redis.Redis") as mock:
        yield mock


@pytest.fixture
def load(monkeypatch, tmp_path, redis_mock):
    monkeypatch.chdir(tmp_path)
    for var in ENV:
        monkeypatch.delenv(var, raising=False)

    def _load(*argv, toml=COLLECTOR, **env):
        (tmp_path / "config.toml").write_text(toml)
        monkeypatch.setattr("sys.argv", ["bawue", *argv])
        for var, value in env.items():
            monkeypatch.setenv(var, value)
        config = BawueConfig()
        config.load()
        return config

    return _load


@pytest.mark.parametrize(
    ("argv", "env", "api_key"),
    [
        ((), {}, "from-file"),
        ((), {"LTZF_API_KEY": "from-env"}, "from-env"),
        (("--ltzf-api-key", "from-cli"), {"LTZF_API_KEY": "from-env"}, "from-cli"),
    ],
    ids=["file", "env-over-file", "cli-over-env"],
)
def test_precedence(load, argv, env, api_key):
    config = load(*argv, toml=COLLECTOR + '[backend]\nltzf-api-key = "from-file"\n', **env)
    assert config.api_key == api_key


def test_config_file_flag_wins_over_cwd(load, tmp_path):
    """Docker, Compose and the Makefile all start with --config-file."""
    other = tmp_path / "other.toml"
    other.write_text(COLLECTOR + '[backend]\nltzf-api-key = "from-flag"\n')
    config = load("--config-file", str(other), toml=COLLECTOR + '[backend]\nltzf-api-key = "from-cwd"\n')
    assert config.api_key == "from-flag"


def test_env_numbers_become_ints(load):
    """Cloud Run configures by env, as strings; asyncio.Semaphore and redis need ints."""
    config = load(LTZF_API_KEY="k", MAX_CONCURRENCY="5", REDIS_PORT="6380")
    assert config.max_concurrency == 5
    assert config.redis_port == 6380


def test_missing_required_setting_exits(load):
    with pytest.raises(SystemExit) as exc:
        load(toml="")  # neither collector-uuid nor an API key
    assert exc.value.code == 1


@pytest.mark.parametrize(
    "argv",
    [(), ("--once",)],  # --once is a no-op since every run is one cycle; old commands still pass it
    ids=["plain", "once-flag"],
)
def test_config_forwards_redis_url_to_cache(load, redis_mock, argv):
    """REDIS_URL must survive the trip through BawueConfig into BawueCache."""
    load(*argv, LTZF_API_KEY="test-key", REDIS_URL=UPSTASH_URL)
    redis_mock.from_url.assert_called_once_with(UPSTASH_URL, decode_responses=True, retry=ANY)


def test_dump_config_redacts_secrets(load):
    dumped = str(load(LTZF_API_KEY="test-key", REDIS_URL=UPSTASH_URL))
    assert "secret-token" not in dumped
    assert "test-key" not in dumped
