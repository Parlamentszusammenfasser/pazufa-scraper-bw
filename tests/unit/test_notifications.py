"""Tests for Mattermost notifications."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

from bawue.notifications import _extract_environment, send_run_report

HOOK_URL = "https://chat.pazufa.de/hooks/testhook"


def _make_config(hook: str = HOOK_URL, username: str = "bawue-scraper", config_file: str = "config.staging.toml"):
    config = MagicMock()
    config.config_file = config_file
    notif_section = {"mattermost-hook": hook, "mattermost-username": username}
    with patch("bawue.notifications.load_toml_section", return_value=notif_section):
        yield config, notif_section


@pytest.fixture()
def mock_config():
    config = MagicMock()
    config.config_file = "config.staging.toml"
    return config


def _patch_notif(hook: str = HOOK_URL, username: str = "bawue-scraper"):
    return patch(
        "bawue.notifications.load_toml_section",
        return_value={"mattermost-hook": hook, "mattermost-username": username},
    )


class TestExtractEnvironment:
    def test_staging_config(self):
        config = MagicMock()
        config.config_file = "config.staging.toml"
        assert _extract_environment(config) == "staging"

    def test_production_config(self):
        config = MagicMock()
        config.config_file = "config.production.toml"
        assert _extract_environment(config) == "production"

    def test_plain_config_falls_back_to_local(self):
        config = MagicMock()
        config.config_file = "config.toml"
        assert _extract_environment(config) == "local"

    def test_absolute_path(self):
        config = MagicMock()
        config.config_file = "/app/config.staging.toml"
        assert _extract_environment(config) == "staging"

    def test_no_config_file_falls_back_to_local(self):
        config = MagicMock()
        config.config_file = None
        assert _extract_environment(config) == "local"

    def test_environment_env_var_wins(self, monkeypatch):
        """Cloud deployments run the default config.toml, so the filename says nothing."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        config = MagicMock()
        config.config_file = "config.toml"
        assert _extract_environment(config) == "staging"

    def test_empty_environment_env_var_is_ignored(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "")
        config = MagicMock()
        config.config_file = "config.production.toml"
        assert _extract_environment(config) == "production"


class TestSendRunReport:
    @responses_lib.activate
    def test_sends_post_to_hook(self, mock_config):
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif():
            send_run_report(mock_config, [("Vorgänge Run", ["Published: 5", "Failed: 0"])])
        assert len(responses_lib.calls) == 1
        assert responses_lib.calls[0].request.url == HOOK_URL

    @responses_lib.activate
    def test_skips_post_when_hook_empty(self, mock_config):
        with _patch_notif(hook=""):
            send_run_report(mock_config, [("Vorgänge Run", ["Published: 5"])])
        assert len(responses_lib.calls) == 0

    @responses_lib.activate
    def test_skips_post_when_hook_whitespace_only(self, mock_config):
        with _patch_notif(hook="   "):
            send_run_report(mock_config, [("Vorgänge Run", ["Published: 5"])])
        assert len(responses_lib.calls) == 0

    @responses_lib.activate
    def test_payload_includes_username(self, mock_config):
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif(username="my-bot"):
            send_run_report(mock_config, [("Title", [])])
        import json

        body = json.loads(responses_lib.calls[0].request.body)
        assert body["username"] == "my-bot"

    @responses_lib.activate
    def test_does_not_raise_on_http_error(self, mock_config, caplog):
        responses_lib.add(responses_lib.POST, HOOK_URL, status=500)
        with _patch_notif(), caplog.at_level(logging.WARNING, logger="bawue.notifications"):
            send_run_report(mock_config, [("Run", ["line"])])
        assert "Failed to send Mattermost notification" in caplog.text
        assert "testhook" not in caplog.text  # the hook URL is the secret

    @responses_lib.activate
    def test_does_not_raise_on_connection_error(self, mock_config, caplog):
        responses_lib.add(responses_lib.POST, HOOK_URL, body=ConnectionError("timeout"))
        with _patch_notif(), caplog.at_level(logging.WARNING, logger="bawue.notifications"):
            send_run_report(mock_config, [("Run", ["line"])])
        assert "Failed to send Mattermost notification" in caplog.text
        assert "testhook" not in caplog.text  # the hook URL is the secret

    @responses_lib.activate
    def test_issue52_one_post_for_all_sections(self, mock_config):
        """Issue #52: one message per cycle and environment, not one per scraper."""
        mock_config.config_file = "config.prod.toml"
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif():
            send_run_report(
                mock_config,
                [("Vorgänge", ["Found: 3"]), ("Beteiligung", ["Found: 1"]), ("Sitzungen", ["Dates found: 50"])],
            )
        import json

        assert len(responses_lib.calls) == 1
        # Environment once; each section keeps its own code block for column alignment.
        assert json.loads(responses_lib.calls[0].request.body)["text"] == (
            "**[prod] BaWue Run Summary**"
            "\n**Vorgänge**\n```\nFound: 3\n```"
            "\n**Beteiligung**\n```\nFound: 1\n```"
            "\n**Sitzungen**\n```\nDates found: 50\n```"
        )

    @responses_lib.activate
    def test_issue52_no_post_without_sections(self, mock_config):
        """No scraper enabled or none reported: nothing to say."""
        with _patch_notif():
            send_run_report(mock_config, [])
        assert len(responses_lib.calls) == 0
