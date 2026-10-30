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
- Демо-PR: <https://github.com/Horda23/SecureMerge/pull/3>
- Подробная инструкция: [README.md](README.md)

## Что показать жюри

1. Открыть [PR #3](https://github.com/Horda23/SecureMerge/pull/3): в нём
   добавлен `test.py` с «секретом» в коде.
2. Показать комментарий бота с реальной причиной блокировки от локальной
   Qwen2.5 3B.
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
LLM (локальная Qwen2.5 3B через Ollama в Docker; поддерживаются Groq,
DeepSeek и любой OpenAI-compatible endpoint)
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

### Вариант A: локальная LLM через Ollama — без внешнего API и ключей

```bash
# 1. Ollama в Docker
docker run -d --name compliance-ollama --restart unless-stopped \
  -p 11434:11434 -v compliance_ollama:/root/.ollama ollama/ollama
docker exec compliance-ollama ollama pull qwen2.5:3b

# 2. .env
GITHUB_TOKEN=...
GITHUB_WEBHOOK_SECRET=...
GROQ_API_KEY=ollama
GROQ_API_URL=http://host.docker.internal:11434/v1
GROQ_MODEL=qwen2.5:3b
GROQ_TIMEOUT_SECONDS=300

# 3. Приложение
docker compose up -d --build
```

Проверка: `curl http://127.0.0.1:8000/health`.

### Вариант B: Docker Compose + публичный туннель

```bash
cp .env.example .env             # Windows: Copy-Item .env.example .env
# заполнить GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET, GROQ_API_KEY
docker compose --profile tunnel up --build
```

В логах `tunnel` появится `https://*.trycloudflare.com` — это Payload URL
для GitHub webhook (`.../webhook`). Если Cloudflare недоступен из вашей сети,
используйте ngrok/другой HTTPS-туннель.

### Вариант C: один PowerShell-скрипт (Windows)

```powershell
.\start.ps1
```

Скрипт сам создаст `.env`, виртуальное окружение, поставит зависимости,
проверит конфиг и запустит uvicorn.

### Вариант D: Render (без локального терминала)

В репозитории есть `render.yaml` и `Dockerfile`. В Render:
**New → Blueprint → подключить репозиторий** → указать секреты
`GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GROQ_API_KEY` → Deploy. Render даст
публичный HTTPS URL; его нужно вставить в настройки GitHub webhook.

> Free-инстанс Render засыпает: первый запрос после простоя может быть
> медленнее. Перед демонстрацией откройте `/health`, чтобы разбудить сервис.

### Вариант E: вручную

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
| `GROQ_API_KEY` | ключ провайдера (`sk-...`, `gsk-...`) или `ollama` для локальной модели |
| `GROQ_API_URL` | `http://host.docker.internal:11434/v1` для Ollama, иначе endpoint провайдера |
| `GROQ_MODEL` | `qwen2.5:3b` в текущем демо, либо модель Groq/DeepSeek |
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
- В демо используется локальная Qwen2.5 3B через Ollama в Docker: внешние
  Groq/DeepSeek/Gemini недоступны или без баланса из региона. Код поддерживает
  их без изменений: достаточно поменять `GROQ_API_URL`/`GROQ_MODEL`/ключ.
- GitHub PAT в демо имеет scope `public_repo`; для приватного репозитория
  нужен `repo`.

## Стек

Python 3.10+, FastAPI, uvicorn, httpx, python-dotenv, pytest, ruff,
OpenAI-compatible LLM API (Groq/DeepSeek) или локальная Ollama/Qwen2.5,
GitHub REST API, Docker.
