#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ReleasePackage,
    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedGitCommit,
    [string]$LiveRoot = "C:\Server\zhaojunjie\recognition-card-system\backend",
    [string]$ServiceTaskName = "RecognitionCardSystem",
    [string]$WatchdogTaskName = "RecognitionCardSystemWatchdog",
    [string]$BackupScript = "C:\Server\zhaojunjie\recognition-card-system\scripts\Invoke-SqliteOnlineBackup.ps1",
    [ValidateRange(1, 65535)]
    [int]$HealthPort = 18082,
    [uri]$ExternalHealthUrl = "https://124.220.229.9:28176/health"
)

<#!
Deploy one verified release package on the production server.

LiveRoot is the deployed application directory
C:\Server\zhaojunjie\recognition-card-system\backend, the same root the daily
backup scripts validate. The script deliberately replaces only app/ and
requirements.txt below it. SQLite data (data_v2), uploads, the virtual
environment, Caddy, task definitions and backups stay in place. It is
invoked by Codex through the approved SSH deployment channel
only after an explicit user release request; a push-triggered job never calls
this script.
#>

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Path([string]$Path, [string]$Kind) {
    if (-not (Test-Path -LiteralPath $Path -PathType $Kind)) { throw "Required $Kind is missing: $Path" }
}

function Get-Sha256([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant() }

function Stop-App {
    $task = Get-ScheduledTask -TaskName $ServiceTaskName -ErrorAction Stop
    if ($task.State -eq "Running") { Stop-ScheduledTask -TaskName $ServiceTaskName }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        $listeners = @(Get-NetTCPConnection -LocalPort $HealthPort -State Listen -ErrorAction SilentlyContinue)
        if (-not $listeners) { return }
        foreach ($listener in $listeners) { Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    throw "Application did not stop listening on port $HealthPort"
}

function Start-App([string]$ExpectedVersion) {
    Start-ScheduledTask -TaskName $ServiceTaskName
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -UseBasicParsing "http://127.0.0.1:$HealthPort/health" -TimeoutSec 5
            if ($health.ok -eq $true -and $health.version -eq $ExpectedVersion) { return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "Health check did not return version $ExpectedVersion"
}

function Confirm-ExternalHealth([string]$ExpectedVersion) {
    # The public endpoint is reached by IP, so the certificate host name never matches.
    $health = Invoke-RestMethod $ExternalHealthUrl.AbsoluteUri -TimeoutSec 15 -SkipCertificateCheck
    if ($health.ok -ne $true -or $health.version -ne $ExpectedVersion) {
        throw "External health check did not return version $ExpectedVersion"
    }
}

$live = [IO.Path]::GetFullPath($LiveRoot).TrimEnd('\')
$package = [IO.Path]::GetFullPath($ReleasePackage)
Assert-Path $live "Container"
Assert-Path $package "Leaf"
Assert-Path $BackupScript "Leaf"
Assert-Path (Join-Path $live ".venv\Scripts\python.exe") "Leaf"

$staging = Join-Path $live (".deploy-staging\" + [Guid]::NewGuid().ToString("N"))
$rollbacks = Join-Path $live ".deploy-rollbacks"
$rollback = Join-Path $rollbacks (Get-Date -Format "yyyyMMddTHHmmss")
$newApp = Join-Path $staging "backend\app"
$newRequirements = Join-Path $staging "backend\requirements.txt"
$oldApp = Join-Path $live "app"
$oldRequirements = Join-Path $live "requirements.txt"
$watchdogWasEnabled = $false
# Set the instant the live app directory leaves its place, so every later failure rolls back.
$oldAppMoved = $false

try {
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    Expand-Archive -LiteralPath $package -DestinationPath $staging -Force
    Assert-Path (Join-Path $staging "release-manifest.json") "Leaf"
    Assert-Path (Join-Path $staging "RELEASE-NOTES.md") "Leaf"
    Assert-Path $newApp "Container"
    Assert-Path $newRequirements "Leaf"

    $manifest = Get-Content (Join-Path $staging "release-manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.app_version -notmatch '^\d{4}\.\d{2}\.\d{2}\.\d+$') { throw "Invalid release version in manifest" }
    if ($manifest.git_commit -ne $ExpectedGitCommit) { throw "Release manifest commit does not match approved commit" }
    foreach ($entry in $manifest.files) {
        if ($entry.path -match '(^|/)(tests|docs)/' -or $entry.path -match '^backend/tests/') { throw "Release contains forbidden test or document path: $($entry.path)" }
        $candidate = [IO.Path]::GetFullPath((Join-Path $staging $entry.path))
        if (-not $candidate.StartsWith($staging + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Manifest path escaped staging: $($entry.path)" }
        Assert-Path $candidate "Leaf"
        if ((Get-Sha256 $candidate) -ne $entry.sha256) { throw "Hash mismatch: $($entry.path)" }
    }

    # The existing online backup script uses sqlite3.Connection.backup(), not a file copy.
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $BackupScript
    if ($LASTEXITCODE -ne 0) { throw "Online database backup failed" }

    $watchdog = Get-ScheduledTask -TaskName $WatchdogTaskName -ErrorAction SilentlyContinue
    if ($watchdog -and $watchdog.State -ne "Disabled") {
        Disable-ScheduledTask -TaskName $WatchdogTaskName | Out-Null
        $watchdogWasEnabled = $true
    }
    Stop-App

    New-Item -ItemType Directory -Path $rollback -Force | Out-Null
    # requirements.txt is preserved first so the rollback copy exists before anything moves.
    Copy-Item -LiteralPath $oldRequirements -Destination (Join-Path $rollback "requirements.txt") -Force
    Move-Item -LiteralPath $oldApp -Destination (Join-Path $rollback "app")
    $oldAppMoved = $true
    Move-Item -LiteralPath $newApp -Destination $oldApp
    Copy-Item -LiteralPath $newRequirements -Destination $oldRequirements -Force

    & (Join-Path $live ".venv\Scripts\python.exe") -m pip install --disable-pip-version-check -r $oldRequirements
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    Start-App $manifest.app_version
    Confirm-ExternalHealth $manifest.app_version
    Set-Content -LiteralPath (Join-Path $live "release-manifest.json") -Value ($manifest | ConvertTo-Json -Depth 8) -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $live "RELEASE-NOTES.md") -Value (Get-Content (Join-Path $staging "RELEASE-NOTES.md") -Raw -Encoding UTF8) -Encoding UTF8
    Write-Output "DEPLOYED version=$($manifest.app_version) commit=$($manifest.git_commit)"
}
catch {
    $failure = $_
    # Every rollback step is isolated so it can never replace or hide the original failure.
    if ($oldAppMoved -and (Test-Path -LiteralPath (Join-Path $rollback "app"))) {
        try { Stop-App }
        catch { Write-Warning "Rollback could not stop the application cleanly: $($_.Exception.Message)" }
        $restored = $false
        try {
            $failedApp = Join-Path $rollback "failed-app"
            if (Test-Path -LiteralPath $oldApp) { Move-Item -LiteralPath $oldApp -Destination $failedApp }
            Move-Item -LiteralPath (Join-Path $rollback "app") -Destination $oldApp
            $restored = $true
            Copy-Item -LiteralPath (Join-Path $rollback "requirements.txt") -Destination $oldRequirements -Force
        }
        catch { Write-Warning "Rollback could not restore the previous app directory: $($_.Exception.Message)" }
        if ($restored) {
            try {
                Start-App ((Select-String -LiteralPath (Join-Path $oldApp "version.py") -Pattern '^APP_VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value)
            }
            catch { Write-Warning "Rollback restored the previous app but it did not become healthy: $($_.Exception.Message)" }
        }
    }
    throw $failure
}
finally {
    if ($watchdogWasEnabled) { Enable-ScheduledTask -TaskName $WatchdogTaskName | Out-Null }
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
