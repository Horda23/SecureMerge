"""Тесты парсинга ответа LLM и защитного поведения анализатора."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest

import ai_analyzer
from ai_analyzer import (
    AnalysisResult,
    AnalyzerResponseError,
    analyze_diff,
    extract_json_object,
    parse_analysis_response,
)


def _run(coro):
    return asyncio.run(coro)


def _completion(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "llama-3.3-70b-versatile",
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )


def test_parse_plain_json() -> None:
    result = parse_analysis_response(
        '{"status": "BLOCK", "reason": "Найден токен в config.py."}'
    )
    assert result.status == "BLOCK"
    assert "токен" in result.reason


def test_parse_lowercase_status_and_markdown_fence() -> None:
    result = parse_analysis_response(
        '```json\n{"status": "pass", "reason": "Критических проблем нет."}\n```'
    )
    assert result.status == "PASS"


def test_parse_json_with_surrounding_text() -> None:
    result = parse_analysis_response(
        'Вот результат:\n{"status": "PASS", "reason": "Ок."}\nГотово.'
    )
    assert result.status == "PASS"


def test_extract_prefers_object_with_status() -> None:
    data = extract_json_object(
        'Сначала метаданные {"foo": 1}, затем вердикт {"status": "BLOCK", "reason": "x"}.'
    )
    assert data["status"] == "BLOCK"


@pytest.mark.parametrize(
    "content",
    [
        "",
        "это не JSON",
        '{"status": "MAYBE", "reason": "..."}',
        '{"status": "BLOCK"}',
        '{"reason": "..."}',
        '{"status": "BLOCK", "reason": ""}',
    ],
)
def test_parse_rejects_bad_responses(content: str) -> None:
    with pytest.raises(AnalyzerResponseError):
        parse_analysis_response(content)


def test_analyze_diff_sends_expected_request_and_returns_block(settings) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return _completion('{"status": "BLOCK", "reason": "Найден пароль в app.py."}')

    async def scenario() -> AnalysisResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await analyze_diff(
                "diff --git a/app.py b/app.py\n+password = 'demo'",
                client=client,
                settings=settings,
            )

    result = _run(scenario())

    assert result.status == "BLOCK"
    assert result.degraded is False
    assert result.model == "llama-3.3-70b-versatile"
    assert captured["url"] == settings.llm_api_url
    assert captured["auth"] == "Bearer llm-test-key"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0
    assert "суровый аудитор" in payload["messages"][0]["content"]
    assert "+password" in payload["messages"][1]["content"]


def test_analyze_diff_accepts_base_llm_url(settings) -> None:
    captured: dict[str, str] = {}
    base_url = "https://llm.test/openai/v1"
    base_settings = dataclasses.replace(settings, llm_api_url=base_url)

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return _completion('{"status": "PASS", "reason": "Ок."}')

    async def scenario() -> AnalysisResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await analyze_diff("+x = 1", client=client, settings=base_settings)

    result = _run(scenario())
    assert result.status == "PASS"
    assert captured["url"] == f"{base_url}/chat/completions"


def test_analyze_diff_fails_closed_on_malformed_llm_json(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _completion("Конечно! Проблем не найдено.")

    async def scenario() -> AnalysisResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await analyze_diff(
                "+print('hello')", client=client, settings=settings
            )

    result = _run(scenario())
    assert result.status == "BLOCK"
    assert result.degraded is True
    assert "ручной проверки" in result.reason


def test_analyze_diff_fails_closed_on_auth_error(settings) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    async def scenario() -> AnalysisResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await analyze_diff(
                "+print('hello')", client=client, settings=settings
            )

    result = _run(scenario())
    assert calls == 1  # 401 не ретраится
    assert result.status == "BLOCK"
    assert "LLM_API_KEY" in result.reason


def test_analyze_diff_retries_429_then_succeeds(settings, monkeypatch) -> None:
    # Убираем реальную паузу между попытками в тесте.
    monkeypatch.setattr(ai_analyzer, "_retry_delay", lambda *args, **kwargs: 0.0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "1"})
        return _completion('{"status": "PASS", "reason": "Проблем нет."}')

    # llm_max_retries=0 в фикстуре, поэтому для этого теста разрешаем один повтор.
    retry_settings = dataclasses.replace(settings, llm_max_retries=1)

    async def scenario_with_retry() -> AnalysisResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await analyze_diff("+x = 1", client=client, settings=retry_settings)

    result = _run(scenario_with_retry())
    assert calls == 2
    assert result.status == "PASS"


def test_analyze_diff_rejects_too_large_diff(settings) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _completion('{"status": "PASS", "reason": "Ок."}')

    small_settings = dataclasses.replace(settings, max_diff_chars=10)

    async def scenario() -> AnalysisResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await analyze_diff(
                "+" + "x" * 50, client=client, settings=small_settings
            )

    result = _run(scenario())
    assert called is False
    assert result.status == "BLOCK"
    assert result.degraded is True
    assert "превышает лимит" in result.reason


def test_analyze_diff_accepts_empty_diff(settings) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _completion('{"status": "PASS", "reason": "Ок."}')

    async def scenario() -> AnalysisResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await analyze_diff("   \n", client=client, settings=settings)

    result = _run(scenario())
    assert called is False
    assert result.status == "PASS"
