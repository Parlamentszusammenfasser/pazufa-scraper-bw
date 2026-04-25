"""Tests for Ollama / local LLM provider support.

Verifies that scrapers correctly enable LLM when a base URL is configured
(e.g. for local Ollama) even without an API key, and that the api_base
parameter is passed through to the LLMConnector and litellm calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bawue.bawue_beteiligung_scraper import BawueBeteiligungScraper
from bawue.bawue_vorgaenge_scraper import BawueVorgaengeScraper


def _make_mock_config(
    *,
    llm_provider_key=None,
    llm_provider_base_url=None,
    llm_model="ollama/gemma4:e4b",
    config_file=None,
):
    """Create a mock CollectorConfiguration with LLM settings."""
    config = MagicMock()
    config.config_file = config_file
    config.collector_id = "00000000-0000-0000-0000-000000000001"
    config.llm_provider_key = llm_provider_key
    config.llm_provider_base_url = llm_provider_base_url
    config.llm_model = llm_model
    return config


class TestVorgaengeScraperOllamaInit:
    """LLM init in BawueVorgaengeScraper with Ollama config."""

    def test_llm_enabled_with_base_url_only(self):
        """LLM should be enabled when base URL is set, even without API key."""
        config = _make_mock_config(llm_provider_base_url="http://localhost:11434")

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.__init__", return_value=None),
            patch("corelib.llm.LLMConnector") as mock_llm_cls,
        ):
            scraper = BawueVorgaengeScraper(config, MagicMock())

        assert scraper._llm_enabled is True
        mock_llm_cls.assert_called_once_with(
            model="ollama/gemma4:e4b",
            api_key=None,
            api_base="http://localhost:11434",
            rate_limit_max_calls=5,
            rate_limit_window_seconds=60,
        )

    def test_llm_enabled_with_api_key_only(self):
        """LLM should still work with just an API key (OpenAI-style)."""
        config = _make_mock_config(
            llm_provider_key="sk-test",
            llm_model="gpt-5-nano",
        )

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.__init__", return_value=None),
            patch("corelib.llm.LLMConnector") as mock_llm_cls,
        ):
            scraper = BawueVorgaengeScraper(config, MagicMock())

        assert scraper._llm_enabled is True
        mock_llm_cls.assert_called_once_with(
            model="gpt-5-nano",
            api_key="sk-test",
            api_base=None,
            rate_limit_max_calls=5,
            rate_limit_window_seconds=60,
        )

    def test_llm_disabled_without_key_or_base_url(self):
        """LLM should be disabled when neither key nor base URL is set."""
        config = _make_mock_config()

        with patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.__init__", return_value=None):
            scraper = BawueVorgaengeScraper(config, MagicMock())

        assert scraper._llm_enabled is False
        assert scraper._llm is None

    def test_llm_enabled_with_both_key_and_base_url(self):
        """LLM should pass both key and base URL when both are set."""
        config = _make_mock_config(
            llm_provider_key="sk-test",
            llm_provider_base_url="http://localhost:11434",
        )

        with (
            patch("bawue.bawue_vorgaenge_scraper.VorgangsScraper.__init__", return_value=None),
            patch("corelib.llm.LLMConnector") as mock_llm_cls,
        ):
            scraper = BawueVorgaengeScraper(config, MagicMock())

        assert scraper._llm_enabled is True
        mock_llm_cls.assert_called_once_with(
            model="ollama/gemma4:e4b",
            api_key="sk-test",
            api_base="http://localhost:11434",
            rate_limit_max_calls=5,
            rate_limit_window_seconds=60,
        )


class TestBeteiligungScraperOllamaInit:
    """LLM init in BawueBeteiligungScraper with Ollama config."""

    def test_llm_enabled_with_base_url_only(self):
        """LLM should be enabled when base URL is set, even without API key."""
        config = _make_mock_config(llm_provider_base_url="http://localhost:11434")

        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.__init__", return_value=None),
            patch("bawue.bawue_beteiligung_scraper.BeteiligungClient"),
            patch("corelib.llm.LLMConnector") as mock_llm_cls,
        ):
            scraper = BawueBeteiligungScraper(config, MagicMock())

        assert scraper._llm_enabled is True
        mock_llm_cls.assert_called_once_with(
            model="ollama/gemma4:e4b",
            api_key=None,
            api_base="http://localhost:11434",
            rate_limit_max_calls=5,
            rate_limit_window_seconds=60,
        )

    def test_llm_disabled_without_key_or_base_url(self):
        """LLM should be disabled when neither key nor base URL is set."""
        config = _make_mock_config()

        with (
            patch("bawue.bawue_beteiligung_scraper.VorgangsScraper.__init__", return_value=None),
            patch("bawue.bawue_beteiligung_scraper.BeteiligungClient"),
        ):
            scraper = BawueBeteiligungScraper(config, MagicMock())

        assert scraper._llm_enabled is False
        assert scraper._llm is None


class TestBawueDokApiBase:
    """Verify extract_semantics passes api_base to litellm."""

    @pytest.mark.asyncio
    async def test_extract_semantics_passes_api_base(self):
        """api_base from LLMConnector should be forwarded to litellm.acompletion."""
        from bawue.bawue_dok import extract_semantics

        mock_llm = MagicMock()
        mock_llm.api_key = None
        mock_llm.api_base = "http://localhost:11434"
        mock_llm.temperature = 0.1
        mock_llm.timeout_seconds = 60.0

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"zusammenfassung": "test", "schlagworte": ["a"]}'))
        ]

        with patch("bawue.bawue_dok.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            mock_litellm.token_counter = MagicMock(return_value=100)

            await extract_semantics(
                mock_llm,
                "Ein langer Text ueber ein Gesetz zur Aenderung des Schulgesetzes.",
                MagicMock(),  # doktyp
                model="ollama/gemma4:e4b",
                max_tokens=12000,
            )

        call_kwargs = mock_litellm.acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "http://localhost:11434"
        assert call_kwargs["api_key"] is None
        assert call_kwargs["model"] == "ollama/gemma4:e4b"

    @pytest.mark.asyncio
    async def test_extract_semantics_api_base_none_for_openai(self):
        """api_base should be None when using standard OpenAI provider."""
        from bawue.bawue_dok import extract_semantics

        mock_llm = MagicMock()
        mock_llm.api_key = "sk-test"
        mock_llm.api_base = None
        mock_llm.temperature = 0.1
        mock_llm.timeout_seconds = 60.0

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"zusammenfassung": "test", "schlagworte": ["a"]}'))
        ]

        with patch("bawue.bawue_dok.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)
            mock_litellm.token_counter = MagicMock(return_value=100)

            await extract_semantics(
                mock_llm,
                "Ein langer Text ueber ein Gesetz.",
                MagicMock(),
                model="gpt-5-nano",
                max_tokens=12000,
            )

        call_kwargs = mock_litellm.acompletion.call_args.kwargs
        assert call_kwargs["api_base"] is None
        assert call_kwargs["api_key"] == "sk-test"
