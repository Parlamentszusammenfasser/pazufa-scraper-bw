"""Mattermost webhook notifications for scraper run summaries."""

import logging
import os
import re

import requests

from bawue.config import BawueConfig
from bawue.config_loader import load_toml_section

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"config\.(\w+)\.toml$")


def _extract_environment(config: BawueConfig) -> str:
    # Cloud deployments run the image's default config.toml, so the filename
    # carries no environment — they set ENVIRONMENT instead.
    env = os.getenv("ENVIRONMENT")
    if env:
        return env
    config_file = getattr(config, "config_file", None)
    if config_file:
        m = _ENV_PATTERN.search(str(config_file))
        if m:
            return m.group(1)
    return "local"


def send_run_report(config: BawueConfig, sections: list[tuple[str, list[str]]]) -> None:
    """Post one cycle's scraper summaries as a single Mattermost message (issue #52).

    *sections* are ``(title, lines)`` pairs, one per scraper. Silently skips if
    mattermost-hook is empty or missing, or there is nothing to report.
    """
    if not sections:
        return
    notif = load_toml_section(config, "notifications")
    hook = notif.get("mattermost-hook", "").strip()
    if not hook:
        return

    username = notif.get("mattermost-username", "bawue-scraper")
    environment = _extract_environment(config)

    text = f"**[{environment}] BaWue Run Summary**" + "".join(
        f"\n**{title}**\n```\n" + "\n".join(lines) + "\n```" for title, lines in sections
    )

    try:
        resp = requests.post(hook, json={"username": username, "text": text}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        # Only the type: requests puts the hook URL, i.e. the secret, into the message.
        logger.warning("Failed to send Mattermost notification: %s", type(e).__name__)
