"""BawueConfig.load(): CLI > env > config file > default, as documented in config.py."""

from unittest.mock import ANY, patch

import pytest
import toml

from bawue.config import BawueConfig

COLLECTOR = '[main]\ncollector-uuid = "00000000-0000-0000-0000-000000000001"\n'
UPSTASH_URL = "rediss://default:secret-token@example-12345.upstash.io:6379"
ENV = (
    *("LTZF_API_KEY", "COLLECTOR_ID", "MAX_CONCURRENCY", "REDIS_PORT", "REDIS_HOST", "REDIS_URL"),
    *("WAHLPERIODE", "WAHLPERIODE_START_DATE", "PARLIS_REQUEST_DELAY_S", "BETEILIGUNGSFRISTEN", "MATTERMOST_HOOK"),
)
SECTIONS = """
[bawue]
wahlperiode = 17
parlis-request-delay-s = 1.0
ics-url = "https://example.org/cal.ics"
beteiligungsfristen = false

[notifications]
mattermost-hook = "https://chat.example.org/hooks/hook-token"
mattermost-username = "bawue-scraper"
"""


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


def test_falsy_file_value_counts_as_written(load):
    """DD-078: `0`/`false` from the file replace the default."""
    config = load(toml=COLLECTOR + "max-concurrency = 0\n", LTZF_API_KEY="k")
    assert config.max_concurrency == 0


def test_empty_required_setting_counts_as_missing(load):
    with pytest.raises(SystemExit) as exc:
        load(toml=COLLECTOR + '[backend]\nltzf-api-key = ""\n')
    assert exc.value.code == 1


def test_dump_config_lists_sections_and_redacts_secrets(load):
    dumped = str(load(toml=COLLECTOR + SECTIONS, LTZF_API_KEY="test-key", REDIS_URL=UPSTASH_URL))
    assert "bawue.ics-url: https://example.org/cal.ics" in dumped
    assert "notifications.mattermost-hook: ***" in dumped
    for secret in ("secret-token", "test-key", "hook-token"):
        assert secret not in dumped


def test_toml_file_is_read_once(load):
    with patch("bawue.config.toml.load", wraps=toml.load) as read:
        config = load(toml=COLLECTOR + SECTIONS, LTZF_API_KEY="k")
        config.section("bawue")
        config.section("notifications")
    read.assert_called_once()


def test_section_keeps_falsy_file_values(load):
    """Kill switches like `beteiligungsfristen = false` (DD-076) must survive (issue #102, DD-078)."""
    config = load(toml=COLLECTOR + SECTIONS, LTZF_API_KEY="k")
    assert config.section("bawue")["beteiligungsfristen"] is False
    assert config.section("gesetzblatt") == {}


# Env overrides for section keys let a cloud deployment run the image's default config.toml.
@pytest.mark.parametrize("env", [{}, {"WAHLPERIODE": ""}], ids=["unset", "empty"])  # Cloud Run passes unset as ""
def test_section_file_values_used_without_env_value(load, env):
    assert load(toml=COLLECTOR + SECTIONS, LTZF_API_KEY="k", **env).section("bawue")["wahlperiode"] == 17


def test_section_env_overrides_file_and_keeps_types(load):
    config = load(
        toml=COLLECTOR + SECTIONS,
        LTZF_API_KEY="k",
        WAHLPERIODE="18",
        WAHLPERIODE_START_DATE="2026-05-01",
        PARLIS_REQUEST_DELAY_S="0.8",
        MATTERMOST_HOOK="https://chat.example.org/hooks/from-env",
    )
    bawue, notifications = config.section("bawue"), config.section("notifications")
    assert bawue["wahlperiode"] == 18
    assert bawue["wahlperiode-start-date"] == "2026-05-01"
    assert bawue["parlis-request-delay-s"] == 0.8
    assert notifications["mattermost-hook"] == "https://chat.example.org/hooks/from-env"
    # Untouched keys still come from the file.
    assert bawue["ics-url"] == "https://example.org/cal.ics"
    assert notifications["mattermost-username"] == "bawue-scraper"


def test_section_env_applies_without_a_file_section(load):
    assert load(LTZF_API_KEY="k", WAHLPERIODE="18").section("bawue") == {"wahlperiode": 18}


@pytest.mark.parametrize(
    ("raw", "expected"), [("false", False), ("False", False), ("0", False), ("true", True), ("1", True)]
)
def test_beteiligungsfristen_switch_from_env(load, raw, expected):
    """Issue #148 (DD-076): the deadlines can be switched off on the job, without a release."""
    assert load(LTZF_API_KEY="k", BETEILIGUNGSFRISTEN=raw).section("bawue")["beteiligungsfristen"] is expected


@pytest.mark.parametrize(("var", "raw"), [("WAHLPERIODE", "eighteen"), ("BETEILIGUNGSFRISTEN", "nein")])
def test_uncastable_section_env_fails_loudly(load, var, raw):
    config = load(LTZF_API_KEY="k", **{var: raw})
    with pytest.raises(ValueError):
        config.section("bawue")
