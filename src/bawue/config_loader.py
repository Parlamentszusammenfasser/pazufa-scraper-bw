"""Shared TOML config section loader for all BaWue scrapers."""

import logging
import os
from collections.abc import Callable
from typing import Any

import toml

from bawue.config import BawueConfig

logger = logging.getLogger(__name__)

# Section keys that differ per environment. Unlike the [main]/[cache]/[backend]
# options these are read straight off disk, so without an env override a cloud
# deployment would need its own TOML baked into the image. Env wins over the
# file, matching BawueConfig's precedence (default < file < env < CLI).
_ENV_OVERRIDES: dict[str, dict[str, tuple[str, Callable[[str], Any]]]] = {
    "bawue": {
        "wahlperiode": ("WAHLPERIODE", int),
        "wahlperiode-start-date": ("WAHLPERIODE_START_DATE", str),
        "parlis-request-delay-s": ("PARLIS_REQUEST_DELAY_S", float),
    },
    "beteiligung": {
        "wahlperiode": ("BETEILIGUNG_WAHLPERIODE", int),
    },
    "notifications": {
        "mattermost-hook": ("MATTERMOST_HOOK", str),
    },
}


def load_toml_section(config: BawueConfig, section: str) -> dict:
    """Load a named section from the BaWue config's TOML file.

    Returns the section dict, or {} if the file is missing or the section absent.
    Keys in _ENV_OVERRIDES are replaced by their environment variable when set.
    """
    values: dict = {}
    config_file = getattr(config, "config_file", None)
    if config_file:
        try:
            values = dict(toml.load(config_file).get(section, {}))
        except Exception:
            logger.warning(
                "Could not load [%s] section from config file: %s",
                section,
                config_file,
                exc_info=True,
            )

    for key, (env_name, cast) in _ENV_OVERRIDES.get(section, {}).items():
        raw = os.getenv(env_name)
        if not raw:
            continue
        try:
            values[key] = cast(raw)
        except ValueError:
            logger.critical("%s=%r is not a valid %s", env_name, raw, cast.__name__)
            raise
    return values
