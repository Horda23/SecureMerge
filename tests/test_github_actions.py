"""Тесты публикации комментария и commit status через GitHub API."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from github_actions import post_pr_comment, set_commit_status
from github_api import GitHubAPIError


def _run(coro):
    return asyncio.run(coro)


def test_post_pr_comment_uses_issue_comments_endpoint(settings) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 1})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await post_pr_comment(
                client, settings, "acme/demo", 42, "## Проверка не пройдена"
            )

    _run(scenario())

    assert captured["method"] == "POST"
    assert captured["path"] == "/repos/acme/demo/issues/42/comments"
    assert captured["authorization"] == "Bearer github-test-token"
    assert captured["body"] == {"body": "## Проверка не пройдена"}


def test_set_commit_status_builds_payload(settings) -> None:
    sha = "a" * 40
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"state": "failure"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await set_commit_status(
                client,
                settings,
                "acme/demo",
                sha,
                "failure",
                "BLOCK — найден секрет",
                target_url="https://sentinel.test",
            )

    _run(scenario())

    assert captured["path"] == f"/repos/acme/demo/statuses/{sha}"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["state"] == "failure"
    assert body["context"] == "Compliance Sentinel"
    assert body["description"] == "BLOCK — найден секрет"
    assert body["target_url"] == "https://sentinel.test"


def test_github_error_is_wrapped(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "Resource not accessible by integration"},
            headers={"x-github-request-id": "req-123"},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await set_commit_status(
                client, settings, "acme/demo", "b" * 40, "failure", "block"
            )

    with pytest.raises(GitHubAPIError) as exc_info:
        _run(scenario())
    assert exc_info.value.status_code == 403
    assert "request_id=req-123" in str(exc_info.value)


def test_invalid_repository_rejected_before_network(settings) -> None:
    async def scenario(repo: str) -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            await post_pr_comment(client, settings, repo, 1, "text")

    for repo in ("../etc/passwd", "..", "owner/..", "no-slash"):
        with pytest.raises(ValueError):
            _run(scenario(repo))


def test_status_description_is_truncated_to_github_limit(settings) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await set_commit_status(
                client,
                settings,
                "acme/demo",
                "c" * 40,
                "failure",
                "очень длинное описание " * 100,
            )

    _run(scenario())
    body = captured["body"]
    assert isinstance(body, dict)
    assert len(body["description"]) <= 140
