# Windows PowerShell twin of the Makefile (SHELL := /bin/bash there).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/tasks.ps1 <target>
# Targets: setup dev-local seed reset-demo test test-py test-node lint fmt
#          contracts openapi ts-types smoke check-docs mypy-gate clean

param(
    [Parameter(Position = 0)]
    [string]$Target = "help"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

function Header($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

switch ($Target) {
    "help" {
        Write-Host "targets: setup dev-local seed reset-demo test test-py test-node lint fmt contracts openapi ts-types smoke check-docs mypy-gate clean"
    }
    "setup" {
        Header "setup (venv + pinned deps + relay npm)"
        python -m venv .venv
        & .venv\Scripts\pip install --upgrade pip
        & .venv\Scripts\pip install -r requirements.txt
        Push-Location relay; npm install; Pop-Location
        if (-not (Test-Path .env) -and (Test-Path .env.example)) {
            Copy-Item .env.example .env
            Write-Host "Created .env from .env.example (all integrations in mock mode)"
        }
    }
    "dev-local" {
        Header "Core on SQLite (http://localhost:8080/docs)"
        Push-Location core
        & ..\.venv\Scripts\alembic.exe upgrade head
        & ..\.venv\Scripts\uvicorn.exe app.main:create_app --factory --reload --port 8080
        Pop-Location
    }
    "seed" {
        Header "migrate + seed demo scenario"
        Push-Location core
        & ..\.venv\Scripts\python.exe -m alembic upgrade head
        & ..\.venv\Scripts\python.exe -m app.seed.seed_demo
        Pop-Location
    }
    "reset-demo" {
        Header "reset demo (between judge demos)"
        Push-Location core
        & ..\.venv\Scripts\alembic.exe upgrade head
        & ..\.venv\Scripts\python.exe -m app.seed.reset_demo
        Pop-Location
    }
    "test" { & $PSScriptRoot\tasks.ps1 test-py; & $PSScriptRoot\tasks.ps1 test-node }
    "test-py" { Header "pytest"; & $py -m pytest }
    "test-parallel" { Header "pytest (-n auto)"; & $py -m pytest -n auto -q }
    "test-node" { Header "relay vitest"; Push-Location relay; npm test; Pop-Location }
    "lint" {
        Header "ruff + tsc"
        & $py -m ruff check .
        & $py -m ruff format --check .
        Push-Location relay; npm run lint; Pop-Location
    }
    "fmt" {
        Header "ruff format + fix"
        & $py -m ruff format .
        & $py -m ruff check --fix .
    }
    "openapi" { Header "export OpenAPI"; & $py scripts\export_openapi.py }
    "ts-types" {
        Header "regenerate shared/ts/core.ts"
        & $PSScriptRoot\tasks.ps1 openapi
        npm --prefix relay run gen:types
    }
    "contracts" { & $PSScriptRoot\tasks.ps1 ts-types }
    "smoke" { Header "mock E2E (Relay path + ASI:One)"; & $py scripts\smoke_e2e.py }
    "check-docs" { & $py scripts\check_docs.py }
    "mypy-gate" { & $py scripts\mypy_gate.py }
    "clean" {
        Header "remove caches and temp artifacts"
        Get-ChildItem -Recurse -Directory -Include __pycache__, .pytest_cache, .ruff_cache -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force
        if (Test-Path relay\node_modules) { Remove-Item relay\node_modules -Recurse -Force }
    }
    default { Write-Host "unknown target '$Target' (try: powershell scripts\tasks.ps1 help)" -ForegroundColor Red; exit 1 }
}
