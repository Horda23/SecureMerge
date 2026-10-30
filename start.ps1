#Requires -Version 5.1
<#
.SYNOPSIS
  One-command local launcher for Compliance Sentinel (SecureMerge).

.DESCRIPTION
  Creates .env from .env.example on first run, creates a virtual environment,
  installs dependencies, validates configuration and starts uvicorn.
  Run from any working directory: .\start.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host ""
    Write-Host "Created .env. Fill GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET and GROQ_API_KEY." -ForegroundColor Yellow
    Write-Host "The file will open in Notepad. Save it, then press Enter here." -ForegroundColor Yellow
    Start-Process notepad.exe -ArgumentList ".env"
    Read-Host "Press Enter after saving .env"
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$createdVenv = $false
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
    }
    else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment. Install Python 3.10+ first."
    }
    $createdVenv = $true
}

if ($createdVenv -or -not $SkipInstall) {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & $venvPython -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed."
    }
}

Write-Host "Validating .env..." -ForegroundColor Cyan
& $venvPython -c "from config import get_settings; get_settings()"
if ($LASTEXITCODE -ne 0) {
    throw "Configuration is invalid. Fix .env and run .\start.ps1 again."
}

Write-Host ""
Write-Host "Compliance Sentinel: http://127.0.0.1:8000/health" -ForegroundColor Green
Write-Host "Swagger:             http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $venvPython -m uvicorn main:app --host 0.0.0.0 --port 8000
