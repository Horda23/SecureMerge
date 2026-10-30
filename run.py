"""Friendly entrypoint for Compliance Sentinel.

Use this instead of calling uvicorn directly: it validates configuration first
and prints an actionable message instead of a raw traceback when `.env` is
missing or incomplete. The application itself and its fail-closed behavior are
unchanged.

Examples:
    python run.py
    python run.py --check
"""

from __future__ import annotations

import os
import sys

from config import ConfigurationError, get_settings


def _force_utf8_console() -> None:
    """Avoid cp1252 encoding errors for Russian messages on Windows."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _print_config_help(error: ConfigurationError) -> None:
    print()
    print("=" * 78)
    print("Compliance Sentinel не может запуститься: проверьте .env")
    print("=" * 78)
    print()
    print(f"Причина: {error}")
    print()
    print("Что сделать:")
    print("  1. Если .env ещё нет, создайте его: cp .env.example .env")
    print("  2. Заполните GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET и LLM_API_KEY.")
    print("     GitHub PAT:  https://github.com/settings/tokens")
    print("     Секрет вебхука: python -c \"import secrets; print(secrets.token_hex(32))\"")
    print("     LLM: для локальной Ollama подойдёт LLM_API_KEY=ollama.")
    print("  3. Для локальной Ollama проверьте:")
    print("     LLM_API_URL=http://host.docker.internal:11434/v1")
    print("     LLM_MODEL=qwen2.5:3b")
    print("  4. Запустите снова: python run.py")
    print()
    print("Подробная инструкция: README.md, раздел «Быстрый старт за 5 минут».")
    print("=" * 78)
    print()


def _read_port() -> int:
    raw = os.getenv("PORT", "8000").strip()
    try:
        port = int(raw)
    except ValueError:
        print(f"Некорректный PORT={raw!r}; использую 8000.")
        return 8000
    if not 1 <= port <= 65535:
        print(f"PORT={port} вне диапазона 1..65535; использую 8000.")
        return 8000
    return port


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = list(argv if argv is not None else sys.argv[1:])
    check_only = "--check" in args or "-c" in args

    try:
        settings = get_settings()
    except ConfigurationError as exc:
        _print_config_help(exc)
        return 1

    if check_only:
        print("Конфигурация корректна.")
        print(f"  LLM endpoint: {settings.llm_api_url}")
        print(f"  LLM model:    {settings.llm_model}")
        print(f"  GitHub API:   {settings.github_api_url}")
        return 0

    import uvicorn

    host = os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = _read_port()
    print(f"Compliance Sentinel: http://127.0.0.1:{port}/health")
    print(f"Swagger:             http://127.0.0.1:{port}/docs")
    print("Остановка: Ctrl+C")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
