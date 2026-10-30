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
DEFAULT_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_STATUS_CONTEXT = "Compliance Sentinel"

_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# Максимальная длина context в Commit Status API (ограничение GitHub).
_MAX_STATUS_CONTEXT_LENGTH = 100


class ConfigurationError(RuntimeError):
    """Некорректная или неполная конфигурация приложения."""


def _read_str(name: str, default: str | None = None, *, required: bool = False) -> str:
    """Прочитать строковую переменную, обрезав пробелы."""
    raw = os.getenv(name)
    value = raw.strip() if raw else ""
    if value:
        return value
    if required:
        raise ConfigurationError(
            f"Обязательная переменная окружения {name} не задана. "
            "Скопируйте .env.example в .env и заполните секреты."
        )
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

    Обязательных секретов всего три: GitHub-токен, секрет вебхука и ключ Groq.
    Остальные параметры имеют безопасные значения по умолчанию.
    """

    github_token: str
    github_webhook_secret: str
    groq_api_key: str

    github_api_url: str = DEFAULT_GITHUB_API_URL
    groq_api_url: str = DEFAULT_GROQ_API_URL
    groq_model: str = DEFAULT_GROQ_MODEL

    status_context: str = DEFAULT_STATUS_CONTEXT
    max_diff_chars: int = 80_000
    http_timeout_seconds: float = 30.0
    groq_timeout_seconds: float = 90.0
    groq_max_retries: int = 2
    max_concurrent_audits: int = 4

    public_base_url: str | None = None
    allowed_repositories: frozenset[str] = frozenset()
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        """Собрать настройки из окружения с валидацией значений."""
        webhook_secret = _read_str("GITHUB_WEBHOOK_SECRET", required=True)
        if len(webhook_secret) < 16:
            raise ConfigurationError(
                "GITHUB_WEBHOOK_SECRET должен содержать не менее 16 символов. "
                'Сгенерируйте его командой: python -c "import secrets; print(secrets.token_hex(32))".'
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
            groq_api_key=_read_str("GROQ_API_KEY", required=True),
            github_api_url=_read_url("GITHUB_API_URL", DEFAULT_GITHUB_API_URL),
            groq_api_url=_read_url("GROQ_API_URL", DEFAULT_GROQ_API_URL),
            groq_model=_read_str("GROQ_MODEL", DEFAULT_GROQ_MODEL),
            status_context=status_context,
            max_diff_chars=_read_int(
                "MAX_DIFF_CHARS", 80_000, minimum=1_000, maximum=500_000
            ),
            http_timeout_seconds=_read_float(
                "HTTP_TIMEOUT_SECONDS", 30.0, minimum=1.0, maximum=300.0
            ),
            groq_timeout_seconds=_read_float(
                "GROQ_TIMEOUT_SECONDS", 90.0, minimum=5.0, maximum=600.0
            ),
            groq_max_retries=_read_int("GROQ_MAX_RETRIES", 2, minimum=0, maximum=5),
            max_concurrent_audits=_read_int(
                "MAX_CONCURRENT_AUDITS", 4, minimum=1, maximum=50
            ),
            public_base_url=public_base_url,
            allowed_repositories=_read_allowed_repositories(),
            log_level=log_level,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Вернуть кэшированные настройки (создаются при старте приложения)."""
    return Settings.from_env()
