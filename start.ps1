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

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host ""
    Write-Host "Created .env. Fill GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET and LLM_API_KEY." -ForegroundColor Yellow
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

Write-Host "Starting Compliance Sentinel..." -ForegroundColor Cyan
Write-Host ""
& $venvPython "run.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Startup failed. Read the message above and fix .env." -ForegroundColor Red
    exit $LASTEXITCODE
}
