#Requires -Version 5.1
<#
.SYNOPSIS
  One-command Docker launcher for Compliance Sentinel.

.DESCRIPTION
  Checks Docker, validates .env, starts the local Ollama container if needed,
  builds/starts the app and prints the exact GitHub webhook / branch-protection
  steps. Safe to run repeatedly.

.EXAMPLE
  .\start-all.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "  [ok] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "  [!]  $Message" -ForegroundColor Yellow
}

function Stop-WithHelp([string]$Message, [string[]]$Help) {
    Write-Host ""
    Write-Host "ОШИБКА: $Message" -ForegroundColor Red
    foreach ($line in $Help) { Write-Host "  $line" }
    Write-Host ""
    exit 1
}

function Get-EnvValue([string]$Name) {
    if (-not (Test-Path -LiteralPath ".env")) { return "" }
    $prefix = "$Name="
    $line = Get-Content -LiteralPath ".env" -Encoding UTF8 |
        Where-Object { $_.TrimStart().StartsWith($prefix) } |
        Select-Object -Last 1
    if (-not $line) { return "" }
    return $line.Substring($line.IndexOf("=") + 1).Trim().Trim('"').Trim("'")
}

Write-Host "Compliance Sentinel — запуск в один клик" -ForegroundColor White

Write-Step "Проверяю Docker"
if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
    Stop-WithHelp "Docker не найден в PATH." @(
        "Установите Docker Desktop: https://www.docker.com/products/docker-desktop/",
        "Перезапустите терминал после установки."
    )
}
& docker.exe info *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-WithHelp "Docker установлен, но движок не запущен." @(
        "Запустите Docker Desktop и дождитесь статуса Running.",
        "Затем повторите: .\start-all.ps1"
    )
}
Write-Ok "Docker работает"

Write-Step "Проверяю .env"
if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Stop-WithHelp "Создан .env, но его нужно заполнить." @(
        "Откройте .env в любом редакторе и заполните:",
        "  GITHUB_TOKEN            — https://github.com/settings/tokens",
        "  GITHUB_WEBHOOK_SECRET   — любая случайная строка (>= 16 символов)",
        "  LLM_API_KEY             — для локальной Ollama подойдёт ollama",
        "Затем снова запустите: .\start-all.ps1"
    )
}

$token = Get-EnvValue "GITHUB_TOKEN"
$secret = Get-EnvValue "GITHUB_WEBHOOK_SECRET"
$llmKey = Get-EnvValue "LLM_API_KEY"
$missing = @()
if ([string]::IsNullOrWhiteSpace($token)) { $missing += "GITHUB_TOKEN" }
if ([string]::IsNullOrWhiteSpace($secret)) { $missing += "GITHUB_WEBHOOK_SECRET" }
if ([string]::IsNullOrWhiteSpace($llmKey)) { $missing += "LLM_API_KEY" }
if ($missing.Count -gt 0) {
    Stop-WithHelp ("В .env не заполнено: " + ($missing -join ", ")) @(
        "GITHUB_TOKEN: https://github.com/settings/tokens (scope repo)",
        "GITHUB_WEBHOOK_SECRET: python -c `"import secrets; print(secrets.token_hex(32))`"",
        "LLM_API_KEY: для локальной Ollama укажите ollama",
        "После заполнения повторите: .\start-all.ps1"
    )
}
Write-Ok ".env заполнен"

$model = Get-EnvValue "LLM_MODEL"
if ([string]::IsNullOrWhiteSpace($model)) { $model = "qwen2.5:3b" }
$llmUrl = Get-EnvValue "LLM_API_URL"
$localLlm = $llmUrl -match "host\.docker\.internal|127\.0\.0\.1|localhost|ollama"

if ($localLlm) {
    Write-Step "Проверяю локальную Ollama"
    $names = @(& docker.exe ps -a --filter "name=compliance-ollama" --format "{{.Names}}")
    if (-not ($names -contains "compliance-ollama")) {
        Write-Host "  Создаю контейнер Ollama (первый запуск может занять время)..." -ForegroundColor DarkGray
        & docker.exe run -d --name compliance-ollama --restart unless-stopped `
            -p 11434:11434 -v compliance_ollama:/root/.ollama ollama/ollama *> $null
        if ($LASTEXITCODE -ne 0) {
            Stop-WithHelp "Не удалось создать контейнер Ollama." @(
                "Проверьте, что Docker Desktop запущен и порт 11434 свободен."
            )
        }
    }
    else {
        $running = @(& docker.exe ps --filter "name=compliance-ollama" --format "{{.Names}}")
        if (-not ($running -contains "compliance-ollama")) {
            & docker.exe start compliance-ollama *> $null
        }
    }

    $modelList = @(& docker.exe exec compliance-ollama ollama list 2>$null)
    if (-not ($modelList -match [regex]::Escape($model))) {
        Write-Host "  Загружаю модель $model (может занять несколько минут)..." -ForegroundColor DarkGray
        & docker.exe exec compliance-ollama ollama pull $model
        if ($LASTEXITCODE -ne 0) {
            Stop-WithHelp "Не удалось загрузить модель $model." @(
                "Проверьте интернет и свободное место.",
                "Можно повторить: docker exec compliance-ollama ollama pull $model"
            )
        }
    }
    Write-Ok "Ollama и модель $model готовы"
}
else {
    Write-Warn "LLM_API_URL указывает на внешний провайдер — Ollama не запускаю."
}

