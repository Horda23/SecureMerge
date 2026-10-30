#!/usr/bin/env bash
# One-command Docker launcher for Compliance Sentinel.
# Checks Docker, validates .env, starts local Ollama if needed, starts the app
# and prints the GitHub webhook / branch-protection checklist.

set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n==> %s\n' "$1"; }
ok() { printf '  [ok] %s\n' "$1"; }
warn() { printf '  [!]  %s\n' "$1"; }
die() {
    printf '\nОШИБКА: %s\n' "$1" >&2
    shift || true
    for line in "$@"; do printf '  %s\n' "$line" >&2; done
    printf '\n' >&2
    exit 1
}

env_value() {
    local name="$1"
    [ -f .env ] || return 0
    local line
    line="$(grep -E "^[[:space:]]*${name}[[:space:]]*=" .env | tail -n 1 || true)"
    [ -n "$line" ] || return 0
    printf '%s' "${line#*=}" | tr -d '\r' | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

printf 'Compliance Sentinel — запуск в один клик\n'

step "Проверяю Docker"
command -v docker >/dev/null 2>&1 || die "Docker не найден в PATH." \
    "Установите Docker Desktop: https://www.docker.com/products/docker-desktop/" \
    "Перезапустите терминал после установки."
docker info >/dev/null 2>&1 || die "Docker установлен, но движок не запущен." \
    "Запустите Docker Desktop и дождитесь статуса Running." \
    "Затем повторите: ./start-all.sh"
docker compose version >/dev/null 2>&1 || die "Не найден Docker Compose v2." \
    "Обновите Docker Desktop или установите плагин docker-compose-plugin."
ok "Docker работает"

step "Проверяю .env"
if [ ! -f .env ]; then
    cp .env.example .env
    die "Создан .env, но его нужно заполнить." \
        "Откройте .env и заполните:" \
        "  GITHUB_TOKEN           — https://github.com/settings/tokens" \
        "  GITHUB_WEBHOOK_SECRET  — случайная строка >= 16 символов" \
        "  LLM_API_KEY            — для локальной Ollama подойдёт ollama" \
        "Затем снова запустите: ./start-all.sh"
fi

missing=()
[ -n "$(env_value GITHUB_TOKEN)" ] || missing+=("GITHUB_TOKEN")
[ -n "$(env_value GITHUB_WEBHOOK_SECRET)" ] || missing+=("GITHUB_WEBHOOK_SECRET")
[ -n "$(env_value LLM_API_KEY)" ] || missing+=("LLM_API_KEY")
if [ "${#missing[@]}" -gt 0 ]; then
    die "В .env не заполнено: ${missing[*]}" \
        "GITHUB_TOKEN: https://github.com/settings/tokens (scope repo)" \
        "GITHUB_WEBHOOK_SECRET: python -c 'import secrets; print(secrets.token_hex(32))'" \
        "LLM_API_KEY: для локальной Ollama укажите ollama" \
        "После заполнения повторите: ./start-all.sh"
fi
ok ".env заполнен"

model="$(env_value LLM_MODEL)"
[ -n "$model" ] || model="qwen2.5:3b"
llm_url="$(env_value LLM_API_URL)"
local_llm=false
case "$llm_url" in
    *host.docker.internal*|*127.0.0.1*|*localhost*|*ollama*) local_llm=true ;;
esac

if [ "$local_llm" = true ]; then
    step "Проверяю локальную Ollama"
    if ! docker ps -a --filter 'name=compliance-ollama' --format '{{.Names}}' | grep -qx 'compliance-ollama'; then
        printf '  Создаю контейнер Ollama (первый запуск может занять время)...\n'
        docker run -d --name compliance-ollama --restart unless-stopped \
            -p 11434:11434 -v compliance_ollama:/root/.ollama ollama/ollama >/dev/null \
            || die "Не удалось создать контейнер Ollama." "Проверьте, что порт 11434 свободен."
    elif ! docker ps --filter 'name=compliance-ollama' --format '{{.Names}}' | grep -qx 'compliance-ollama'; then
        docker start compliance-ollama >/dev/null
    fi

    if ! docker exec compliance-ollama ollama list 2>/dev/null | grep -Fq "$model"; then
        printf '  Загружаю модель %s (может занять несколько минут)...\n' "$model"
        docker exec compliance-ollama ollama pull "$model" \
            || die "Не удалось загрузить модель $model." "Проверьте интернет и свободное место."
    fi
    ok "Ollama и модель $model готовы"
else
    warn "LLM_API_URL указывает на внешний провайдер — Ollama не запускаю."
fi

step "Собираю и запускаю приложение"
mkdir -p logs
compose_log="logs/start-all-compose.log"
docker compose up -d --build >"$compose_log" 2>&1 || {
    printf '  Последние строки лога сборки:\n' >&2
    tail -n 60 "$compose_log" >&2 || true
    die "docker compose up завершился с ошибкой." \
        "Проверьте Docker Desktop и выполните: docker compose logs --tail 50 app"
}

step "Жду готовности приложения"
healthy=false
i=0
while [ "$i" -lt 60 ]; do
    if curl -fsS --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        healthy=true
        break
    fi
    i=$((i + 1))
    sleep 2
done
if [ "$healthy" != true ]; then
    warn "Приложение не ответило на /health за 120 секунд."
    docker compose logs --tail 60 app || true
    die "Не удалось проверить здоровье приложения." \
        "Скорее всего, неверный .env. Исправьте и повторите запуск." \
        "Логи: docker compose logs --tail 100 app"
fi
ok "Приложение работает"

public="$(env_value PUBLIC_BASE_URL)"
if [ -n "$public" ]; then
    base="${public%/}"
else
    base="http://127.0.0.1:8000"
fi
webhook_url="${base}/webhook"

printf '\n'
printf '============================================================\n'
printf ' Compliance Sentinel запущен\n'
printf '============================================================\n\n'
printf '  Health:   http://127.0.0.1:8000/health\n'
printf '  Swagger:  http://127.0.0.1:8000/docs\n'
printf '  Webhook:  %s\n\n' "$webhook_url"
printf 'Следующие шаги в GitHub:\n'
printf '  1. Settings -> Webhooks -> Add webhook\n'
printf '     Payload URL: %s\n' "$webhook_url"
printf '     Content type: application/json\n'
printf '     Secret: значение GITHUB_WEBHOOK_SECRET из .env\n'
printf '     Events: Pull requests\n'
printf '  2. Settings -> Branches -> Branch protection для main\n'
printf '     Включите Require status checks и добавьте "Compliance Sentinel".\n\n'
if [ -z "$public" ]; then
    warn "PUBLIC_BASE_URL пуст: GitHub не сможет достучаться до localhost."
    printf '  Для реального вебхука нужен публичный URL (туннель или Render).\n'
    printf '  Укажите его в .env как PUBLIC_BASE_URL и перезапустите: docker compose up -d\n\n'
fi
printf 'Остановка: docker compose down\n'
