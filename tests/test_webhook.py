"""Тесты FastAPI-эндпоинта /webhook: подпись, события и запуск аудита."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from config import get_settings
from main import MAX_WEBHOOK_BODY_BYTES, app, verify_webhook_signature

WEBHOOK_SECRET = "test-webhook-secret-0123456789abcdef"
HEAD_SHA = "a" * 40


def _payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "repository": {"full_name": "acme/demo"},
        "pull_request": {
            "number": 7,
            "head": {"sha": HEAD_SHA},
            "base": {"sha": "b" * 40},
        },
    }


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _headers(body: bytes, event: str = "pull_request", signature: str | None = None):
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-test-1",
        "X-Hub-Signature-256": signature or _sign(body),
    }


@pytest.fixture()
def api_client(monkeypatch):
    """Поднять приложение с тестовыми env-переменными и закрыть после теста."""
    monkeypatch.setenv("GITHUB_TOKEN", "github-test-token")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("LLM_API_KEY", "llm-test-key")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.delenv("ALLOWED_REPOSITORIES", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_valid_opened_webhook_schedules_audit(api_client) -> None:
    body = json.dumps(_payload("opened")).encode()
    with patch("main.run_compliance_audit", new_callable=AsyncMock) as audit:
        response = api_client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert kwargs["repo_full_name"] == "acme/demo"
    assert kwargs["pull_number"] == 7
    assert kwargs["head_sha"] == HEAD_SHA
    assert kwargs["action"] == "opened"


def test_synchronize_webhook_schedules_audit(api_client) -> None:
    body = json.dumps(_payload("synchronize")).encode()
    with patch("main.run_compliance_audit", new_callable=AsyncMock) as audit:
        response = api_client.post("/webhook", content=body, headers=_headers(body))

    assert response.status_code == 202
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "synchronize"


def test_invalid_signature_rejected(api_client) -> None:
    body = json.dumps(_payload()).encode()
    headers = _headers(body)
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    with patch("main.run_compliance_audit", new_callable=AsyncMock) as audit:
        response = api_client.post("/webhook", content=body, headers=headers)

    assert response.status_code == 401
    audit.assert_not_awaited()


def test_missing_signature_rejected(api_client) -> None:
    body = json.dumps(_payload()).encode()
    headers = _headers(body)
    headers.pop("X-Hub-Signature-256")
    response = api_client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 401


def test_ping_is_acknowledged(api_client) -> None:
    body = b'{"zen": "Keep it logically awesome."}'
    response = api_client.post(
        "/webhook", content=body, headers=_headers(body, event="ping")
    )
    assert response.status_code == 202
    assert response.json()["status"] == "pong"


def test_unsupported_event_ignored(api_client) -> None:
    body = b'{"action": "created"}'
    with patch("main.run_compliance_audit", new_callable=AsyncMock) as audit:
        response = api_client.post(
            "/webhook", content=body, headers=_headers(body, event="issue_comment")
        )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    audit.assert_not_awaited()


def test_closed_action_ignored(api_client) -> None:
    body = json.dumps(_payload("closed")).encode()
    with patch("main.run_compliance_audit", new_callable=AsyncMock) as audit:
        response = api_client.post("/webhook", content=body, headers=_headers(body))
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    audit.assert_not_awaited()


def test_invalid_json_after_valid_signature_returns_400(api_client) -> None:
    body = b"{not valid json"
    response = api_client.post("/webhook", content=body, headers=_headers(body))
    assert response.status_code == 400


def test_malformed_pr_payload_returns_400(api_client) -> None:
    payload = {"action": "opened", "repository": {"full_name": "acme/demo"}}
    body = json.dumps(payload).encode()
    response = api_client.post("/webhook", content=body, headers=_headers(body))
    assert response.status_code == 400


def test_signature_helper_rejects_tampered_body() -> None:
    body = b'{"action":"opened"}'
    signature = _sign(body)
    assert verify_webhook_signature(body, signature, WEBHOOK_SECRET) is True
    assert verify_webhook_signature(body + b" ", signature, WEBHOOK_SECRET) is False
    assert verify_webhook_signature(body, "sha1=abc", WEBHOOK_SECRET) is False
    assert verify_webhook_signature(body, None, WEBHOOK_SECRET) is False
    # Не-ASCII/невалидный hex не должен приводить к исключению (500).
    assert (
        verify_webhook_signature(body, "sha256=нет-такой-подписи", WEBHOOK_SECRET)
        is False
    )
    assert verify_webhook_signature(body, "sha256=" + "z" * 64, WEBHOOK_SECRET) is False
    assert verify_webhook_signature(body, "sha256=" + "я" * 64, WEBHOOK_SECRET) is False


def test_oversized_body_is_rejected_before_reading(api_client) -> None:
    # Content-Length больше лимита: тело даже не должно буферизоваться.
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "Content-Length": str(MAX_WEBHOOK_BODY_BYTES + 1),
    }
    response = api_client.post("/webhook", headers=headers, content=b"{}")
    assert response.status_code == 413


def test_oversized_chunked_body_is_rejected(api_client) -> None:
    # Имитируем chunked-запрос без Content-Length: лимит должен работать
    # при потоковом чтении, а не только по заголовку.
    def body_chunks():
        chunk = b"x" * (MAX_WEBHOOK_BODY_BYTES // 2 + 1)
        yield chunk
        yield chunk

    response = api_client.post(
        "/webhook",
        headers={"Content-Type": "application/json", "X-GitHub-Event": "pull_request"},
        content=body_chunks(),
    )
    assert response.status_code == 413
