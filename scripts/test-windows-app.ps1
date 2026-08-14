[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ApplicationRoot,
    [Parameter(Mandatory = $true)]
    [string]$DataRoot,
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30
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

$applicationPath = [System.IO.Path]::GetFullPath($ApplicationRoot)
$dataPath = [System.IO.Path]::GetFullPath($DataRoot)
$executable = Join-Path $applicationPath 'LifeCase.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw 'LifeCase.exe is missing from ApplicationRoot.'
}
if (Test-Path -LiteralPath $dataPath) {
    throw 'DataRoot must be a new path for frozen acceptance.'
}

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$launcher = Start-Process -FilePath $executable -ArgumentList @(
    '--no-browser',
    '--no-dialog',
    '--data-root', $dataPath,
    '--port', $port
) -PassThru -WindowStyle Hidden

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$health = $null
while ([DateTime]::UtcNow -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/v1/system/health" -TimeoutSec 1
        if ($health.status -eq 'ok') {
            break
        }
    }
    catch {
        Start-Sleep -Milliseconds 100
    }
}

try {
    if ($null -eq $health -or $health.status -ne 'ok') {
        throw 'Frozen LifeCase.exe did not become healthy in time.'
    }
    $web = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/" -TimeoutSec 5
    if ($web.StatusCode -ne 200 -or $web.Content -notlike '*LifeCase*') {
        throw 'Frozen LifeCase.exe did not serve the built Web application.'
    }
}
finally {
    $stop = Start-Process -FilePath $executable -ArgumentList @(
        '--stop',
        '--no-dialog',
        '--data-root', $dataPath,
        '--port', $port
    ) -Wait -PassThru -WindowStyle Hidden
    if ($stop.ExitCode -ne 0) {
        throw 'Frozen LifeCase.exe did not stop cleanly.'
    }
}

$launcher.WaitForExit(5000) | Out-Null
$statePath = Join-Path $dataPath 'runtime\daily-use-state.json'
$databasePath = Join-Path $dataPath 'lifecase.sqlite3'
if (Test-Path -LiteralPath $statePath) {
    throw 'Frozen LifeCase.exe left false running state after stop.'
}
if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
    throw 'Frozen LifeCase.exe did not preserve the initialized database.'
}

[ordered]@{
    status = 'passed'
    health_version = $health.version
    web_status = 200
    stopped = $true
    data_retained = $true
} | ConvertTo-Json -Compress
