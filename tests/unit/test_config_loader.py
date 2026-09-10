"""Env-var overrides for the TOML sections read straight off disk.

These are what let a cloud deployment run the image's default config.toml
instead of baking a per-environment TOML into the image.
"""

from unittest.mock import MagicMock

import pytest

from bawue.config_loader import load_toml_section

SECTIONS = """
[bawue]
wahlperiode = 17
wahlperiode-start-date = "2021-04-26"
parlis-request-delay-s = 1.0
ics-url = "https://example.org/cal.ics"

[beteiligung]
wahlperiode = 17

[notifications]
mattermost-hook = "https://chat.example.org/hooks/from-file"
mattermost-username = "bawue-scraper"
"""


@pytest.fixture()
def config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(SECTIONS)
    cfg = MagicMock()
    cfg.config_file = str(path)
    return cfg


def test_file_values_used_when_no_env(config):
    section = load_toml_section(config, "bawue")
    assert section["wahlperiode"] == 17
    assert section["parlis-request-delay-s"] == 1.0


def test_env_overrides_file_and_keeps_types(config, monkeypatch):
    monkeypatch.setenv("WAHLPERIODE", "18")
    monkeypatch.setenv("WAHLPERIODE_START_DATE", "2026-05-01")
    monkeypatch.setenv("PARLIS_REQUEST_DELAY_S", "0.8")

    section = load_toml_section(config, "bawue")

    assert section["wahlperiode"] == 18
    assert section["wahlperiode-start-date"] == "2026-05-01"
    assert section["parlis-request-delay-s"] == 0.8
    # Untouched keys still come from the file.
    assert section["ics-url"] == "https://example.org/cal.ics"


def test_beteiligung_wahlperiode_is_a_separate_knob(config, monkeypatch):
    monkeypatch.setenv("WAHLPERIODE", "18")
    assert load_toml_section(config, "beteiligung")["wahlperiode"] == 17

    monkeypatch.setenv("BETEILIGUNG_WAHLPERIODE", "18")
    assert load_toml_section(config, "beteiligung")["wahlperiode"] == 18


def test_mattermost_hook_from_env(config, monkeypatch):
    monkeypatch.setenv("MATTERMOST_HOOK", "https://chat.example.org/hooks/from-env")
    section = load_toml_section(config, "notifications")
    assert section["mattermost-hook"] == "https://chat.example.org/hooks/from-env"
    assert section["mattermost-username"] == "bawue-scraper"


def test_empty_env_does_not_override(config, monkeypatch):
    """Cloud Run passes unset variables as empty strings — those must be ignored."""
    monkeypatch.setenv("WAHLPERIODE", "")
    assert load_toml_section(config, "bawue")["wahlperiode"] == 17


def test_env_applies_without_a_config_file(monkeypatch):
    monkeypatch.setenv("WAHLPERIODE", "18")
    cfg = MagicMock()
    cfg.config_file = None
    assert load_toml_section(cfg, "bawue") == {"wahlperiode": 18}


def test_uncastable_env_fails_loudly(config, monkeypatch):
    monkeypatch.setenv("WAHLPERIODE", "eighteen")
    with pytest.raises(ValueError):
        load_toml_section(config, "bawue")


def test_missing_section_still_gets_env_overrides(config, monkeypatch):
    monkeypatch.setenv("MATTERMOST_HOOK", "https://chat.example.org/hooks/from-env")
    assert load_toml_section(config, "gesetzblatt") == {}
