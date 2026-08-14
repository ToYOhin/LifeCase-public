$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Verification command failed with exit code $LASTEXITCODE."
    }
}

Invoke-CheckedNative { uv run ruff check apps/api }
Invoke-CheckedNative { uv run ruff format --check apps/api }
Invoke-CheckedNative { uv run mypy apps/api/src }
Invoke-CheckedNative { uv run pytest }
Invoke-CheckedNative { npm run check:web }
Invoke-CheckedNative { npm run test:web }
Invoke-CheckedNative { npm run build:web }
