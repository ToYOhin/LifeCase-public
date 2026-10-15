param(
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'LifeCase-DeepSeek-Demo'),
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$staticRoot = Join-Path $projectRoot 'apps\web\dist'

Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath (Join-Path $staticRoot 'index.html') -PathType Leaf)) {
    Write-Host 'Web build is missing; building the checked-in Web app first...'
    npm run build:web
    if ($LASTEXITCODE -ne 0) {
        throw "Web build failed with exit code $LASTEXITCODE."
    }
}

$launcherArguments = @(
    '-m', 'lifecase.deepseek_live',
    '--runtime-root', $DataRoot,
    '--static-root', $staticRoot,
    '--port', $Port,
    '--confirm-synthetic-data'
)
if (-not $NoBrowser) {
    $launcherArguments += '--open-browser'
}

uv run python @launcherArguments

if ($LASTEXITCODE -ne 0) {
    throw "DeepSeek Live Agent launcher failed with exit code $LASTEXITCODE."
}
