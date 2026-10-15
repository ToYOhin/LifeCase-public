param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$arguments = @('-m', 'lifecase.demo_readiness', 'seed', '--port', $Port)
if ($Execute) {
    $arguments += '--execute'
}

Set-Location -LiteralPath $projectRoot
uv run python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "DeepSeek demo seed or acceptance failed with exit code $LASTEXITCODE."
}
