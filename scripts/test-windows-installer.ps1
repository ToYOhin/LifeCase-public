[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SetupExe,
    [Parameter(Mandatory = $true)]
    [string]$DataRoot
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

$setupPath = [System.IO.Path]::GetFullPath($SetupExe)
$dataPath = [System.IO.Path]::GetFullPath($DataRoot)
if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) {
    throw 'LifeCaseSetup.exe is missing.'
}
if (Test-Path -LiteralPath $dataPath) {
    throw 'DataRoot must be a new path for installer acceptance.'
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw 'LOCALAPPDATA is unavailable.'
}

$programsRoot = Join-Path $env:LOCALAPPDATA 'Programs'
$acceptanceName = 'LifeCase-Acceptance-' + [Guid]::NewGuid().ToString('N')
$installRoot = Join-Path $programsRoot $acceptanceName
$startMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'LifeCase.lnk'
$desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'LifeCase.lnk'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LifeCase'

if ((Test-Path -LiteralPath $installRoot) -or
    (Test-Path -LiteralPath $startMenu) -or
    (Test-Path -LiteralPath $desktop) -or
    (Test-Path -LiteralPath $uninstallKey)) {
    throw 'Installer acceptance requires an unused LifeCase install/shortcut/registry boundary.'
}

$installed = $false
$installedUninstaller = $null
try {
    $install = Start-Process -FilePath $setupPath -ArgumentList @(
        '--silent',
        '--no-launch',
        '--no-desktop-shortcut',
        '--install-root', $installRoot,
        '--data-root', $dataPath
    ) -Wait -PassThru -WindowStyle Hidden
    if ($install.ExitCode -ne 0) {
        throw 'LifeCaseSetup.exe installation failed.'
    }
    $installed = $true

    $applicationExe = Join-Path $installRoot 'LifeCase.exe'
    $uninstallerExe = Join-Path $installRoot 'LifeCaseUninstall.exe'
    $marker = Join-Path $installRoot '.lifecase-install.json'
    if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $uninstallerExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $marker -PathType Leaf) -or
        -not (Test-Path -LiteralPath $startMenu -PathType Leaf) -or
        -not (Test-Path -LiteralPath $uninstallKey)) {
        throw 'Installed files or Windows integration are incomplete.'
    }
    $installedUninstaller = $uninstallerExe

    $applicationAcceptance = & (Join-Path $PSScriptRoot 'test-windows-app.ps1') `
        -ApplicationRoot $installRoot `
        -DataRoot $dataPath
    $applicationAcceptance
}
finally {
    if ($installed -and (Test-Path -LiteralPath $installRoot)) {
        $uninstall = Start-Process -FilePath $installedUninstaller -ArgumentList @(
            '--silent',
            '--uninstall',
            '--install-root', $installRoot,
            '--data-root', $dataPath
        ) -Wait -PassThru -WindowStyle Hidden
        if ($uninstall.ExitCode -ne 0) {
            throw 'LifeCaseSetup.exe uninstall failed.'
        }
        $uninstallDeadline = [DateTime]::UtcNow.AddSeconds(30)
        while ((Test-Path -LiteralPath $installRoot) -and
            [DateTime]::UtcNow -lt $uninstallDeadline) {
            Start-Sleep -Milliseconds 100
        }
    }
}

if ((Test-Path -LiteralPath $installRoot) -or
    (Test-Path -LiteralPath $startMenu) -or
    (Test-Path -LiteralPath $uninstallKey)) {
    throw 'Uninstall did not remove the installed program integration.'
}
$database = Join-Path $dataPath 'lifecase.sqlite3'
if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw 'Uninstall did not preserve the user database.'
}

[ordered]@{
    status = 'passed'
    installed = $true
    launched = $true
    stopped = $true
    uninstalled = $true
    data_retained = $true
} | ConvertTo-Json -Compress
