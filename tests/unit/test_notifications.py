"""Tests for Mattermost notifications."""

import logging
from unittest.mock import MagicMock

import pytest
import responses as responses_lib

from bawue.notifications import _extract_environment, send_alert, send_run_report

HOOK_URL = "https://chat.pazufa.de/hooks/testhook"


@pytest.fixture()
def mock_config():
    config = MagicMock()
    config.config_file = "config.staging.toml"
    config.section.return_value = {"mattermost-hook": HOOK_URL, "mattermost-username": "bawue-scraper"}
    return config


class TestExtractEnvironment:
    @pytest.mark.parametrize(
        ("config_file", "env", "environment"),
        [
            ("config.staging.toml", None, "staging"),
            ("config.production.toml", None, "production"),
            ("/app/config.staging.toml", None, "staging"),
            ("config.toml", None, "local"),
            (None, None, "local"),
            # Cloud deployments run the default config.toml, so the filename says nothing.
            ("config.toml", "staging", "staging"),
            ("config.production.toml", "", "production"),  # an empty ENVIRONMENT is ignored
        ],
    )
    def test_extract_environment(self, monkeypatch, config_file, env, environment):
        if env is not None:
            monkeypatch.setenv("ENVIRONMENT", env)
        config = MagicMock()
        config.config_file = config_file
        assert _extract_environment(config) == environment


class TestSendRunReport:
    @responses_lib.activate
    @pytest.mark.parametrize("hook", ["", "   "])
    def test_skips_post_without_hook(self, mock_config, hook):
        mock_config.section.return_value["mattermost-hook"] = hook
        send_run_report(mock_config, [("Vorgänge Run", ["Published: 5"])])
        assert len(responses_lib.calls) == 0

    @responses_lib.activate
    @pytest.mark.parametrize(
        "failure", [{"status": 500}, {"body": ConnectionError("timeout")}], ids=["http-error", "connection-error"]
    )
    def test_does_not_raise_on_failure(self, mock_config, caplog, failure):
        responses_lib.add(responses_lib.POST, HOOK_URL, **failure)
        with caplog.at_level(logging.WARNING, logger="bawue.notifications"):
            send_run_report(mock_config, [("Run", ["line"])])
        assert "Failed to send Mattermost notification" in caplog.text
        assert "testhook" not in caplog.text  # the hook URL is the secret

    @responses_lib.activate
    def test_issue52_one_post_for_all_sections(self, mock_config):
        """Issue #52: one message per cycle and environment, not one per scraper."""
        mock_config.config_file = "config.prod.toml"
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        mock_config.section.return_value["mattermost-username"] = "my-bot"
        send_run_report(
            mock_config,
            [("Vorgänge", ["Found: 3"]), ("Beteiligung", ["Found: 1"]), ("Sitzungen", ["Dates found: 50"])],
        )
        import json

        assert len(responses_lib.calls) == 1
        assert responses_lib.calls[0].request.url == HOOK_URL
        assert json.loads(responses_lib.calls[0].request.body)["username"] == "my-bot"
        # Environment once; each section keeps its own code block for column alignment.
        assert json.loads(responses_lib.calls[0].request.body)["text"] == (
            "**[prod] BaWue Run Summary**"
            "\n**Vorgänge**\n```\nFound: 3\n```"
            "\n**Beteiligung**\n```\nFound: 1\n```"
            "\n**Sitzungen**\n```\nDates found: 50\n```"
        )

    @responses_lib.activate
    def test_issue128_plain_section_keeps_links_clickable(self, mock_config):
        """Issue #128: links inside a code block are not clickable, so this section has none."""
        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        send_run_report(
            mock_config, [("Vorgänge", ["Found: 3"])], plain_sections=[("Fehlende Dokumente", ["- [a](b)"])]
        )
        import json

        assert json.loads(responses_lib.calls[0].request.body)["text"] == (
            "**[staging] BaWue Run Summary**\n**Vorgänge**\n```\nFound: 3\n```\n**Fehlende Dokumente**\n- [a](b)"
        )

    @responses_lib.activate
    def test_issue52_no_post_without_sections(self, mock_config):
        """No scraper enabled or none reported: nothing to say."""
        send_run_report(mock_config, [])
        assert len(responses_lib.calls) == 0


class TestSendAlert:
    @responses_lib.activate
    def test_posts_alert_with_environment(self, mock_config):
        import json

        responses_lib.add(responses_lib.POST, HOOK_URL, json={"ok": True}, status=200)
        send_alert(mock_config, "Redis unreachable")
        payload = json.loads(responses_lib.calls[0].request.body)
        assert "[staging]" in payload["text"]
        assert "Redis unreachable" in payload["text"]

    @responses_lib.activate
    def test_skips_post_when_hook_empty(self, mock_config):
        mock_config.section.return_value["mattermost-hook"] = ""
        send_alert(mock_config, "Redis unreachable")
        assert len(responses_lib.calls) == 0
