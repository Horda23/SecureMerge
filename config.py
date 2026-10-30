"""Конфигурация Compliance Sentinel.

Все секреты и настройки читаются из переменных окружения. Файл `.env`
загружается автоматически, если он лежит рядом с проектом; уже заданные
переменные окружения имеют приоритет. В коде нет захардкоженных секретов.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent

# .env — удобство локальной разработки. override=False не даёт файлу
# перетереть переменные, которые уже выставлены в окружении/CI.
load_dotenv(dotenv_path=PROJECT_DIR / ".env", override=False)

DEFAULT_GITHUB_API_URL = "https://api.github.com"
# Локальный OpenAI-совместимый endpoint Ollama для запуска приложения в Docker.
DEFAULT_LLM_API_URL = "http://host.docker.internal:11434/v1"
DEFAULT_LLM_MODEL = "qwen2.5:3b"
DEFAULT_STATUS_CONTEXT = "Compliance Sentinel"

_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_MAX_STATUS_CONTEXT_LENGTH = 100


class ConfigurationError(RuntimeError):
    """Некорректная или неполная конфигурация приложения."""


_REQUIRED_ENV_HELP = {
    "GITHUB_TOKEN": (
        "Создайте GitHub PAT: https://github.com/settings/tokens\n"
        "  Нужен доступ к Pull requests (read/write) и Commit statuses (read/write)."
    ),
    "GITHUB_WEBHOOK_SECRET": (
        "Придумайте случайную строку и укажите её же в GitHub webhook Secret.\n"
        "  Пример генерации: python -c \"import secrets; print(secrets.token_hex(32))\"."
    ),
    "LLM_API_KEY": (
        "Укажите ключ LLM-провайдера. Для локальной Ollama подойдёт любое "
        "непустое значение, например: LLM_API_KEY=ollama."
    ),
}


def _missing_env_message(name: str) -> str:
    help_text = _REQUIRED_ENV_HELP.get(name, "Заполните переменную в .env.")
    return (
        f"Не задана обязательная переменная {name}.\n"
        f"  {help_text}\n"
        "  Файл .env создаётся командой: cp .env.example .env"
    )


def _read_str(name: str, default: str | None = None, *, required: bool = False) -> str:
    """Прочитать строковую переменную, обрезав пробелы."""
    raw = os.getenv(name)
    value = raw.strip() if raw else ""
    if value:
        return value
    if required:
        raise ConfigurationError(_missing_env_message(name))
    return default or ""


def _read_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Прочитать целое число и проверить допустимый диапазон."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} должна быть целым числом, получено: {raw!r}."
        ) from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} должна находиться в диапазоне {minimum}..{maximum}, получено {value}."
        )
    return value


def _read_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """Прочитать число с плавающей точкой и проверить диапазон."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} должна быть числом, получено: {raw!r}."
        ) from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} должна находиться в диапазоне {minimum}..{maximum}, получено {value}."
        )
    return value


def _read_url(name: str, default: str) -> str:
    """Прочитать и проверить абсолютный HTTP(S)-URL."""
    value = _read_str(name, default).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(
            f"{name} должна быть абсолютным HTTP(S)-URL, получено: {value!r}."
        )
    return value


def _read_allowed_repositories() -> frozenset[str]:
    """Разобрать необязательный allow-list вида ``owner/repo, owner2/repo2``."""
    raw = os.getenv("ALLOWED_REPOSITORIES", "")
    repositories: set[str] = set()
    for item in raw.split(","):
        repo = item.strip()
        if not repo:
            continue
        if not _REPO_NAME_RE.fullmatch(repo):
            raise ConfigurationError(
                "ALLOWED_REPOSITORIES должна содержать значения owner/repo "
                f"через запятую; некорректное значение: {repo!r}."
            )
        repositories.add(repo.lower())
    return frozenset(repositories)


@dataclass(frozen=True, slots=True)
class Settings:
    """Настройки приложения.

    Обязательных секретов три: GitHub-токен, секрет вебхука и ключ LLM API.
    Провайдер LLM задаётся через LLM_API_URL/LLM_MODEL и должен поддерживать
    OpenAI-совместимый endpoint /chat/completions.
    """

    github_token: str
    github_webhook_secret: str
    llm_api_key: str

    github_api_url: str = DEFAULT_GITHUB_API_URL
    llm_api_url: str = DEFAULT_LLM_API_URL
    llm_model: str = DEFAULT_LLM_MODEL

    status_context: str = DEFAULT_STATUS_CONTEXT
    max_diff_chars: int = 80_000
    http_timeout_seconds: float = 30.0
    llm_timeout_seconds: float = 300.0
    llm_max_retries: int = 1
    max_concurrent_audits: int = 2

    public_base_url: str | None = None
    allowed_repositories: frozenset[str] = frozenset()
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        """Собрать настройки из окружения с валидацией значений."""
        webhook_secret = _read_str("GITHUB_WEBHOOK_SECRET", required=True)
        if len(webhook_secret) < 16:
            raise ConfigurationError(
                "GITHUB_WEBHOOK_SECRET слишком короткий: нужно минимум 16 символов.\n"
                "  Сгенерируйте: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
                "  и укажите это же значение в GitHub webhook Secret."
            )

        status_context = _read_str("STATUS_CONTEXT", DEFAULT_STATUS_CONTEXT)
        if len(status_context) > _MAX_STATUS_CONTEXT_LENGTH:
            raise ConfigurationError(
                f"STATUS_CONTEXT должен быть не длиннее {_MAX_STATUS_CONTEXT_LENGTH} символов "
                "из-за ограничения GitHub."
            )

        public_url_raw = _read_str("PUBLIC_BASE_URL", "")
        public_base_url: str | None = None
        if public_url_raw:
            parsed = urlparse(public_url_raw)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ConfigurationError(
                    "PUBLIC_BASE_URL должен быть абсолютным HTTP(S)-URL или пустым."
                )
            public_base_url = public_url_raw.rstrip("/")

        log_level = _read_str("LOG_LEVEL", "INFO").upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigurationError(
                f"LOG_LEVEL должен быть одним из {sorted(_LOG_LEVELS)}, получено {log_level!r}."
            )

        return cls(
            github_token=_read_str("GITHUB_TOKEN", required=True),
            github_webhook_secret=webhook_secret,
            llm_api_key=_read_str("LLM_API_KEY", required=True),
            github_api_url=_read_url("GITHUB_API_URL", DEFAULT_GITHUB_API_URL),
            llm_api_url=_read_url("LLM_API_URL", DEFAULT_LLM_API_URL),
            llm_model=_read_str("LLM_MODEL", DEFAULT_LLM_MODEL),
            status_context=status_context,
            max_diff_chars=_read_int(
                "MAX_DIFF_CHARS", 80_000, minimum=1_000, maximum=500_000
            ),
            http_timeout_seconds=_read_float(
                "HTTP_TIMEOUT_SECONDS", 30.0, minimum=1.0, maximum=300.0
            ),
            llm_timeout_seconds=_read_float(
                "LLM_TIMEOUT_SECONDS", 300.0, minimum=5.0, maximum=900.0
            ),
            llm_max_retries=_read_int("LLM_MAX_RETRIES", 1, minimum=0, maximum=5),
            max_concurrent_audits=_read_int(
                "MAX_CONCURRENT_AUDITS", 2, minimum=1, maximum=50
            ),
            public_base_url=public_base_url,
            allowed_repositories=_read_allowed_repositories(),
            log_level=log_level,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Вернуть кэшированные настройки (создаются при старте приложения)."""
    return Settings.from_env()