Write-Step "Собираю и запускаю приложение"
$logDir = Join-Path $PSScriptRoot "logs"
$null = New-Item -ItemType Directory -Force -Path $logDir
$composeLog = Join-Path $logDir "start-all-compose.log"
& docker.exe compose up -d --build *> $composeLog
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Последние строки лога сборки:" -ForegroundColor DarkGray
    Get-Content -LiteralPath $composeLog -Tail 60 -ErrorAction SilentlyContinue
    Stop-WithHelp "docker compose up завершился с ошибкой." @(
        "Проверьте Docker Desktop и выполните: docker compose logs --tail 50 app"
    )
}

Write-Step "Жду готовности приложения"
$healthy = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 3 | Out-Null
        $healthy = $true
        break
    }
    catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $healthy) {
    Write-Warn "Приложение не ответило на /health за 120 секунд."
    Write-Host "  Последние логи:" -ForegroundColor DarkGray
    & docker.exe compose logs --tail 60 app
    Stop-WithHelp "Не удалось проверить здоровье приложения." @(
        "Скорее всего, неверный .env. Исправьте и повторите запуск.",
        "Логи: docker compose logs --tail 100 app"
    )
}
Write-Ok "Приложение работает"

$public = Get-EnvValue "PUBLIC_BASE_URL"
$base = if ([string]::IsNullOrWhiteSpace($public)) { "http://127.0.0.1:8000" } else { $public.TrimEnd("/") }
$webhookUrl = "$base/webhook"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Compliance Sentinel запущен" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Health:   http://127.0.0.1:8000/health"
Write-Host "  Swagger:  http://127.0.0.1:8000/docs"
Write-Host "  Webhook:  $webhookUrl"
Write-Host ""
Write-Host "Следующие шаги в GitHub:" -ForegroundColor Yellow
Write-Host "  1. Settings → Webhooks → Add webhook"
Write-Host "     Payload URL: $webhookUrl"
Write-Host "     Content type: application/json"
Write-Host "     Secret: значение GITHUB_WEBHOOK_SECRET из .env"
Write-Host "     Events: Pull requests"
Write-Host "  2. Settings → Branches → Branch protection для main"
Write-Host "     Включите Require status checks и добавьте 'Compliance Sentinel'."
Write-Host ""
if ([string]::IsNullOrWhiteSpace($public)) {
    Write-Warn "PUBLIC_BASE_URL пуст: GitHub не сможет достучаться до localhost."
    Write-Host "  Для реального вебхука нужен публичный HTTPS/HTTP URL (туннель или Render)."
    Write-Host "  Укажите его в .env как PUBLIC_BASE_URL и перезапустите: docker compose up -d"
    Write-Host ""
}
Write-Host "Остановка: docker compose down" -ForegroundColor DarkGray
