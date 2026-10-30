# SecureMerge / Compliance Sentinel — submission

## 30-секундный питч

Compliance Sentinel — GitHub-бот, который проверяет Pull Request до слияния.
Он получает diff, анализирует его LLM-моделью и, если находит захардкоженный
секрет, PII, GPL-зависимость или опасную уязвимость, публикует объяснение и
ставит обязательный статус `failure`. Merge блокируется branch protection.
Если проверка не может быть выполнена — срабатывает fail-closed: PR тоже
блокируется.

## Проблема

- Секреты и опасные изменения проскакивают в ревью из-за усталости человека.
- Комментарии ревьюеров не создают жёсткого барьера для Merge.
- Готовые сканеры не объясняют риск простым языком и плохо встроены в PR.
- В regulated-разработке нужен воспроизводимый аудит каждого изменения.

## Решение

1. GitHub webhook на `pull_request` (`opened`/`synchronize`).
2. HMAC SHA-256 по сырому телу; неверная подпись → `401`.
3. `202 Accepted`, аудит уходит в `BackgroundTasks`.
4. `GET /pulls/{n}` с `Accept: application/vnd.github.v3.diff`.
5. LLM анализирует diff и возвращает строгий JSON `BLOCK`/`PASS`.
6. При `BLOCK` — комментарий с причиной и commit status `failure`.
7. Branch protection с обязательной проверкой `Compliance Sentinel`
   не даёт нажать Merge.
8. При исправлении и новом коммите бот проверяет заново; `PASS` ставит
   `success` и разблокирует Merge.

## Демо

- Репозиторий: <https://github.com/Horda23/SecureMerge>
- Заблокированный PR: <https://github.com/Horda23/SecureMerge/pull/3>
- Release: <https://github.com/Horda23/SecureMerge/releases/tag/v1.0.4>
- Live-демо: ссылка **Website** в блоке About репозитория.
- README: <https://github.com/Horda23/SecureMerge/blob/main/README.md>

## Стек

- Python 3.10+, FastAPI, uvicorn, httpx, pydantic, python-dotenv.
- GitHub REST API: diff, issue comments, commit statuses.
- LLM: локальная Qwen2.5 3B через Ollama; поддерживается любой
  OpenAI-совместимый endpoint.
- Docker / Docker Compose; pytest; ruff.

## Архитектура

```text
GitHub PR --webhook--> FastAPI /webhook
                           | HMAC + allow-list
                           v
                     BackgroundTasks
                           | GitHub REST diff
                           v
                    ai_analyzer.py --> LLM API
                           | строгий JSON
             +-------------+-------------+
             |                           |
          BLOCK                        PASS
             |                           |
   PR comment + status failure     status success
             |                           |
             +---------> branch protection <--------+
                              |
                     Merge blocked/allowed
```

## Безопасность

- HMAC SHA-256 по сырому телу, constant-time сравнение.
- Ограничение размера тела (2 МБ) и diff (`MAX_DIFF_CHARS`).
- Валидация `owner/repo`, PR number и commit SHA.
- Allow-list репозиториев.
- Экранирование HTML, нейтрализация `@`-упоминаний и markdown-ссылок.
- Маскирование известных токенов и типичных секретов в комментарии.
- Секреты не логируются; `.env` исключён из Git и Docker image.
- Fail-closed при любой ошибке LLM/JSON/сети.

## Проверено

| Проверка | Результат |
| --- | --- |
| `pytest -q` | 48 passed |
| `ruff check .` | clean |
| Live webhook ping | `202 OK` |
| Реальный PR | комментарий + `failure` |
| Branch protection | `mergeable_state: blocked` |
| Успешный сценарий | docs-PR получили `PASS`, `success`, merged |
| Секреты в публичном репозитории | отсутствуют |

## Ограничения

- Анализируется только diff, а не весь репозиторий.
- Локальная модель 3B слабее облачных и может ошибаться.
- Большой diff/ответ модели → fail-closed.
- `BackgroundTasks` не переживают рестарт; нет очереди и дедупликации.
- Нет CI-workflow и постоянного облачного хостинга в текущей поставке.
- Бот не заменяет SAST, secret scanning, license scanner и юридическую
  проверку.

## Запуск

```bash
cp .env.example .env
# заполнить GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET, LLM_API_KEY
# Windows: .\start-all.ps1   Linux/macOS: ./start-all.sh
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

Для локальной модели:

```bash
docker run -d --name compliance-ollama --restart unless-stopped \
  -p 11434:11434 -v compliance_ollama:/root/.ollama ollama/ollama
docker exec compliance-ollama ollama pull qwen2.5:3b
```

В `.env`:

```dotenv
LLM_API_KEY=ollama
LLM_API_URL=http://host.docker.internal:11434/v1
LLM_MODEL=qwen2.5:3b
LLM_TIMEOUT_SECONDS=300
LLM_MAX_RETRIES=1
```

## Демо на 2 минуты

1. Открыть PR #3: виден комментарий бота и `failure`.
2. Показать, что Merge заблокирован.
3. Открыть файл `test.py` и показать вымышленный пароль.
4. Исправить/удалить пароль и запушить коммит (или открыть docs-PR).
5. Показать новый `success` и доступный Merge.
6. Показать README, тесты, release и архитектуру.

## Ответы на вопросы

**Почему локальная модель?**
Регион блокирует часть внешних LLM-провайдеров; локальная Qwen2.5 3B делает
демо автономным. Код поддерживает любой OpenAI-совместимый endpoint без
изменений.

**Как блокируется Merge?**
Commit status `failure` + обязательная проверка `Compliance Sentinel` в
branch protection с выключенным admin bypass.

**Что при сбое LLM?**
Fail-closed: PR блокируется, в комментарии объясняется причина.

**Куда уходят данные?**
В текущем демо diff обрабатывается локальной моделью и никуда не отправляется.
При внешнем провайдере данные уходят ему.

**Можно ли использовать в приватном репозитории?**
Да, при PAT с нужными правами и branch protection на платном плане GitHub.

**Что дальше?**
GitHub App вместо PAT, очередь задач, persistent audit log, CI, облачный
хостинг, более сильная модель.
