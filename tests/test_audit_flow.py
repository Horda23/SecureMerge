"""Интеграционные тесты полного аудита с мок-сервером GitHub/Groq.

Сеть не используется: httpx.MockTransport подменяет и GitHub REST API,
и OpenAI-совместимый эндпоинт Groq.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from main import run_compliance_audit

HEAD_SHA = "a" * 40
REPO = "acme/demo"
PR_NUMBER = 7


def _run(coro):
    return asyncio.run(coro)


def _diff() -> bytes:
    return (
        b"diff --git a/app.py b/app.py\n"
        b"index 1111111..2222222 100644\n"
        b"--- a/app.py\n"
        b"+++ b/app.py\n"
        b"@@ -1,2 +1,3 @@\n"
        b" print('hello')\n"
        b"+password = 'demo-secret'\n"
    )


def _groq_response(status: str, reason: str) -> httpx.Response:
    content = json.dumps({"status": status, "reason": reason}, ensure_ascii=False)
    return httpx.Response(
        200,
        json={
            "model": "llama-test",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )


def test_block_flow_sets_failure_and_comments(settings) -> None:
    captured: dict[str, list] = {"comments": [], "statuses": [], "order": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.method == "GET"
            and request.url.path == f"/repos/{REPO}/pulls/{PR_NUMBER}"
        ):
            assert request.headers["accept"] == "application/vnd.github.v3.diff"
            return httpx.Response(200, content=_diff())
        if request.method == "POST" and str(request.url) == settings.groq_api_url:
            return _groq_response("BLOCK", "Найден захардкоженный пароль в app.py.")
        if (
            request.method == "POST"
            and request.url.path == f"/repos/{REPO}/issues/{PR_NUMBER}/comments"
        ):
            captured["comments"].append(json.loads(request.content)["body"])
            captured["order"].append("comment")
            return httpx.Response(201, json={"id": 1})
        if (
            request.method == "POST"
            and request.url.path == f"/repos/{REPO}/statuses/{HEAD_SHA}"
        ):
            captured["statuses"].append(json.loads(request.content))
            captured["order"].append("status")
            return httpx.Response(201, json={})
        raise AssertionError(f"Неожиданный запрос: {request.method} {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await run_compliance_audit(
                client,
                settings,
                asyncio.Semaphore(1),
                repo_full_name=REPO,
                pull_number=PR_NUMBER,
                head_sha=HEAD_SHA,
                action="opened",
                delivery_id="test-delivery",
            )

    _run(scenario())

    assert captured["order"] == ["status", "comment"]
    status_body = captured["statuses"][0]
    assert status_body["state"] == "failure"
    assert status_body["context"] == "Compliance Sentinel"
    assert status_body["target_url"] == "https://sentinel.test"
    # Reason LLM не публикуется в commit status (только в PR-комментарии).
    assert "Найден" not in status_body["description"]
    assert len(captured["comments"]) == 1
    comment = captured["comments"][0]
    assert "Compliance Sentinel" in comment
    assert "Найден захардкоженный пароль в app.py." in comment


def test_pass_flow_sets_success_without_comment(settings) -> None:
    captured: dict[str, list] = {"comments": [], "statuses": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.method == "GET"
            and request.url.path == f"/repos/{REPO}/pulls/{PR_NUMBER}"
        ):
            return httpx.Response(200, content=_diff())
        if request.method == "POST" and str(request.url) == settings.groq_api_url:
            return _groq_response("PASS", "Критических проблем не найдено.")
        if request.method == "POST" and request.url.path.startswith(
            f"/repos/{REPO}/statuses/"
        ):
            captured["statuses"].append(json.loads(request.content))
            return httpx.Response(201, json={})
        if request.method == "POST" and request.url.path.endswith("/comments"):
            captured["comments"].append(json.loads(request.content))
            return httpx.Response(201, json={})
        raise AssertionError(f"Неожиданный запрос: {request.method} {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await run_compliance_audit(
                client,
                settings,
                asyncio.Semaphore(1),
                repo_full_name=REPO,
                pull_number=PR_NUMBER,
                head_sha=HEAD_SHA,
                action="synchronize",
                delivery_id="test-delivery-pass",
            )

    _run(scenario())

    assert captured["comments"] == []
    assert len(captured["statuses"]) == 1
    assert captured["statuses"][0]["state"] == "success"
    assert "PASS" in captured["statuses"][0]["description"]


def test_github_diff_failure_fails_closed(settings) -> None:
    captured: dict[str, list] = {"comments": [], "statuses": [], "groq_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.method == "GET"
            and request.url.path == f"/repos/{REPO}/pulls/{PR_NUMBER}"
        ):
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and str(request.url) == settings.groq_api_url:
            captured["groq_calls"] += 1
            return _groq_response("PASS", "Не должно вызываться.")
        if request.method == "POST" and request.url.path.endswith("/comments"):
            captured["comments"].append(json.loads(request.content)["body"])
            return httpx.Response(201, json={})
        if request.method == "POST" and request.url.path.startswith(
            f"/repos/{REPO}/statuses/"
        ):
            captured["statuses"].append(json.loads(request.content))
            return httpx.Response(201, json={})
        raise AssertionError(f"Неожиданный запрос: {request.method} {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await run_compliance_audit(
                client,
                settings,
                asyncio.Semaphore(1),
                repo_full_name=REPO,
                pull_number=PR_NUMBER,
                head_sha=HEAD_SHA,
                action="opened",
                delivery_id="test-delivery-error",
            )

    _run(scenario())

    assert captured["groq_calls"] == 0
    assert captured["statuses"][0]["state"] == "failure"
    assert captured["comments"]
    assert "Не удалось получить diff" in captured["comments"][0]


def test_200_non_diff_body_fails_closed(settings) -> None:
    captured: dict[str, list] = {"comments": [], "statuses": [], "groq_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.method == "GET"
            and request.url.path == f"/repos/{REPO}/pulls/{PR_NUMBER}"
        ):
            # Аномальный 200: Content-Type diff, но тело — не unified diff.
            return httpx.Response(
                200,
                content=b'{"message": "unexpected"}',
                headers={"content-type": "application/vnd.github.v3.diff"},
            )
        if request.method == "POST" and str(request.url) == settings.groq_api_url:
            captured["groq_calls"] += 1
            return _groq_response("PASS", "Не должно вызываться.")
        if request.method == "POST" and request.url.path.endswith("/comments"):
            captured["comments"].append(json.loads(request.content)["body"])
            return httpx.Response(201, json={})
        if request.method == "POST" and request.url.path.startswith(
            f"/repos/{REPO}/statuses/"
        ):
            captured["statuses"].append(json.loads(request.content))
            return httpx.Response(201, json={})
        raise AssertionError(f"Неожиданный запрос: {request.method} {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await run_compliance_audit(
                client,
                settings,
                asyncio.Semaphore(1),
                repo_full_name=REPO,
                pull_number=PR_NUMBER,
                head_sha=HEAD_SHA,
                action="opened",
                delivery_id="test-delivery-non-diff",
            )

    _run(scenario())

    assert captured["groq_calls"] == 0
    assert captured["statuses"][0]["state"] == "failure"
    assert captured["comments"]
    assert "Не удалось получить diff" in captured["comments"][0]
    assert "HTTP 200" in captured["comments"][0]
