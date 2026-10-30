"""Небольшой асинхронный слой для GitHub REST API.

Здесь собраны общие вещи, которые нужны и для чтения diff, и для публикации
результата: валидация owner/repo и SHA, авторизованные заголовки, единая
обработка ошибок GitHub.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from config import Settings

logger = logging.getLogger(__name__)

GITHUB_ACCEPT_JSON = "application/vnd.github+json"
# Специальный media type, по которому GitHub отдаёт unified diff PR.
GITHUB_DIFF_ACCEPT = "application/vnd.github.v3.diff"
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "compliance-sentinel/1.0"

_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_MAX_ERROR_MESSAGE_LENGTH = 500


class GitHubAPIError(RuntimeError):
    """Ошибка GitHub REST API или сетевого запроса к нему."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def split_repository(repo_full_name: str) -> tuple[str, str]:
    """Проверить и разделить ``owner/repo`` на две безопасные части.

    Валидация не даёт собрать URL с лишними сегментами (path traversal).
    """
    if not isinstance(repo_full_name, str) or not _REPO_NAME_RE.fullmatch(
        repo_full_name
    ):
        raise ValueError(f"Некорректное имя репозитория: {repo_full_name!r}.")
    owner, repo = repo_full_name.split("/", 1)
    # "." и ".." формально проходят regex, но это не имена GitHub и это
    # позволило бы собрать путь с traversal-сегментами.
    if owner in {".", ".."} or repo in {".", ".."}:
        raise ValueError(f"Некорректное имя репозитория: {repo_full_name!r}.")
    return owner, repo


def validate_commit_sha(commit_sha: str) -> str:
    """Проверить, что SHA — полный hex-идентификатор коммита."""
    if not isinstance(commit_sha, str) or not _COMMIT_SHA_RE.fullmatch(commit_sha):
        raise ValueError(f"Некорректный SHA коммита: {commit_sha!r}.")
    return commit_sha


def github_headers(
    settings: Settings, *, accept: str = GITHUB_ACCEPT_JSON
) -> dict[str, str]:
    """Собрать авторизованные заголовки GitHub API."""
    return {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": accept,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": USER_AGENT,
    }


def _format_github_error(response: httpx.Response) -> str:
    """Короткое безопасное описание ошибки GitHub для логов и исключения."""
    message = "описание отсутствует"
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        raw_message = data.get("message")
        if isinstance(raw_message, str) and raw_message.strip():
            message = raw_message.strip()
    elif isinstance(data, list) and data:
        # Некоторые endpoint'ы возвращают список ошибок.
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("message"), str):
            message = first["message"].strip()

    message = " ".join(message.split())[:_MAX_ERROR_MESSAGE_LENGTH]
    request_id = response.headers.get("x-github-request-id")
    suffix = f" (request_id={request_id})" if request_id else ""
    return f"GitHub API вернул HTTP {response.status_code}: {message}{suffix}"


async def github_request(
    client: httpx.AsyncClient,
    settings: Settings,
    method: str,
    path: str,
    *,
    accept: str = GITHUB_ACCEPT_JSON,
    **kwargs: Any,
) -> httpx.Response:
    """Выполнить запрос к GitHub API и превратить ошибки в GitHubAPIError.

    ``path`` начинается со слэша и уже содержит проверенные сегменты URL.
    Ответы 2xx возвращаются как есть; 3xx автоматически следуют редиректам
    (нужно для отдачи diff крупных PR).
    """
    url = f"{settings.github_api_url.rstrip('/')}/{path.lstrip('/')}"
    headers = github_headers(settings, accept=accept)
    extra_headers = kwargs.pop("headers", None)
    if extra_headers:
        headers.update(extra_headers)

    try:
        response = await client.request(
            method,
            url,
            headers=headers,
            timeout=settings.http_timeout_seconds,
            follow_redirects=True,
            **kwargs,
        )
    except httpx.HTTPError as exc:
        logger.warning("Сетевая ошибка GitHub API (%s %s): %s", method, url, exc)
        raise GitHubAPIError(
            "Не удалось соединиться с GitHub API. Проверьте сеть и повторите позже."
        ) from exc

    if response.status_code >= 400:
        error_text = _format_github_error(response)
        logger.warning("Ошибка GitHub API (%s %s): %s", method, url, error_text)
        raise GitHubAPIError(error_text, status_code=response.status_code)

    return response
