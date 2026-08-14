[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('setup', 'start', 'stop', 'status', 'upgrade', 'uninstall')]
    [string]$Command = 'start',

    [string]$DataRoot,

    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Write-Output '{"error":{"action":"Pass an absolute path with -DataRoot.","code":"data_root_default_unavailable","message":"The safe per-user data-directory default is unavailable.","retryable":false}}'
        exit 1
    }
    $DataRoot = Join-Path -Path $env:LOCALAPPDATA -ChildPath 'LifeCase'
}

$python = Join-Path -Path $projectRoot -ChildPath '.venv\Scripts\python.exe'
if ($Command -eq 'upgrade') {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Write-Output '{"error":{"action":"Run setup with the locked offline caches first.","code":"python_runtime_missing","message":"The locked Python runtime is unavailable.","retryable":true}}'
        exit 1
    }
    $upgradeCheckArguments = @(
        '-m', 'lifecase.daily_use', 'upgrade',
        '--data-root', $DataRoot,
        '--program-root', $projectRoot,
        '--port', $Port
    )
    $upgradeCheck = & $python @upgradeCheckArguments
    if ($LASTEXITCODE -ne 0) {
        $upgradeCheck | Write-Output
        exit $LASTEXITCODE
    }
}

if ($Command -in @('setup', 'upgrade')) {
    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv sync --frozen --offline *> $null
    $pythonSetupExit = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($pythonSetupExit -ne 0) {
        Write-Output '{"error":{"action":"Restore the locked Python package cache and retry offline.","code":"python_setup_failed","message":"Locked offline Python setup failed.","retryable":true}}'
        exit 1
    }
    $ErrorActionPreference = 'Continue'
    & npm ci --offline *> $null
    $webSetupExit = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($webSetupExit -ne 0) {
        Write-Output '{"error":{"action":"Restore the locked npm package cache and retry offline.","code":"web_setup_failed","message":"Locked offline Web setup failed.","retryable":true}}'
        exit 1
    }
    $ErrorActionPreference = 'Continue'
    & npm run build:web *> $null
    $staticBuildExit = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($staticBuildExit -ne 0) {
        Write-Output '{"error":{"action":"Correct the Web build failure and retry.","code":"static_build_failed","message":"The production Web build failed.","retryable":true}}'
        exit 1
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Output '{"error":{"action":"Run the setup command with the locked repository tools.","code":"python_runtime_missing","message":"The locked Python runtime is unavailable.","retryable":true}}'
    exit 1
}

$runtimeCommand = $Command
if ($Command -eq 'setup') {
    $setupArguments = @(
        '-m', 'lifecase.daily_use', 'setup',
        '--data-root', $DataRoot,
        '--program-root', $projectRoot,
        '--static-root', (Join-Path -Path $projectRoot -ChildPath 'apps\web\dist'),
        '--port', $Port
    )
    $setupResult = & $python @setupArguments
    if ($LASTEXITCODE -ne 0) {
        $setupResult | Write-Output
        exit $LASTEXITCODE
    }
    $runtimeCommand = 'start'
}

$arguments = @(
    '-m', 'lifecase.daily_use', $runtimeCommand,
    '--data-root', $DataRoot,
    '--program-root', $projectRoot,
    '--static-root', (Join-Path -Path $projectRoot -ChildPath 'apps\web\dist'),
    '--port', $Port
)
$result = & $python @arguments
$exitCode = $LASTEXITCODE
$result | Write-Output

if ($exitCode -eq 0 -and $runtimeCommand -eq 'start' -and -not $NoBrowser) {
    try {
        $status = $result | ConvertFrom-Json
        if ($status.status -eq 'running' -and $status.url -like 'http://127.0.0.1:*') {
            Start-Process -FilePath $status.url
        }
    }
    catch {
        Write-Warning 'LifeCase started, but the loopback page could not be opened automatically.'
    }
}

exit $exitCode
