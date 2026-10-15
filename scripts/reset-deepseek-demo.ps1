param(
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'LifeCase-DeepSeek-Demo'),
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "127.0.0.1:$Port is still in use. Stop the verified LifeCase demo process before reset."
}

Write-Host 'Reset will delete only the marked synthetic demo database, data objects, and uploads.'
if (-not $Force) {
    $confirmation = Read-Host 'Type RESET to continue'
    if ($confirmation -cne 'RESET') {
        Write-Host 'Reset cancelled.'
        exit 0
    }
}

Set-Location -LiteralPath $projectRoot
uv run python -m lifecase.demo_readiness reset `
    --runtime-root $DataRoot `
    --confirm-reset
if ($LASTEXITCODE -ne 0) {
    throw "DeepSeek demo reset was refused with exit code $LASTEXITCODE."
}
