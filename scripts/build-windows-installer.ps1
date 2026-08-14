[CmdletBinding()]
param(
    [string]$OutputRoot = (Join-Path $env:TEMP 'LifeCase-v0.2.0-windows'),
    [string]$ApplicationRoot,
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
    throw 'LifeCaseSetup.exe can only be built on Windows.'
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

if ([string]::IsNullOrWhiteSpace($ApplicationRoot)) {
    & (Join-Path $PSScriptRoot 'build-windows-app.ps1') `
        -OutputRoot $outputPath `
        -SkipDependencySync:$SkipDependencySync `
        -SkipWebBuild:$SkipWebBuild
    if ($LASTEXITCODE -ne 0) {
        throw 'LifeCase application payload build failed.'
    }
    $applicationPath = Join-Path $outputPath 'app-dist\LifeCase'
}
else {
    $applicationPath = [System.IO.Path]::GetFullPath($ApplicationRoot)
}

$applicationExe = Join-Path $applicationPath 'LifeCase.exe'
if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
    throw 'ApplicationRoot does not contain LifeCase.exe.'
}
if (-not $SkipDependencySync) {
    & uv sync --frozen --group packaging
    if ($LASTEXITCODE -ne 0) {
        throw 'Locked Python packaging dependency sync failed.'
    }
}

$distPath = Join-Path $outputPath 'installer-dist'
$workPath = Join-Path $outputPath 'installer-build'
$specPath = Join-Path $outputPath 'installer-spec'
New-Item -ItemType Directory -Force -Path $specPath | Out-Null

$arguments = @(
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--name', 'LifeCaseSetup',
    '--paths', (Join-Path $repositoryRoot 'apps\api\src'),
    '--version-file', (Join-Path $repositoryRoot 'tools\packaging\lifecase-setup-version-info.txt'),
    '--add-data', "$applicationPath;payload/LifeCase",
    '--distpath', $distPath,
    '--workpath', $workPath,
    '--specpath', $specPath,
    (Join-Path $repositoryRoot 'apps\api\src\lifecase\windows_installer.py')
)

& uv run --group packaging pyinstaller @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'LifeCaseSetup.exe build failed.'
}

$builtSetupExe = Join-Path $distPath 'LifeCaseSetup.exe'
if (-not (Test-Path -LiteralPath $builtSetupExe -PathType Leaf)) {
    throw 'LifeCaseSetup.exe was not produced.'
}
$setupExe = Join-Path $outputPath 'LifeCaseSetup.exe'
Copy-Item -LiteralPath $builtSetupExe -Destination $setupExe -Force

$setupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setupExe).Hash.ToLowerInvariant()
$appHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $applicationExe).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    format = 'lifecase.windows-installer-build.v1'
    version = '0.2.0'
    installer = $setupExe
    installer_sha256 = $setupHash
    application_root = $applicationPath
    application_sha256 = $appHash
}
$manifestPath = Join-Path $outputPath 'windows-installer-build.json'
[System.IO.File]::WriteAllText(
    $manifestPath,
    (($manifest | ConvertTo-Json -Depth 3) + "`n"),
    [System.Text.UTF8Encoding]::new($false)
)
$checksumsPath = Join-Path $outputPath 'SHA256SUMS.txt'
[System.IO.File]::WriteAllText(
    $checksumsPath,
    "$setupHash  LifeCaseSetup.exe`n",
    [System.Text.UTF8Encoding]::new($false)
)

$manifest | ConvertTo-Json -Compress
