"""Действия бота в GitHub: комментарий к PR и commit status.

Commit Status API — ключевой механизм блокировки: статус ``failure`` с нужным
context блокирует кнопку Merge, если в настройках ветки включены обязательные
status checks. Комментарий нужен, чтобы разработчик видел причину.
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx

from config import Settings
from github_api import (
    GITHUB_ACCEPT_JSON,
    github_request,
    split_repository,
    validate_commit_sha,
)

logger = logging.getLogger(__name__)

GitHubCommitState = Literal["error", "failure", "pending", "success"]

# Ограничения GitHub API.
_STATUS_CONTEXT_MAX = 100
_STATUS_DESCRIPTION_MAX = 140
_COMMENT_BODY_MAX = 60_000


def _truncate(text: str, limit: int) -> str:
    """Схлопнуть пробелы и обрезать строку до лимита GitHub."""
    normalized = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _validate_pull_number(pull_number: int) -> int:
    if (
        isinstance(pull_number, bool)
        or not isinstance(pull_number, int)
        or pull_number <= 0
    ):
        raise ValueError(f"Некорректный номер Pull Request: {pull_number!r}.")
    return pull_number


async def post_pr_comment(
    client: httpx.AsyncClient,
    settings: Settings,
    repo_full_name: str,
    pull_number: int,
    body: str,
) -> None:
    """Опубликовать комментарий в Pull Request от имени токена бота.

    Используется endpoint комментариев issue: PR в GitHub — это issue с
    дополнительными полями, поэтому комментарий появляется в обсуждении PR.
    """
    owner, repo = split_repository(repo_full_name)
    pull_number = _validate_pull_number(pull_number)

    if not isinstance(body, str) or not body.strip():
        raise ValueError("Текст комментария не может быть пустым.")
    if len(body) > _COMMENT_BODY_MAX:
        logger.warning(
            "Комментарий обрезан: %d > %d символов.", len(body), _COMMENT_BODY_MAX
        )
        body = body[:_COMMENT_BODY_MAX]

    response = await github_request(
        client,
        settings,
        "POST",
        f"/repos/{owner}/{repo}/issues/{pull_number}/comments",
        accept=GITHUB_ACCEPT_JSON,
        json={"body": body},
    )
    logger.info(
        "Комментарий опубликован в %s#%s (HTTP %s).",
        repo_full_name,
        pull_number,
        response.status_code,
    )


async def set_commit_status(
    client: httpx.AsyncClient,
    settings: Settings,
    repo_full_name: str,
    commit_sha: str,
    state: GitHubCommitState,
    description: str,
    *,
    context: str | None = None,
    target_url: str | None = None,
) -> None:
    """Установить commit status для HEAD-коммита PR.

    Состояния:
    - ``failure`` — критическая проблема или сбой анализа (fail-closed);
    - ``success`` — проблем не найдено;
    - ``pending``/``error`` — доступны для расширения.
    """
    owner, repo = split_repository(repo_full_name)
    commit_sha = validate_commit_sha(commit_sha)

    if state not in {"error", "failure", "pending", "success"}:
        raise ValueError(f"Некорректное состояние commit status: {state!r}.")

    status_context = (context or settings.status_context).strip()
    if not status_context:
        raise ValueError("Context commit status не может быть пустым.")

    status_description = _truncate(
        description or f"Результат проверки: {state}", _STATUS_DESCRIPTION_MAX
    )
    payload: dict[str, str] = {
        "state": state,
        "description": status_description,
        "context": status_context[:_STATUS_CONTEXT_MAX],
    }
    if target_url:
        payload["target_url"] = target_url

    response = await github_request(
        client,
        settings,
        "POST",
        f"/repos/{owner}/{repo}/statuses/{commit_sha}",
        accept=GITHUB_ACCEPT_JSON,
        json=payload,
    )
    logger.info(
        "Commit status %s установлен для %s@%s (HTTP %s).",
        state,
        repo_full_name,
        commit_sha[:7],
        response.status_code,
    )
