[CmdletBinding()]
param(
    [string]$OutputRoot = (Join-Path $env:TEMP 'LifeCase-v0.2.0-windows'),
    [switch]$SkipDependencySync,
    [switch]$SkipWebBuild
)

$ErrorActionPreference = 'Stop'

try {
    $currentProcess = Get-Process -Id $PID
    $currentProcess.PriorityClass = 'Idle'
    if ([Environment]::ProcessorCount -ge 2) {
        $currentProcess.ProcessorAffinity = [IntPtr]3
    }
}
catch {
    Write-Warning 'Could not apply the optional low-resource process limits.'
}

if ($env:OS -ne 'Windows_NT') {
    throw 'LifeCase.exe can only be built on Windows.'
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outputWasRooted = [System.IO.Path]::IsPathRooted($OutputRoot)
$outputPath = [System.IO.Path]::GetFullPath($OutputRoot)
$repositoryPrefix = $repositoryRoot.TrimEnd('\') + '\'
if (-not $outputWasRooted) {
    throw 'OutputRoot must be an absolute path.'
}
if ($outputPath.Equals($repositoryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $outputPath.StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Windows build artifacts must stay outside the repository.'
}

New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
Set-Location -LiteralPath $repositoryRoot

if (-not $SkipDependencySync) {
    & uv sync --frozen --group packaging
    if ($LASTEXITCODE -ne 0) {
        throw 'Locked Python packaging dependency sync failed.'
    }
    & npm ci
    if ($LASTEXITCODE -ne 0) {
        throw 'Locked Web dependency install failed.'
    }
}

if (-not $SkipWebBuild) {
    & npm run build:web
    if ($LASTEXITCODE -ne 0) {
        throw 'Web production build failed.'
    }
}

$staticRoot = Join-Path $repositoryRoot 'apps\web\dist'
$staticIndex = Join-Path $staticRoot 'index.html'
if (-not (Test-Path -LiteralPath $staticIndex -PathType Leaf)) {
    throw 'Built Web assets are missing.'
}

$distPath = Join-Path $outputPath 'app-dist'
$workPath = Join-Path $outputPath 'app-build'
$specPath = Join-Path $outputPath 'spec'
New-Item -ItemType Directory -Force -Path $specPath | Out-Null

$arguments = @(
    '--noconfirm',
    '--clean',
    '--onedir',
    '--windowed',
    '--name', 'LifeCase',
    '--paths', (Join-Path $repositoryRoot 'apps\api\src'),
    '--version-file', (Join-Path $repositoryRoot 'tools\packaging\lifecase-version-info.txt'),
    '--add-data', "$staticRoot;web",
    '--collect-data', 'tzdata',
    '--distpath', $distPath,
    '--workpath', $workPath,
    '--specpath', $specPath
)

$templates = @(
    'warranty_claim.v1.yaml',
    'return_refund_claim.v1.yaml',
    'expense_reimbursement.v1.yaml',
    'subscription_cancellation_claim.v1.yaml'
)
foreach ($template in $templates) {
    $arguments += @(
        '--add-data',
        "$(Join-Path $repositoryRoot "packages\case-templates\$template");lifecase/templates/resources"
    )
}
$arguments += Join-Path $repositoryRoot 'apps\api\src\lifecase\windows_app.py'

& uv run --group packaging pyinstaller @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'LifeCase.exe build failed.'
}

$applicationRoot = Join-Path $distPath 'LifeCase'
$applicationExe = Join-Path $applicationRoot 'LifeCase.exe'
if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
    throw 'LifeCase.exe was not produced.'
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $applicationExe).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    format = 'lifecase.windows-app-build.v1'
    version = '0.2.0'
    application_root = $applicationRoot
    executable = $applicationExe
    executable_sha256 = $hash
}
$manifestPath = Join-Path $outputPath 'windows-app-build.json'
[System.IO.File]::WriteAllText(
    $manifestPath,
    (($manifest | ConvertTo-Json -Depth 3) + "`n"),
    [System.Text.UTF8Encoding]::new($false)
)

$manifest | ConvertTo-Json -Compress
