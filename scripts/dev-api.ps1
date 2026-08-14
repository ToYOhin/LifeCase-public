$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
uv run uvicorn lifecase.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8000 --reload
