"""Friendly, actionable configuration errors."""

from __future__ import annotations

import pytest

from config import ConfigurationError, Settings


def _clear_required(monkeypatch) -> None:
    for name in ("GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_missing_github_token_explains_where_to_get_it(monkeypatch) -> None:
    _clear_required(monkeypatch)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "x" * 32)
    monkeypatch.setenv("LLM_API_KEY", "ollama")

    with pytest.raises(ConfigurationError) as error:
        Settings.from_env()

    message = str(error.value)
    assert "GITHUB_TOKEN" in message
    assert "github.com/settings/tokens" in message


def test_missing_llm_key_mentions_ollama_option(monkeypatch) -> None:
    _clear_required(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "github-test-token")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "x" * 32)

    with pytest.raises(ConfigurationError) as error:
        Settings.from_env()

    message = str(error.value)
    assert "LLM_API_KEY" in message
    assert "ollama" in message.lower()


def test_short_webhook_secret_explains_minimum(monkeypatch) -> None:
    _clear_required(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "github-test-token")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "short")
    monkeypatch.setenv("LLM_API_KEY", "ollama")

    with pytest.raises(ConfigurationError) as error:
        Settings.from_env()

    message = str(error.value)
    assert "GITHUB_WEBHOOK_SECRET" in message
    assert "16" in message
