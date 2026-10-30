# 🛡️ Compliance Sentinel

[![tests](https://img.shields.io/badge/tests-41%20passed-brightgreen)](#тесты)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](#требования)
[![fastapi](https://img.shields.io/badge/FastAPI-async-009688)](https://fastapi.tiangolo.com/)
[![llm](https://img.shields.io/badge/LLM-local%20Qwen2.5%203B-7c3aed)](#)
[![docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](#быстрый-старт)


> **SecureMerge** — репозиторий проекта; **Compliance Sentinel** — имя
> GitHub-бота. Для сдачи и демо см. [SUBMISSION.md](SUBMISSION.md).

AI-бот для GitHub, который перехватывает Pull Request, анализирует `git diff`
с помощью LLM (в live-демо — локальная Qwen2.5 3B; поддерживаются Groq и
DeepSeek) и **блокирует слияние**, если находит критический риск:

- захардкоженные пароли, токены, ключи и строки подключения;
- утечку/небезопасную обработку персональных данных (PII);
- зависимости с вирусными (copyleft) лицензиями, в первую очередь GPL/AGPL;
- очевидно опасные уязвимости: SQL/command injection, path traversal, SSRF,
  небезопасную десериализацию, отключённый TLS и т. п.

Бот работает по событиям `pull_request` (`opened` и `synchronize`), пишет
комментарий с причиной и выставляет commit status `failure`/`success`.
При включённом branch protection кнопка **Merge** блокируется на `failure`.

> **Принцип fail-closed:** если GitHub или LLM-провайдер недоступны, diff
> слишком большой или LLM вернула некорректный JSON, PR блокируется для
> ручной проверки. Compliance-бот не должен пропускать риск из-за сбоя
> зависимости.
>
> Бот работает с любым OpenAI-совместимым чат-API. Если Groq недоступен в
> вашем регионе (из РФ он отдаёт Cloudflare `403`), см. раздел
> [DeepSeek как альтернатива](#deepseek-как-альтернатива-если-groq-недоступен).

---

## Демо

| Что | Где |
| --- | --- |
| Открытый PR, который бот заблокировал | [PR #3](https://github.com/Horda23/SecureMerge/pull/3) |
| Live-демо | ссылка **Website** в блоке About этого репозитория (обновляется автоматически) |
| Release для сдачи | [v1.0.1](https://github.com/Horda23/SecureMerge/releases/tag/v1.0.2) |
| Чек-лист жюри | [SUBMISSION.md](SUBMISSION.md) |

> В live-демо используется локальная Qwen2.5 3B через Ollama в Docker —
> без внешних API и биллинга. Проект также поддерживает Groq, DeepSeek
> и любой OpenAI-совместимый endpoint.

## Содержание

1. [Как это работает](#как-это-работает)
2. [Структура проекта](#структура-проекта)
3. [Требования](#требования)
4. [Быстрый старт](#быстрый-старт)
5. [Получение токенов](#получение-токенов)
6. [Настройка `.env`](#настройка-env)
7. [Запуск сервера](#запуск-сервера)
8. [Проксирование через ngrok](#проксирование-через-ngrok)
9. [Настройка вебхука в GitHub](#настройка-вебхука-в-github)
10. [Branch protection: как реально заблокировать Merge](#branch-protection-как-реально-заблокировать-merge)
11. [Проверка на демо-PR](#проверка-на-демо-pr)
12. [Тесты](#тесты)
13. [Диагностика проблем](#диагностика-проблем)
14. [Безопасность и ограничения](#безопасность-и-ограничения)
15. [Что дальше](#что-дальше)

---

## Как это работает

```text
GitHub Pull Request
       │  webhook: opened / synchronize
       ▼
POST /webhook  ──► проверка HMAC SHA-256 по сырому телу
       │             (неверная подпись → 401)
       ▼
   202 Accepted, аудит уходит в BackgroundTasks
       │
       ├─► GET /repos/{owner}/{repo}/pulls/{n}
       │      Accept: application/vnd.github.v3.diff
       ▼
   ai_analyzer.py ──► LLM Chat Completions (OpenAI-совместимый API:
       │              Groq по умолчанию, DeepSeek — альтернатива)
       │              response_format=json_object
       ▼
   строгий JSON: {"status": "BLOCK"|"PASS", "reason": "..."}
       │
       ├─ BLOCK ──► комментарий в PR + commit status failure
       └─ PASS  ──► commit status success

Branch protection с обязательной проверкой "Compliance Sentinel"
       └─► Merge заблокирован, пока status != success
```

| Результат | Комментарий в PR | Commit status | Кнопка Merge |
|---|---|---|---|
| `BLOCK` | да, с причиной на русском | `failure` | заблокирована* |
| `PASS` | нет | `success` | доступна* |
| Сбой анализа | да, с пояснением про ручную проверку | `failure` | заблокирована* |

\* При условии, что в branch protection включены обязательные status checks
и выбран контекст `Compliance Sentinel`. Об этом — отдельный раздел ниже.

---

## Структура проекта

```text
.
├── main.py                 # FastAPI, /webhook, HMAC, фоновая оркестрация, логика вердикта
├── ai_analyzer.py          # системный промпт, запрос к LLM, ретраи, строгий парсинг JSON
├── github_actions.py       # комментарий в PR и Commit Status API
├── github_api.py           # общий async-слой GitHub REST API, валидация owner/repo/SHA
├── config.py               # чтение .env, валидация и кэширование настроек
├── requirements.txt        # runtime-зависимости
├── requirements-dev.txt    # pytest + ruff для локальных проверок
├── .env.example            # пример переменных окружения (без секретов)
├── Dockerfile              # контейнер для Render/Docker
├── docker-compose.yml      # локальный запуск + опциональный Cloudflare-туннель
├── render.yaml             # Blueprint для деплоя без локального сервера
├── start.ps1 / start.sh    # запуск одной командой
├── SUBMISSION.md           # краткий чек-лист для жюри
├── pytest.ini
└── tests/
    ├── test_webhook.py             # подпись, события, лимиты тела, постановка аудита в фон
    ├── test_ai_analyzer.py         # парсинг JSON, fail-closed, ретраи
    ├── test_github_actions.py      # комментарий и commit status
    ├── test_comment_sanitization.py # маскирование секретов и Markdown
    └── test_audit_flow.py          # полный BLOCK/PASS/сбой без реальной сети
```

Стек: Python 3.10+, FastAPI, uvicorn, httpx (асинхронные запросы к GitHub и
LLM через OpenAI-совместимый REST API), python-dotenv, Docker, GitHub Actions.
Провайдер LLM: Groq (`llama-3.3-70b-versatile` в `.env.example`) или DeepSeek
(`deepseek-flash` в текущем демо).

---

## Требования

- Python **3.10+** (проверено на 3.12);
- аккаунт GitHub и права администратора репозитория (для создания вебхука);
- токен GitHub PAT;
- API-ключ LLM-провайдера: Groq или DeepSeek (см. ниже);
- `ngrok` или другой публичный HTTPS-туннель — GitHub должен достучаться
  до локального сервера.

Проверьте версию Python:

```bash
python --version
```

---

## Быстрый старт

### Запуск одной командой / без локального сервера

Для жюри и пользователей, которым не хочется открывать несколько терминалов:

**Windows — один скрипт:**

```powershell
.\start.ps1
```

Скрипт сам создаст `.env` (если его нет), виртуальное окружение, поставит
зависимости, проверит конфиг и запустит uvicorn.

**Docker Compose + публичный туннель:**

```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
# заполнить три обязательные переменные
docker compose --profile tunnel up --build
```

В логах контейнера `tunnel` появится `https://*.trycloudflare.com` — это
публичный URL для GitHub webhook.

**Render (без терминала и без ngrok):** в репозитории есть
[`render.yaml`](render.yaml) и [`Dockerfile`](Dockerfile). В Render:
**New → Blueprint → выбрать репозиторий** → задать секреты в dashboard →
Deploy. Сервис получит постоянный HTTPS URL.

Подробный чек-лист — в [SUBMISSION.md](SUBMISSION.md).

### Ручной локальный запуск

Если нужен полный контроль над окружением:

#### 1. Установить зависимости

**Windows (PowerShell):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Если PowerShell запрещает активацию:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 2. Создать `.env`

**Windows:**

```powershell
Copy-Item .env.example .env
```

**Linux / macOS:**

```bash
cp .env.example .env
```

> `.env.example` — публичный шаблон. Не вписывайте в него реальные токены и
> ключи: все секреты должны быть только в `.env` (он исключён из Git).

#### 3. Сгенерировать секрет вебхука

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Скопируйте результат в `GITHUB_WEBHOOK_SECRET` в `.env`. Этот же секрет
нужно будет указать в настройках вебхука в GitHub.

#### 4. Заполнить `.env`

Минимум три обязательные переменные:

```dotenv
GITHUB_TOKEN=ghp_...
GITHUB_WEBHOOK_SECRET=<сгенерированная строка>
GROQ_API_KEY=sk-...   # ключ DeepSeek; для Groq — gsk_...
GROQ_API_URL=https://api.deepseek.com
GROQ_MODEL=deepseek-flash
```

`STATUS_CONTEXT` можно оставить `Compliance Sentinel` — именно эту строку
нужно будет выбрать в branch protection.

#### 5. Запустить сервер и проверить health

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

В другом терминале:

```bash
curl -s http://127.0.0.1:8000/health
```

> В Windows PowerShell `curl` может быть алиасом `Invoke-WebRequest`; там
> используйте `curl.exe -s http://127.0.0.1:8000/health`.

Ожидаемый ответ:

```json
{"status":"ok","service":"compliance-sentinel","version":"1.0.0"}
```

Swagger: `http://127.0.0.1:8000/docs`.

#### 6. Поднять ngrok и настроить вебхук

См. разделы [Проксирование через ngrok](#проксирование-через-ngrok) и
[Настройка вебхука в GitHub](#настройка-вебхука-в-github).

#### 7. Включить branch protection

Без этого шага commit status `failure` сам по себе **не блокирует** Merge.
См. [Branch protection](#branch-protection-как-реально-заблокировать-merge).

---

## Получение токенов

### GitHub Personal Access Token (PAT)

#### Вариант A: classic token (проще для хакатона)

1. Откройте <https://github.com/settings/tokens>.
2. **Generate new token (classic)**.
3. Note: `compliance-sentinel`.
4. Выберите expiration (например, 30 дней).
5. Отметьте scope:
   - `repo` — для приватных репозиториев (включает PR и commit statuses);
   - `public_repo` — если нужны только публичные репозитории.
6. **Generate token** и сразу скопируйте `ghp_...` — второй раз токен
   не покажут.
7. Вставьте в `.env` → `GITHUB_TOKEN`.

Если репозиторий в организации, включён SSO и токен нужно авторизовать
для этой организации в настройках токена.

#### Вариант B: fine-grained token (аккуратнее по правам)

1. Откройте <https://github.com/settings/personal-access-tokens>.
2. **Generate new token** → выберите владельца и нужный репозиторий
   (или все репозитории).
3. Repository permissions:
   - **Contents** — Read-only (читать diff);
   - **Pull requests** — Read and write (комментарии);
   - **Commit statuses** — Read and write (блокировка Merge);
   - **Metadata** — Read-only (обязательное право).
4. Сгенерируйте и вставьте токен в `.env`.

Токен должен иметь доступ именно к тому репозиторию, на котором висит
вебхук. Для проверки прав после запуска посмотрите логи: ошибки `403`
означают, что permissions не хватает.

### Groq API Key

1. Откройте <https://console.groq.com/keys>.
2. Войдите/зарегистрируйтесь.
3. **Create API Key**, скопируйте `gsk_...`.
4. Вставьте в `.env` → `GROQ_API_KEY`.

Модель `llama-3.3-70b-versatile` должна быть доступна аккаунту. Если Groq
сообщает, что модели больше нет, укажите актуальную модель в `GROQ_MODEL`
(список — в консоли Groq).

> Бесплатный тариф Groq имеет rate limits. Бот корректно обрабатывает
> `429`: делает до `GROQ_MAX_RETRIES` повторов, затем fail-closed.

### DeepSeek как альтернатива (если Groq недоступен)

Из некоторых регионов (в частности, из РФ) Groq блокируется на уровне
Cloudflare: и `console.groq.com`, и `api.groq.com` отвечают
`{"error":{"message":"Forbidden"}}` с HTTP `403`. Это не ошибка ключа —
сеть просто не пускает к сервису.

Проект не привязан к Groq жёстко: `ai_analyzer.py` отправляет запросы на
любой OpenAI-совместимый `/chat/completions`. Для DeepSeek:

1. Зарегистрируйтесь на <https://platform.deepseek.com/>
   (для API может понадобиться пополнить баланс).
2. Создайте API-ключ в разделе **API keys** — он начинается с `sk-...`.
3. В `.env` укажите:

```dotenv
GROQ_API_KEY=sk-...
GROQ_API_URL=https://api.deepseek.com
GROQ_MODEL=deepseek-flash
```

4. Перезапустите uvicorn.

Имена переменных `GROQ_*` оставлены для совместимости с исходным ТЗ и
кодом; фактически это настройки любого OpenAI-совместимого провайдера.
DeepSeek поддерживает JSON mode, который использует проект.

> Если по правилам хакатона обязателен именно Groq, альтернативный
> провайдер не заменит требование. В этом случае запросите у организаторов
> рабочий доступ/прокси или запускайте демо из сети, где Groq доступен.
> Обход региональных ограничений может нарушать ToS сервиса.

---

## Настройка `.env`

| Переменная | Обязательна | По умолчанию | Назначение |
|---|---:|---|---|
| `GITHUB_TOKEN` | да | — | PAT для GitHub API |
| `GITHUB_WEBHOOK_SECRET` | да | — | секрет HMAC для вебхука, ≥ 16 символов |
| `GROQ_API_KEY` | да | — | ключ LLM-провайдера (Groq `gsk_...` или DeepSeek `sk-...`) |
| `GITHUB_API_URL` | нет | `https://api.github.com` | для GitHub Enterprise |
| `GROQ_API_URL` | нет | `https://api.groq.com/openai/v1/chat/completions` | OpenAI-совместимый endpoint; можно указать базовый `.../v1`; для DeepSeek — `https://api.deepseek.com` |
| `GROQ_MODEL` | нет | `llama-3.3-70b-versatile` | модель; для DeepSeek — `deepseek-flash` |
| `STATUS_CONTEXT` | нет | `Compliance Sentinel` | имя проверки в GitHub; ≤ 100 символов |
| `MAX_DIFF_CHARS` | нет | `80000` | больше — fail-closed без вызова LLM |
| `HTTP_TIMEOUT_SECONDS` | нет | `30` | таймаут GitHub API |
| `GROQ_TIMEOUT_SECONDS` | нет | `90` | таймаут LLM API |
| `GROQ_MAX_RETRIES` | нет | `2` | повторы при 429/5xx/сетевых ошибках |
| `MAX_CONCURRENT_AUDITS` | нет | `4` | лимит одновременных аудитов |
| `PUBLIC_BASE_URL` | нет | пусто | ссылка Details в commit status (обычно ngrok URL) |
| `ALLOWED_REPOSITORIES` | нет | пусто | allow-list `owner/repo` через запятую; пусто = все |
| `LOG_LEVEL` | нет | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |

`PUBLIC_BASE_URL` можно заполнить после запуска ngrok (например,
`https://abcd-1234.ngrok-free.app`) и перезапустить сервер. Это не влияет
на блокировку — только на ссылку «Details» у проверки.

---

## Запуск сервера

Из корня проекта, с активированным venv:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Для «боевого» локального запуска без автоперезагрузки:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Альтернатива:

```bash
python main.py
```

При старте приложение валидирует `.env`. Если чего-то не хватает, в логе
будет понятное сообщение и процесс завершится — это лучше, чем принять
вебхук и молча ничего не проверить.

Проверка:

```bash
curl -s http://127.0.0.1:8000/health
```

В PowerShell используйте `curl.exe` (см. пояснение выше).

Логи должны содержать строку вида:

```text
Compliance Sentinel v1.0.0 запущен. Модель: deepseek-flash; ...
```

---

## Проксирование через ngrok

GitHub не может отправить вебхук на `localhost`. Нужен публичный HTTPS URL.

### 1. Установить ngrok

Скачайте с <https://ngrok.com/download> или через пакетный менеджер.
Зарегистрируйтесь и скопируйте authtoken из личного кабинета.

```bash
ngrok config add-authtoken <ВАШ_NGROK_AUTHTOKEN>
```

### 2. Запустить туннель

```bash
ngrok http 8000
```

В выводе будет строка:

```text
Forwarding  https://abcd-1234.ngrok-free.app -> http://localhost:8000
```

### 3. Указать URL

- Публичный URL: `https://abcd-1234.ngrok-free.app`
- URL вебхука для GitHub: `https://abcd-1234.ngrok-free.app/webhook`
- Опционально добавьте в `.env`:
  `PUBLIC_BASE_URL=https://abcd-1234.ngrok-free.app` и перезапустите сервер.

> На бесплатном тарифе ngrok URL меняется при каждом перезапуске туннеля.
> После смены URL обновите Payload URL в настройках вебхука GitHub. Удобнее
> завести бесплатный static domain в ngrok Dashboard; актуальный синтаксис
> запуска (`--url`/`--domain`) указан там же.

---

## Настройка вебхука в GitHub

1. Откройте репозиторий → **Settings** → **Webhooks** → **Add webhook**.
2. **Payload URL**: `https://<ваш-ngrok-домен>/webhook`
3. **Content type**: `application/json` (обязательно; не
   `application/x-www-form-urlencoded`).
4. **Secret**: вставьте значение `GITHUB_WEBHOOK_SECRET` из `.env`
   (символ в символ).
5. **SSL verification**: **Enable SSL verification**.
6. **Which events would you like to trigger this webhook?**
   - выберите **Let me select individual events**;
   - отметьте только **Pull requests**;
   - снимите остальные галочки, включая Push.
7. **Active** должен быть включён.
8. **Add webhook**.

GitHub сразу отправит `ping`. Откройте **Recent Deliveries**:

- Request: `ping`;
- Response: HTTP **202**, тело `{"status":"pong",...}`;
- если 401 — Secret не совпадает с `.env`; поправьте и переотправьте ping
  кнопкой **Redeliver**.

Бот реагирует только на:

- `pull_request` → `opened`;
- `pull_request` → `synchronize` (новый коммит в PR).

Остальные события/действия возвращают `202` со статусом `ignored`.

---

## Branch protection: как реально заблокировать Merge

Commit status — это только отметка. GitHub запрещает Merge по ней лишь
тогда, когда статус указан как **обязательная проверка** в защите ветки.

> На GitHub Free branch protection (и repository rulesets) доступны только
> для **публичных** репозиториев. Для приватного репозитория нужен GitHub
> Pro/Team, иначе GitHub вернёт `403: Upgrade to GitHub Pro or make this
> repository public`. Демо-репозиторий проекта публичный.

### Classic branch protection

1. Репозиторий → **Settings** → **Branches**.
2. **Add branch protection rule** (или измените существующее правило).
3. **Branch name pattern**: `main` (или ваша основная ветка).
4. Включите **Require status checks to pass before merging**.
5. В поиске статусов выберите **`Compliance Sentinel`** — ровно тот
   контекст, который задан в `STATUS_CONTEXT`.
6. При необходимости включите **Require branches to be up to date before
   merging**.
7. **Save changes**.

> Если вы администратор репозитория, GitHub по умолчанию может разрешать
> обход правил. Для честной демонстрации включите **Do not allow bypassing
> the above settings** (classic protection) или не назначайте bypass-список
> (rulesets) и проверяйте Merge с аккаунта без прав администратора.

Если списка проверок ещё нет, сначала откройте тестовый PR — после первого
прогона контекст появится в поиске (GitHub показывает недавно
запускавшиеся проверки). После этого добавьте его и сохраните.

### Repository rulesets (новый интерфейс)

Settings → Rules → Rulesets → **New ruleset** → **New branch ruleset**:

- target branch `main`;
- включите **Require status checks to pass**;
- добавьте проверку `Compliance Sentinel`;
- enforcement: **Active**;
- сохраните.

Проверка появится на странице PR во вкладке **Checks / Conversation**.
При `failure` кнопка Merge будет недоступна с сообщением о неуспешной
обязательной проверке.

> Важно: для PR из fork'ов GitHub может требовать отдельного разрешения на
> запуск. Если бот не получил событие, проверьте Recent Deliveries.

---

## Проверка на демо-PR

Создайте ветку и добавьте файл с **заведомо фейковым** «секретом»:

```bash
git checkout -b demo/compliance-sentinel
```

`demo_secret.py`:

```python
# Только для демонстрации Compliance Sentinel. Не настоящий ключ.
DATABASE_PASSWORD = "demo-password-do-not-use-123"
API_TOKEN = "demo-token-do-not-use-abc"
```

```bash
git add demo_secret.py
git commit -m "test: add demo credentials for compliance check"
git push -u origin demo/compliance-sentinel
```

Откройте Pull Request в `main`. Через несколько секунд:

- в PR появится комментарий **«Compliance Sentinel: проверка не пройдена»**
  с причиной;
- на вкладке Checks/статусов будет `failure` с контекстом
  `Compliance Sentinel`;
- кнопка Merge станет недоступна, если branch protection настроен (для
  аккаунта без права bypass);
- в Recent Deliveries вебхука будет ответ `202 accepted`.

Затем удалите секреты, закоммитьте и запушьте снова — GitHub пришлёт
`synchronize`, бот выполнит новую проверку и выставит `success`.

Не используйте реальные токены в демо. Если в репозитории включён GitHub
Secret Scanning / Push Protection, он может заблокировать сам push с
похожей на секрет строкой — используйте явно демонстрационные значения.

### Готовый демо-PR этой сдачи

- Репозиторий: <https://github.com/Horda23/SecureMerge>
- PR с демонстрационным «секретом»: <https://github.com/Horda23/SecureMerge/pull/3>
- В PR есть реальный AI-комментарий с найденным секретом и проваленный
  статус `Compliance Sentinel`; branch protection блокирует Merge.
- Живое демо использует локальную Qwen2.5 3B через Ollama в Docker.
- Более короткий чек-лист для жюри: [SUBMISSION.md](SUBMISSION.md).

---

## Тесты

Тесты не ходят в сеть: GitHub и LLM заменены на `httpx.MockTransport`.

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

Ожидаемо: **41 passed** и `All checks passed!`. Покрыты:

- HMAC-подпись и отклонение подделок (включая не-ASCII заголовок);
- ping, неподдерживаемые события, `opened`/`synchronize`;
- потоковый лимит тела: Content-Length и chunked-запросы без него;
- парсинг JSON в markdown-ограждениях и с мусором вокруг;
- fail-closed при кривом ответе LLM, 401 LLM API, превышении размера diff;
- ретрай `429`;
- payload комментария и commit status, ограничения длины;
- редактирование известных форматов секретов в комментарии и защита
  Markdown/HTML/@-упоминаний;
- полный BLOCK/PASS-цикл, сбой получения diff и аномальный не-diff 200.

---

## Диагностика проблем

| Симптом | Вероятная причина | Что делать |
|---|---|---|
| Процесс не стартует, `ConfigurationError` | не заполнен `.env` | заполните обязательные переменные, перезапустите uvicorn |
| Вебхук отвечает `401` | `GITHUB_WEBHOOK_SECRET` не совпадает с Secret в GitHub | сверьте значение, переотправьте ping |
| Вебхук отвечает `400` | пришёл не JSON / нет полей PR | Content type должен быть `application/json`, событие — Pull requests |
| `202 accepted`, но нет комментария/статуса | фоновая ошибка, не хватает прав токена, LLM недоступен | смотрите логи uvicorn; проверьте права PAT и `GROQ_API_KEY` |
| `403 Resource not accessible by integration` | токену не хватает Pull requests / Commit statuses | выдайте права и обновите `GITHUB_TOKEN` |
| `404` при получении diff | нет доступа к репозиторию / Contents: Read | проверьте доступ токена и имя репозитория |
| LLM `401` | неверный/отозванный ключ | создайте новый ключ и обновите `GROQ_API_KEY` |
| LLM `402` | недостаточно средств на балансе провайдера | пополните баланс или используйте локальную LLM |
| LLM `400` или `404` | модель недоступна/неверный `GROQ_MODEL` | укажите актуальную модель провайдера |
| LLM `429` | rate limit | уменьшите нагрузку, увеличьте `GROQ_MAX_RETRIES`, проверьте лимиты тарифа |
| Groq отдаёт `403 Forbidden` | региональная блокировка (например, из РФ) | используйте DeepSeek/локальную LLM или доступ от организаторов; ключ не поможет |
| Merge не блокируется | branch protection не требует `Compliance Sentinel` | включите required status checks и выберите нужный контекст |
| Проверки `Compliance Sentinel` нет в списке branch protection | бот ещё не отправил ни одного статуса | откройте тестовый PR, дождитесь статуса, затем добавьте проверку |
| «diff превышает лимит» | PR больше `MAX_DIFF_CHARS` | увеличьте лимит или разбейте PR; сейчас сработал fail-closed |
| ngrok отдаёт `502` | uvicorn не запущен/неверный порт | проверьте `http://127.0.0.1:8000/health`, перезапустите туннель |
| После перезапуска ngrok вебхуки не приходят | сменился публичный URL | обновите Payload URL в GitHub |

Полезные места:

- GitHub: **Settings → Webhooks → Recent Deliveries** — запрос и ответ;
- локально: `http://127.0.0.1:8000/docs` — Swagger;
- логи uvicorn содержат `delivery=<id>`, `repo#pr`, `commit` и вердикт.

---

## Безопасность и ограничения

### Что сделано для безопасности

- HMAC SHA-256 проверяется по **сырому телу** запроса в постоянном времени;
- без валидной подписи запрос отклоняется до разбора JSON;
- имя репозитория и SHA валидируются, чтобы нельзя было собрать опасный URL;
- тело webhook читается потоково с жёстким лимитом 2 МБ (защита и от
  Content-Length, и от chunked-запросов без него);
- опциональный allow-list репозиториев (`ALLOWED_REPOSITORIES`);
- секреты не логируются и не возвращаются в HTTP-ответах;
- текст LLM экранируется перед вставкой в GitHub Markdown; известные форматы
  токенов маскируются, а reason LLM не попадает в commit status — только в
  комментарий PR;
- проверяется, что 200-ответ GitHub действительно является unified diff;
- fail-closed при любом сбое анализа;
- таймауты и ограничение параллелизма;
- секреты читаются только из окружения, `.env` в `.gitignore`.

### Ограничения MVP

- **LLM не заменяет SAST/secret scanning/license scanner и юридическую
  проверку.** Возможны ложные срабатывания и пропуски.
- Анализируется только diff, без полного контекста репозитория и истории.
- GPL/AGPL-детект ограничен тем, что видно в diff; лицензия проекта боту
  неизвестна.
- Diff больше `MAX_DIFF_CHARS` не анализируется — сразу BLOCK.
- GitHub REST API может не отдать diff очень крупных PR; сработает fail-closed.
- `BackgroundTasks` живут в памяти процесса: при рестарте незавершённый
  аудит потеряется. Для production нужна очередь.
- Нет дедупликации по `X-GitHub-Delivery`: при ручном Redeliver бот выполнит
  проверку повторно (комментарий может появиться дважды).
- PAT — долгоживущий секрет. Для production лучше GitHub App.

### Конфиденциальность

Diff отправляется LLM-провайдеру (третья сторона: Groq, DeepSeek или другой
OpenAI-совместимый сервис). Не подключайте бота к репозиториям с регулируемыми
данными без согласования политики обработки данных и оценки провайдера.
Секреты, попавшие в diff, уйдут в LLM — именно поэтому бот и должен ловить их
до Merge, но для реальных утечек нужна немедленная ротация ключа.

Модели запрещено цитировать значения секретов/PII в reason, а известные
паттерны токенов дополнительно маскируются перед публикацией. Это снижает
риск, но не даёт 100% гарантии для произвольных форматов секретов: не
публикуйте комментарий бота в публичный репозиторий, если diff может
содержать реальные credentials, без предварительной ротации.

---

## Что дальше

Идеи для развития после хакатона:

- GitHub App вместо PAT и Checks API вместо Commit Status;
- очередь (Celery/RQ/Arq) + персистентный журнал аудитов;
- ретраи/backoff для GitHub REST API (сейчас ретраи есть только у LLM);
- дедупликация по `X-GitHub-Delivery` и идемпотентные комментарии;
- анализ не только diff, но и манифестов зависимостей, lock-файлов,
  лицензий проекта;
- блокировка по отдельным policy-правилам (secrets/PII/license) с
  конфигурацией severity;
- self-hosted модель/LLM для чувствительных репозиториев;
- метрики Prometheus, алерты, ретраи публикации статуса.

---

## API сервиса

| Метод | Path | Назначение |
|---|---|---|
| GET | `/` | краткая информация |
| GET | `/health` | healthcheck |
| POST | `/webhook` | приём GitHub webhook, ответ `202` |
| GET | `/docs` | Swagger UI |
