# SecureMerge / Compliance Sentinel — submission

## 30-секундный питч

**SecureMerge (Compliance Sentinel)** — GitHub-бот, который перехватывает Pull
Request, анализирует diff через LLM и не даёт смержить изменения с критическим
риском: захардкоженными секретами, небезопасной обработкой PII, GPL/AGPL-
зависимостями и очевидными инъекциями. Вердикт виден прямо в PR: комментарий
с причиной + обязательный commit status `Compliance Sentinel`. Если проверка
не прошла, branch protection блокирует Merge. При сбое LLM/GitHub действует
fail-closed: слияние останавливается для ручной проверки.

- Репозиторий: <https://github.com/Horda23/SecureMerge>
- Демо-PR: <https://github.com/Horda23/SecureMerge/pull/2>
- Подробная инструкция: [README.md](README.md)

## Что показать жюри

1. Открыть [PR #2](https://github.com/Horda23/SecureMerge/pull/2): в нём
   добавлен `test.py` с «секретом» в коде.
2. Показать комментарий бота с причиной блокировки.
3. Показать проверку `Compliance Sentinel` в состоянии **failure**.
4. Показать, что кнопка **Merge pull request** недоступна: branch protection
   требует успешного статуса.
5. Показать `GET /health` и `/docs` на запущенном сервисе.
6. Показать локальный запуск тестов `pytest -q` (41 тест).

После исправления секрета и нового push бот получает событие `synchronize`,
перепроверяет diff и выставляет `success` — Merge разблокируется.

## Архитектура

```text
GitHub webhook (opened / synchronize)
        │  HMAC SHA-256
        ▼
FastAPI /webhook (202 Accepted, BackgroundTasks)
        │
        ├─ GitHub REST API: получить unified diff PR
        ▼
LLM (DeepSeek по умолчанию в демо; Groq/любой OpenAI-compatible endpoint)
        │  строгий JSON: {"status": "BLOCK"|"PASS", "reason": "..."}
        ▼
BLOCK → комментарий в PR + commit status failure
PASS  → commit status success
        │
        ▼
Branch protection: обязательный check "Compliance Sentinel"
```

Ключевые свойства: HMAC по сырому телу, fail-closed, идемпотентный по
содержанию статус, отдельный комментарий только для BLOCK, ограничение
размера diff, защита Markdown от LLM-инъекций, секрет-редактирование
известных форматов токенов.

## Как запустить (для проверки)

### Вариант A: Docker Compose + публичный туннель — одна команда

```bash
cp .env.example .env             # Windows: Copy-Item .env.example .env
# заполнить GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET, GROQ_API_KEY
docker compose --profile tunnel up --build
```

В логах `tunnel` появится `https://*.trycloudflare.com` — это Payload URL
для GitHub webhook (`.../webhook`).

### Вариант B: один PowerShell-скрипт (Windows)

```powershell
.\start.ps1
```

Скрипт сам создаст `.env`, виртуальное окружение, поставит зависимости,
проверит конфиг и запустит uvicorn.

### Вариант C: Render (без локального терминала)

В репозитории есть `render.yaml` и `Dockerfile`. В Render:
**New → Blueprint → подключить репозиторий** → указать секреты
`GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GROQ_API_KEY` → Deploy. Render даст
публичный HTTPS URL; его нужно вставить в настройки GitHub webhook.

> Free-инстанс Render засыпает: первый запрос после простоя может быть
> медленнее. Перед демонстрацией откройте `/health`, чтобы разбудить сервис.

### Вариант D: вручную

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `GITHUB_TOKEN` | PAT с доступом к Pull requests и Commit statuses |
| `GITHUB_WEBHOOK_SECRET` | секрет HMAC, тот же в GitHub webhook |
| `GROQ_API_KEY` | ключ LLM-провайдера (DeepSeek `sk-...` в текущем демо) |
| `GROQ_API_URL` | `https://api.deepseek.com` или Groq/другой OpenAI-compatible endpoint |
| `GROQ_MODEL` | `deepseek-flash` в текущем демо |
| `STATUS_CONTEXT` | `Compliance Sentinel` — имя обязательной проверки |
| `ALLOWED_REPOSITORIES` | опциональный allow-list `owner/repo` |

Секреты в репозиторий не попадают: `.env` в `.gitignore`, в
`.env.example` оставлены только пустые поля.

## Проверка качества

```bash
pip install -r requirements-dev.txt
pytest -q          # 41 passed
ruff check .       # All checks passed
```

Покрыты: HMAC (включая мусорные заголовки), `opened`/`synchronize`, лимиты
webhook-тела (включая chunked), парсинг LLM-ответа и fail-closed, ретраи,
публикация комментария/статуса, редактирование секретов, полный BLOCK/PASS-
цикл и аномальные ответы GitHub.

## Безопасность и ограничения

- LLM не заменяет SAST/secret scanning/license scanner; возможны false
  positives/negatives. Бот — дополнительный контур, а не юридическая гарантия.
- Исходный diff отправляется выбранному LLM-провайдеру; для чувствительных
  репозиториев нужен self-hosted провайдер.
- `BackgroundTasks` не переживают рестарт процесса; для production нужна
  очередь и идемпотентность по `X-GitHub-Delivery`.
- В демо используется DeepSeek, потому что Groq отдаёт `403` из сети
  разработки. Код поддерживает Groq без изменений: достаточно вернуть
  `GROQ_API_URL`/`GROQ_MODEL` на Groq.
- GitHub PAT в демо имеет scope `public_repo`; для приватного репозитория
  нужен `repo`.

## Стек

Python 3.10+, FastAPI, uvicorn, httpx, python-dotenv, pytest, ruff,
DeepSeek/Groq OpenAI-compatible API, GitHub REST API, Docker, GitHub Actions.
