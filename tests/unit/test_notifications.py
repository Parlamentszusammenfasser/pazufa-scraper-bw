"""Tests for Mattermost notifications."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

from bawue.notifications import _extract_environment, send_mattermost_summary

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


class TestSendMattermostSummary:
    @responses_lib.activate
    def test_sends_post_to_hook(self, mock_config):
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif():
            send_mattermost_summary(mock_config, "Vorgänge Run", ["Published: 5", "Failed: 0"])
        assert len(responses_lib.calls) == 1
        assert responses_lib.calls[0].request.url == HOOK_URL

    @responses_lib.activate
    def test_skips_post_when_hook_empty(self, mock_config):
        with _patch_notif(hook=""):
            send_mattermost_summary(mock_config, "Vorgänge Run", ["Published: 5"])
        assert len(responses_lib.calls) == 0

    @responses_lib.activate
    def test_skips_post_when_hook_whitespace_only(self, mock_config):
        with _patch_notif(hook="   "):
            send_mattermost_summary(mock_config, "Vorgänge Run", ["Published: 5"])
        assert len(responses_lib.calls) == 0

    @responses_lib.activate
    def test_payload_includes_username(self, mock_config):
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif(username="my-bot"):
            send_mattermost_summary(mock_config, "Title", [])
        import json

        body = json.loads(responses_lib.calls[0].request.body)
        assert body["username"] == "my-bot"

    @responses_lib.activate
    def test_payload_text_includes_environment(self, mock_config):
        mock_config.config_file = "config.staging.toml"
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif():
            send_mattermost_summary(mock_config, "Vorgänge Run", ["Published: 5"])
        import json

        body = json.loads(responses_lib.calls[0].request.body)
        assert "staging" in body["text"]

    @responses_lib.activate
    def test_payload_text_includes_title(self, mock_config):
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif():
            send_mattermost_summary(mock_config, "Sitzungen Run Summary", ["some line"])
        import json

        body = json.loads(responses_lib.calls[0].request.body)
        assert "Sitzungen Run Summary" in body["text"]

    @responses_lib.activate
    def test_payload_text_includes_summary_lines(self, mock_config):
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        with _patch_notif():
            send_mattermost_summary(mock_config, "Run", ["Published: 42", "Failed: 1"])
        import json

        body = json.loads(responses_lib.calls[0].request.body)
        assert "Published: 42" in body["text"]
        assert "Failed: 1" in body["text"]

    @responses_lib.activate
    def test_does_not_raise_on_http_error(self, mock_config, caplog):
        responses_lib.add(responses_lib.POST, HOOK_URL, status=500)
        with _patch_notif(), caplog.at_level(logging.WARNING, logger="bawue.notifications"):
            send_mattermost_summary(mock_config, "Run", ["line"])
        assert "Failed to send Mattermost notification" in caplog.text

    @responses_lib.activate
    def test_does_not_raise_on_connection_error(self, mock_config, caplog):
        responses_lib.add(responses_lib.POST, HOOK_URL, body=ConnectionError("timeout"))
        with _patch_notif(), caplog.at_level(logging.WARNING, logger="bawue.notifications"):
            send_mattermost_summary(mock_config, "Run", ["line"])
        assert "Failed to send Mattermost notification" in caplog.text
