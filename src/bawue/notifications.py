"""Mattermost webhook notifications for scraper run summaries."""

import logging
import re

import requests
from collector.config import CollectorConfiguration

from bawue.config_loader import load_toml_section

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"config\.(\w+)\.toml$")


def _extract_environment(config: CollectorConfiguration) -> str:
    config_file = getattr(config, "config_file", None)
    if config_file:
        m = _ENV_PATTERN.search(str(config_file))
        if m:
            return m.group(1)
    return "local"


def send_mattermost_summary(
    config: CollectorConfiguration,
    title: str,
    lines: list[str],
) -> None:
    """Post a run summary to the configured Mattermost channel.

    Silently skips if mattermost-hook is empty or missing.
    """
    notif = load_toml_section(config, "notifications")
    hook = notif.get("mattermost-hook", "").strip()
    if not hook:
        return

    username = notif.get("mattermost-username", "bawue-scraper")
    environment = _extract_environment(config)

    text = f"**[{environment}] {title}**\n```\n" + "\n".join(lines) + "\n```"

    try:
        resp = requests.post(hook, json={"username": username, "text": text}, timeout=10)
        resp.raise_for_status()
    except Exception:
        logger.warning("Failed to send Mattermost notification", exc_info=True)
