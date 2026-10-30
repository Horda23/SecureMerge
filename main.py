"""Compliance Sentinel — FastAPI-сервис для проверки Pull Request.

Поток работы:

1. GitHub присылает подписанный webhook на POST /webhook.
2. Проверяем HMAC SHA-256, событие и действие (opened/synchronize).
3. Возвращаем 202 сразу и запускаем аудит в BackgroundTasks.
4. Получаем unified diff PR через GitHub REST API.
5. Отправляем diff в LLM (локальная Ollama или внешний провайдер), получаем
   JSON-вердикт BLOCK/PASS.
6. Публикуем комментарий (для BLOCK) и commit status failure/success.
   Branch protection с обязательным статусом "Compliance Sentinel"
   не даст нажать Merge при failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status

from ai_analyzer import AnalysisResult, analyze_diff
from config import ConfigurationError, Settings, get_settings
from github_actions import post_pr_comment, set_commit_status
from github_api import (
    GITHUB_DIFF_ACCEPT,
    GitHubAPIError,
    github_request,
    split_repository,
    validate_commit_sha,
)

APP_VERSION = "1.0.0"
SUPPORTED_PR_ACTIONS = frozenset({"opened", "synchronize"})
MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024  # GitHub payload обычно << 1 МБ

# Известные форматы секретов, которые модель не должна перепечатывать в PR.
# Это дополнительная защита: основной барьер — запрет в системном промпте.
_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)

# Дополнительная защита от типичной ошибки LLM: модель находит секрет и
# перепечатывает его значение в reason. Это шире, чем известные форматы
# токенов выше, и покрывает пароли/PII в кавычках и присваиваниях.
_GENERIC_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"
    r"access[_-]?key|private[_-]?key))\b(\s*[:=]\s*)(['\"]?)([^\s'\"]{4,})\3"
)
_QUOTED_SECRET_VALUE_RE = re.compile(
    r"(?i)(['\"`])([^'\"`\n]{0,60}(?:password|passwd|pwd|secret|token|"
    r"api[_-]?key|access[_-]?key|private[_-]?key)[^'\"`\n]{0,60})\1"
)
_RU_QUOTED_SECRET_RE = re.compile(
    r"(?i)\b(парол\w*|токен\w*|секрет\w*|ключ\w*)\b([^\"'\n]{0,25})(['\"])([^\"'\n]{3,})\3"
)

logger = logging.getLogger("compliance_sentinel")


def configure_logging(level: str) -> None:
    """Настроить logging для приложения и приглушить шум httpx."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=False,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Создать общий HTTP-клиент и проверить конфигурацию при старте."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        logger.critical("Не удалось запустить сервис: %s", exc)
        raise

    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    app.state.audit_semaphore = asyncio.Semaphore(settings.max_concurrent_audits)

    logger.info(
        "Compliance Sentinel v%s запущен. Модель: %s; статус: %r; "
        "лимит diff: %d символов; параллельных аудитов: %d.",
        APP_VERSION,
        settings.llm_model,
        settings.status_context,
        settings.max_diff_chars,
        settings.max_concurrent_audits,
    )
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        logger.info("Compliance Sentinel остановлен.")


app = FastAPI(
    title="Compliance Sentinel",
    version=APP_VERSION,
    description=(
        "AI-бот аудита Pull Request: ищет секреты, утечки PII и проблемы "
        "с GPL-лицензиями, затем блокирует merge через Commit Status API."
    ),
    lifespan=lifespan,
)


