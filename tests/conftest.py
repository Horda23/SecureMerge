"""Общие фикстуры тестов Compliance Sentinel."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings


@pytest.fixture()
def settings() -> Settings:
    """Тестовые настройки без реальных секретов и сетевых вызовов."""
    return Settings(
        github_token="github-test-token",
        github_webhook_secret="webhook-test-secret-0123456789abcdef",
        groq_api_key="groq-test-key",
        github_api_url="https://api.github.test",
        groq_api_url="https://groq.test/openai/v1/chat/completions",
        groq_model="llama-test",
        status_context="Compliance Sentinel",
        max_diff_chars=10_000,
        http_timeout_seconds=5.0,
        groq_timeout_seconds=5.0,
        groq_max_retries=0,
        max_concurrent_audits=2,
        public_base_url="https://sentinel.test",
        log_level="DEBUG",
    )
