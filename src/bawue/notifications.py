"""Mattermost webhook notifications for scraper run summaries."""

import logging
import os
import re

import requests

from bawue.config import BawueConfig

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"config\.(\w+)\.toml$")


def extract_environment(config: BawueConfig) -> str:
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
    """Post one cycle's errors as a single Mattermost message (issue #52); numbers go to Grafana (DD-082).

    *sections* are ``(title, lines)`` pairs, one per scraper with errors, each in a code block.
    Silently skips if mattermost-hook is empty or missing, or there is nothing to report.
    """
    if not sections:
        return
    text = f"**[{extract_environment(config)}] BaWue run errors**" + "".join(
        f"\n**{title}**\n```\n" + "\n".join(lines) + "\n```" for title, lines in sections
    )
    _post(config, text)


def send_alert(config: BawueConfig, message: str) -> None:
    """Post a run-aborting error to Mattermost. Silently skips without a hook."""
    _post(config, f"**[{extract_environment(config)}] BaWue run aborted**\n{message}")


def _post(config: BawueConfig, text: str) -> None:
    notif = config.section("notifications")
    hook = notif.get("mattermost-hook", "").strip()
    if not hook:
        return
    username = notif.get("mattermost-username", "bawue-scraper")
    try:
        resp = requests.post(hook, json={"username": username, "text": text}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        # Only the type: requests puts the hook URL, i.e. the secret, into the message.
        logger.warning("Failed to send Mattermost notification: %s", type(e).__name__)
