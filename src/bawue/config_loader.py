"""Shared TOML config section loader for all BaWue scrapers."""

import logging

import toml

from bawue.config import BawueConfig

logger = logging.getLogger(__name__)


def load_toml_section(config: BawueConfig, section: str) -> dict:
    """Load a named section from the BaWue config's TOML file.

    Returns the section dict, or {} if the file is missing or the section absent.
    """
    config_file = getattr(config, "config_file", None)
    if config_file:
        try:
            loaded = toml.load(config_file)
            return loaded.get(section, {})
        except Exception:
            logger.warning(
                "Could not load [%s] section from config file: %s",
                section,
                config_file,
                exc_info=True,
            )
    return {}