@dataclass(frozen=True, slots=True)
class PullRequestContext:
    """Минимальные проверенные данные о PR из webhook-payload."""

    repo_full_name: str
    pull_number: int
    head_sha: str
    action: str


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Проверить подпись GitHub ``X-Hub-Signature-256``.

    Сравнение выполняется в постоянном времени, чтобы не давать атаковать
    секрет по таймингу. Подпись считается от сырых байт запроса, а не от
    повторно сериализованного JSON.
    """
    if not raw_body or not secret or not isinstance(signature_header, str):
        return False
    prefix = "sha256="
    signature = signature_header.strip()
    if not signature.startswith(prefix):
        return False
    received_digest = signature[len(prefix) :].strip()
    if len(received_digest) != 64:
        return False
    try:
        received_bytes = bytes.fromhex(received_digest)
    except ValueError:
        # bytes.fromhex/compare_digest не должны падать на мусорном заголовке.
        return False
    expected_bytes = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(received_bytes, expected_bytes)


def parse_pull_request_payload(payload: dict[str, Any]) -> PullRequestContext:
    """Извлечь и провалидировать поля PR из доверенного webhook-payload."""
    action = payload.get("action")
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")

    if not isinstance(action, str) or action not in SUPPORTED_PR_ACTIONS:
        raise ValueError("Неподдерживаемое действие Pull Request.")
    if not isinstance(repository, dict) or not isinstance(pull_request, dict):
        raise TypeError("В payload нет repository или pull_request.")

    repo_full_name = repository.get("full_name")
    split_repository(repo_full_name)  # валидация owner/repo и защита URL

    pull_number = pull_request.get("number")
    if (
        isinstance(pull_number, bool)
        or not isinstance(pull_number, int)
        or pull_number <= 0
    ):
        raise ValueError("Некорректный номер Pull Request.")

    head = pull_request.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    validate_commit_sha(head_sha)  # защита URL commit status

    return PullRequestContext(
        repo_full_name=repo_full_name,
        pull_number=pull_number,
        head_sha=head_sha,
        action=action,
    )


async def _read_limited_webhook_body(request: Request) -> bytes:
    """Прочитать тело вебхука потоково, не превышая MAX_WEBHOOK_BODY_BYTES.

    Проверки только Content-Length недостаточно: chunked-запрос вполне может
    прийти без него. Поэтому читаем чанки и обрываемся сразу при превышении,
    не буферизуя потенциально огромное тело целиком.
    """
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large.")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload too large.")
    return bytes(body)


async def fetch_pull_request_diff(
    client: httpx.AsyncClient,
    settings: Settings,
    repo_full_name: str,
    pull_number: int,
) -> str:
    """Получить unified diff Pull Request через GitHub API.

    Для этого используется тот же endpoint, что и для метаданных PR,
    но с Accept: application/vnd.github.v3.diff. GitHub может ответить
    редиректом — клиент следует ему.
    """
    owner, repo = split_repository(repo_full_name)
    if (
        isinstance(pull_number, bool)
        or not isinstance(pull_number, int)
        or pull_number <= 0
    ):
        raise ValueError(f"Некорректный номер Pull Request: {pull_number!r}.")

    response = await github_request(
        client,
        settings,
        "GET",
        f"/repos/{owner}/{repo}/pulls/{pull_number}",
        accept=GITHUB_DIFF_ACCEPT,
    )
    content_type = response.headers.get("content-type", "")
    if content_type and "diff" not in content_type.lower():
        raise GitHubAPIError(
            "GitHub API вернул ответ, не похожий на unified diff.",
            status_code=response.status_code,
        )

    # Diff — это текст; декодируем строго как UTF-8 с заменой битых байт,
    # чтобы не упасть на нестандартной кодировке в файлах.
    diff = response.content.decode("utf-8", errors="replace")
    stripped_diff = diff.lstrip("\ufeff \t\r\n")
    if stripped_diff and not stripped_diff.startswith("diff --git "):
        raise GitHubAPIError(
            "GitHub API вернул 200, но тело не является unified diff.",
            status_code=response.status_code,
        )
    logger.info(
        "Получен diff для %s#%s: %d символов.",
        repo_full_name,
        pull_number,
        len(diff),
    )
    return diff


def _redact_secret_fragments(text: str) -> str:
    """Замаскировать секреты в тексте LLM перед публикацией в PR.

    Сначала вырезаем известные форматы токенов, затем типичные присваивания
    секретов и значения в кавычках. Это снижает риск повторной публикации
    credentials, хотя не может гарантировать 100% для произвольных форматов.
    """
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)

    text = _GENERIC_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text
    )
    text = _QUOTED_SECRET_VALUE_RE.sub("[REDACTED]", text)
    text = _RU_QUOTED_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text
    )
    return text


def _sanitize_reason_for_markdown(reason: str) -> str:
    """Подготовить текст LLM для безопасной вставки в GitHub Markdown.

    Модель — потенциально ненадёжный источник текста: редактируем известные
    паттерны секретов, экранируем HTML, нейтрализуем @-упоминания и
    markdown-ссылки/картинки, ограничиваем длину.
    """
    text = " ".join(str(reason).replace("\r", " ").replace("\n", " ").split())
    text = _redact_secret_fragments(text)
    if len(text) > 2_000:
        text = text[:2_000].rstrip() + "…"
    text = html.escape(text, quote=False)
    # Не даём упоминать пользователей/команды и вставлять ссылки.
    text = text.replace("@", "@\u200b")
    text = text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return text


def build_block_comment(
    result: AnalysisResult,
    settings: Settings,
    *,
    repo_full_name: str,
    pull_number: int,
    head_sha: str,
    action: str,
    status_published: bool,
) -> str:
    """Собрать Markdown-комментарий с причиной блокировки."""
    reason = _sanitize_reason_for_markdown(result.reason)
    notes: list[str] = []

    if result.degraded:
        notes.append(
            "> ⚠️ Автоматический анализ не завершился. Слияние заблокировано "
            "по принципу fail-closed — требуется ручная проверка."
        )
    if not status_published:
        notes.append(
            "> ⚠️ Не удалось установить commit status: проверьте права токена "
            "(Commit statuses: write). Блокировка Merge в branch protection "
            "не гарантирована."
        )

    notes_block = "\n>\n".join(notes)
    if notes_block:
        notes_block += "\n\n"

    model = html.escape(result.model or settings.llm_model)
    context = html.escape(settings.status_context)
    action_safe = html.escape(action)

    return (
        "## 🛑 Compliance Sentinel: проверка не пройдена\n\n"
        "**Причина:**\n"
        f"> {reason}\n\n"
        f"{notes_block}"
        "**Что делать:** исправьте проблему и добавьте новый коммит — "
        "проверка перезапустится автоматически (событие `synchronize`).\n\n"
        "---\n"
        f"<sub>Проверка: <b>{context}</b> · "
        f"PR: <code>{repo_full_name}#{pull_number}</code> · "
        f"коммит: <code>{head_sha[:7]}</code> · "
        f"модель: <code>{model}</code> · "
        f"событие: <code>{action_safe}</code></sub>"
    )


def _status_description(result: AnalysisResult) -> str:
    """Короткое описание для commit status.

    Намеренно не включаем reason LLM: commit status доступен шире, чем сам PR,
    а модель может по ошибке процитировать секрет или PII. Подробности остаются
    в комментарии PR, где diff и так виден команде.
    """
    if result.degraded:
        return "BLOCK (fail-closed) — требуется ручная проверка"
    if result.is_blocking:
        return "BLOCK — критическая проблема, подробности в PR"
    return "PASS — критических проблем не найдено"


async def publish_audit_result(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    repo_full_name: str,
    pull_number: int,
    head_sha: str,
    action: str,
    result: AnalysisResult,
) -> None:
    """Опубликовать вердикт: сначала статус, затем комментарий при BLOCK.

    Ошибки публикации не выбрасываются наружу: фоновый аудит не должен
    ронять приложение. Каждая ошибка логируется отдельно, чтобы можно было
    диагностировать права токена или недоступность GitHub.
    """
    new_state = "failure" if result.is_blocking else "success"

    status_published = True
    try:
        await set_commit_status(
            client,
            settings,
            repo_full_name,
            head_sha,
            new_state,
            description=_status_description(result),
            target_url=settings.public_base_url,
        )
    except Exception:
        status_published = False
        logger.exception(
            "Не удалось установить commit status %s для %s@%s.",
            new_state,
            repo_full_name,
            head_sha[:7],
        )

    if result.is_blocking:
        try:
            comment = build_block_comment(
                result,
                settings,
                repo_full_name=repo_full_name,
                pull_number=pull_number,
                head_sha=head_sha,
                action=action,
                status_published=status_published,
            )
            await post_pr_comment(
                client, settings, repo_full_name, pull_number, comment
            )
        except Exception:
            logger.exception(
                "Не удалось опубликовать комментарий в %s#%s.",
                repo_full_name,
                pull_number,
            )

    logger.info(
        "Аудит %s#%s завершён: %s (degraded=%s, status_published=%s).",
        repo_full_name,
        pull_number,
        result.status,
        result.degraded,
        status_published,
    )


async def run_compliance_audit(
    client: httpx.AsyncClient,
    settings: Settings,
    semaphore: asyncio.Semaphore,
    *,
    repo_full_name: str,
    pull_number: int,
    head_sha: str,
    action: str,
    delivery_id: str,
) -> None:
    """Полный цикл аудита одного PR в фоновой задаче."""
    async with semaphore:
        logger.info(
            "Аудит принят: %s#%s, commit=%s, delivery=%s.",
            repo_full_name,
            pull_number,
            head_sha[:7],
            delivery_id,
        )

        try:
            diff = await fetch_pull_request_diff(
                client, settings, repo_full_name, pull_number
            )
        except GitHubAPIError as exc:
            detail = (
                f"GitHub API вернул HTTP {exc.status_code}"
                if exc.status_code
                else "ошибка соединения с GitHub API"
            )
            logger.exception(
                "Не удалось получить diff для %s#%s.", repo_full_name, pull_number
            )
            result = AnalysisResult(
                status="BLOCK",
                reason=(
                    f"Не удалось получить diff Pull Request ({detail}). "
                    "Требуется ручная проверка."
                ),
                degraded=True,
            )
        except Exception:
            logger.exception(
                "Неожиданная ошибка при получении diff для %s#%s.",
                repo_full_name,
                pull_number,
            )
            result = AnalysisResult(
                status="BLOCK",
                reason=(
                    "Неожиданная ошибка при получении diff Pull Request. "
                    "Требуется ручная проверка."
                ),
                degraded=True,
            )
        else:
            if not diff.strip():
                result = AnalysisResult(
                    status="PASS",
                    reason="GitHub не вернул изменений в коде — проверять нечего.",
                    model=settings.llm_model,
                )
            else:
                try:
                    result = await analyze_diff(diff, client=client, settings=settings)
                except Exception:
                    # analyze_diff спроектирован fail-closed, но подстрахуемся
                    # от действительно неожиданных ошибок.
                    logger.exception(
                        "Неожиданный сбой анализатора для %s#%s.",
                        repo_full_name,
                        pull_number,
                    )
                    result = AnalysisResult(
                        status="BLOCK",
                        reason=(
                            "Анализатор завершился с непредвиденной ошибкой. "
                            "Требуется ручная проверка."
                        ),
                        degraded=True,
                    )

        await publish_audit_result(
            client,
            settings,
            repo_full_name=repo_full_name,
            pull_number=pull_number,
            head_sha=head_sha,
            action=action,
            result=result,
        )


@app.get("/health", tags=["service"], summary="Проверка доступности сервиса")
async def health() -> dict[str, str]:
    """Лёгкий healthcheck для ngrok/мониторинга; секретов не раскрывает."""
    return {
        "status": "ok",
        "service": "compliance-sentinel",
        "version": APP_VERSION,
    }


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    """Корневой endpoint: удобно открыть публичный URL и увидеть, что сервис жив."""
    return {
        "service": "Compliance Sentinel",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }


@app.post(
    "/webhook",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["webhook"],
    summary="Приём GitHub webhook",
)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Принять webhook GitHub и поставить аудит PR в фон.

    Сразу отвечаем 202: GitHub требует быстрый ответ, а анализ LLM может
    занимать десятки секунд. Результат публикуется через GitHub API.
    """
    settings: Settings = request.app.state.settings
    event_name = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")[:100]

    # Читаем тело потоково с жёстким лимитом и только потом проверяем HMAC.
    raw_body = await _read_limited_webhook_body(request)

    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_webhook_signature(
        raw_body, signature, settings.github_webhook_secret
    ):
        logger.warning(
            "Отклонён webhook с неверной подписью (event=%r, delivery=%s).",
            event_name,
            delivery_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    # Ping приходит при создании webhook в GitHub UI.
    if event_name == "ping":
        logger.info("Получен ping от GitHub (delivery=%s).", delivery_id)
        return {"status": "pong", "delivery": delivery_id}

    if event_name != "pull_request":
        return {
            "status": "ignored",
            "reason": "Поддерживается только событие pull_request.",
        }

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Некорректный JSON в webhook (delivery=%s).", delivery_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload must be a JSON object.",
        )

    action = payload.get("action")
    if not isinstance(action, str) or action not in SUPPORTED_PR_ACTIONS:
        logger.info(
            "PR-событие с действием %r проигнорировано (delivery=%s).",
            action,
            delivery_id,
        )
        return {
            "status": "ignored",
            "reason": "Обрабатываются только действия opened и synchronize.",
        }

    try:
        pr = parse_pull_request_payload(payload)
    except (TypeError, ValueError) as exc:
        logger.warning("Некорректный payload PR (delivery=%s): %s", delivery_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid pull_request payload: {exc}",
        ) from exc

    if (
        settings.allowed_repositories
        and pr.repo_full_name.lower() not in settings.allowed_repositories
    ):
        logger.info(
            "Репозиторий %s не входит в ALLOWED_REPOSITORIES; PR #%s пропущен.",
            pr.repo_full_name,
            pr.pull_number,
        )
        return {
            "status": "ignored",
            "reason": "Репозиторий не входит в ALLOWED_REPOSITORIES.",
        }

    # Важно: add_task выполняется после отправки HTTP-ответа. Сама функция
    # run_compliance_audit ловит свои ошибки и не роняет event loop.
    background_tasks.add_task(
        run_compliance_audit,
        request.app.state.http_client,
        settings,
        request.app.state.audit_semaphore,
        repo_full_name=pr.repo_full_name,
        pull_number=pr.pull_number,
        head_sha=pr.head_sha,
        action=pr.action,
        delivery_id=delivery_id,
    )

    logger.info(
        "Принят PR %s#%s (%s, commit=%s, delivery=%s).",
        pr.repo_full_name,
        pr.pull_number,
        pr.action,
        pr.head_sha[:7],
        delivery_id,
    )
    return {
        "status": "accepted",
        "repository": pr.repo_full_name,
        "pull_number": pr.pull_number,
        "action": pr.action,
        "delivery": delivery_id,
    }


if __name__ == "__main__":
    # Альтернатива CLI: python main.py. В README рекомендуется uvicorn.
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
