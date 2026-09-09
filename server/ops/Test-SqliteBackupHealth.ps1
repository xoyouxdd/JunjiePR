[CmdletBinding()]
param(
    [string]$TaskName = "RecognitionCardSystem-SQLiteBackup",
    [string]$BackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite",
    [string]$Python = "C:\Server\zhaojunjie\recognition-card-system\.venv\Scripts\python.exe",
    [ValidateRange(1, 168)]
    [int]$MaxBackupAgeHours = 26,
    [ValidateRange(1, 1440)]
    [int]$BackupCompletionTimeoutMinutes = 60,
    [ValidateRange(1, 168)]
    [int]$AlertRepeatHours = 24,
    [string]$AlertWebhookUrlEnvironmentVariable,
    [string]$StatusFile,
    [switch]$TestMode,
    [string]$TestRoot,
    [string]$TaskStatusFile,
    [Nullable[DateTime]]$NowUtc
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedBackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite"
$expectedPython = "C:\Server\zhaojunjie\recognition-card-system\.venv\Scripts\python.exe"
$backupNamePattern = '^recognition_v2-\d{8}T\d{6}-\d{3}\.db$'

function Get-NormalizedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "Path must not be empty" }
    return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Test-IsChildPath([string]$Child, [string]$Parent) {
    $childPath = Get-NormalizedPath $Child
    $parentPath = Get-NormalizedPath $Parent
    return $childPath.StartsWith($parentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-ExactPath([string]$Actual, [string]$Expected, [string]$Label) {
    if (-not (Get-NormalizedPath $Actual).Equals((Get-NormalizedPath $Expected), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected $Label path: $Actual"
    }
}

function Resolve-PythonExecutable([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) {
        $resolved = Get-NormalizedPath $Value
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { throw "Python executable not found: $resolved" }
        return $resolved
    }
    $command = Get-Command -Name $Value -CommandType Application -ErrorAction Stop | Select-Object -First 1
    return (Get-NormalizedPath $command.Source)
}

function Convert-ToUtcOrNull($Value) {
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        [string]$Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal,
        [ref]$parsed
    )) { throw "Invalid UTC date value: $Value" }
    return $parsed.UtcDateTime
}

function Add-Issue([Collections.Generic.List[object]]$Issues, [string]$Code, [string]$Message) {
    $Issues.Add([pscustomobject]@{ code = $Code; message = $Message })
}

$backupPath = Get-NormalizedPath $BackupRoot
if ([string]::IsNullOrWhiteSpace($StatusFile)) {
    $StatusFile = Join-Path $backupPath "backup-health-status.json"
}
$statusPath = Get-NormalizedPath $StatusFile

if (-not $TestMode) {
    Assert-ExactPath $backupPath $expectedBackupRoot "backup root"
    Assert-ExactPath $Python $expectedPython "production Python"
    if (-not (Test-IsChildPath $statusPath $backupPath)) { throw "Status file must be inside the backup root" }
    if ($null -ne $NowUtc) { throw "NowUtc is only allowed in TestMode" }
    if (-not [string]::IsNullOrWhiteSpace($TaskStatusFile)) { throw "TaskStatusFile is only allowed in TestMode" }
}
else {
    if ([string]::IsNullOrWhiteSpace($TestRoot)) { throw "TestRoot is required with TestMode" }
    $testPath = Get-NormalizedPath $TestRoot
    if (-not (Test-IsChildPath $backupPath $testPath)) { throw "Test backup root must be inside TestRoot" }
    if (-not (Test-IsChildPath $statusPath $testPath)) { throw "Test status file must be inside TestRoot" }
    if ([string]::IsNullOrWhiteSpace($TaskStatusFile)) { throw "TaskStatusFile is required with TestMode" }
    $taskStatusPath = Get-NormalizedPath $TaskStatusFile
    if (-not (Test-IsChildPath $taskStatusPath $testPath)) { throw "Test task status file must be inside TestRoot" }
    if (-not (Test-Path -LiteralPath $taskStatusPath -PathType Leaf)) { throw "Test task status file not found" }
}

if (-not (Test-Path -LiteralPath $backupPath -PathType Container)) {
    throw "Backup root not found: $backupPath"
}
$pythonPath = Resolve-PythonExecutable $Python
$now = if ($null -ne $NowUtc) { $NowUtc.ToUniversalTime() } else { [DateTime]::UtcNow }
$issues = [Collections.Generic.List[object]]::new()

if ($TestMode) {
    $taskSnapshot = Get-Content -Raw -LiteralPath $taskStatusPath | ConvertFrom-Json
}
else {
    $scheduledTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $scheduledTask) {
        $taskSnapshot = [pscustomobject]@{
            exists = $false
            enabled = $false
            state = "Missing"
            last_run_time_utc = $null
            next_run_time_utc = $null
            last_task_result = $null
        }
    }
    else {
        $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
        $lastRun = if ($taskInfo.LastRunTime -eq [DateTime]::MinValue) { $null } else { $taskInfo.LastRunTime.ToUniversalTime().ToString("o") }
        $nextRun = if ($taskInfo.NextRunTime -eq [DateTime]::MinValue) { $null } else { $taskInfo.NextRunTime.ToUniversalTime().ToString("o") }
        $taskSnapshot = [pscustomobject]@{
            exists = $true
            enabled = [bool]$scheduledTask.Settings.Enabled
            state = [string]$scheduledTask.State
            last_run_time_utc = $lastRun
            next_run_time_utc = $nextRun
            last_task_result = [long]$taskInfo.LastTaskResult
        }
    }
}

$taskExists = $null -ne $taskSnapshot.exists -and [bool]$taskSnapshot.exists
$taskEnabled = $taskExists -and $null -ne $taskSnapshot.enabled -and [bool]$taskSnapshot.enabled
$taskState = if ($null -eq $taskSnapshot.state) { "Unknown" } else { [string]$taskSnapshot.state }
$lastRunUtc = Convert-ToUtcOrNull $taskSnapshot.last_run_time_utc
$nextRunUtc = Convert-ToUtcOrNull $taskSnapshot.next_run_time_utc
$lastTaskResult = if ($null -eq $taskSnapshot.last_task_result) { $null } else { [long]$taskSnapshot.last_task_result }

if (-not $taskExists) {
    Add-Issue $issues "task_missing" "The scheduled backup task does not exist."
}
elseif (-not $taskEnabled) {
    Add-Issue $issues "task_disabled" "The scheduled backup task is disabled."
}
if ($taskExists -and $null -eq $lastRunUtc) {
    Add-Issue $issues "task_never_run" "The scheduled backup task has never run."
}
elseif ($taskExists -and $null -ne $lastTaskResult -and $lastTaskResult -ne 0 -and $taskState -ne "Running") {
    Add-Issue $issues "task_last_run_failed" "The latest scheduled backup task result is non-zero."
}

$backupFiles = @(
    Get-ChildItem -LiteralPath $backupPath -File |
        Where-Object { $_.Name -match $backupNamePattern } |
        Sort-Object LastWriteTimeUtc -Descending
)
$latestBackup = if ($backupFiles.Count -gt 0) { $backupFiles[0] } else { $null }
$latestCreatedUtc = $null
$latestManifestPath = $null
$latestManifest = $null
$actualQuickCheck = $null
$hashVerified = $false
$sizeVerified = $false
$manifestVerified = $false

if ($null -eq $latestBackup) {
    Add-Issue $issues "backup_missing" "No managed SQLite backup file exists."
}
else {
    $latestCreatedUtc = $latestBackup.LastWriteTimeUtc
    $ageHours = ($now - $latestCreatedUtc).TotalHours
    if ($ageHours -gt $MaxBackupAgeHours) {
        Add-Issue $issues "backup_stale" "The latest backup is older than the allowed age."
    }

    $latestManifestPath = "$($latestBackup.FullName).manifest.json"
    if (-not (Test-Path -LiteralPath $latestManifestPath -PathType Leaf)) {
        Add-Issue $issues "manifest_missing" "The latest backup has no adjacent manifest."
    }
    else {
        try {
            $latestManifest = Get-Content -Raw -LiteralPath $latestManifestPath | ConvertFrom-Json
            if ([string]$latestManifest.backup_file -ne $latestBackup.Name) {
                Add-Issue $issues "manifest_backup_name_mismatch" "The manifest backup filename does not match."
            }
            if ([string]$latestManifest.quick_check -ne "ok") {
                Add-Issue $issues "manifest_quick_check_failed" "The manifest quick_check value is not ok."
            }
            if ([long]$latestManifest.size_bytes -ne $latestBackup.Length) {
                Add-Issue $issues "manifest_size_mismatch" "The backup size does not match its manifest."
            }
            else { $sizeVerified = $true }
            if ([string]::IsNullOrWhiteSpace([string]$latestManifest.sha256)) {
                Add-Issue $issues "manifest_sha256_missing" "The manifest has no SHA-256 value."
            }
            else {
                $actualHash = (Get-FileHash -LiteralPath $latestBackup.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
                if (-not $actualHash.Equals(([string]$latestManifest.sha256).ToLowerInvariant(), [StringComparison]::Ordinal)) {
                    Add-Issue $issues "manifest_sha256_mismatch" "The backup SHA-256 does not match its manifest."
                }
                else { $hashVerified = $true }
            }
            $manifestVerified = $sizeVerified -and $hashVerified -and ([string]$latestManifest.backup_file -eq $latestBackup.Name) -and ([string]$latestManifest.quick_check -eq "ok")
        }
        catch {
            Add-Issue $issues "manifest_invalid" "The latest backup manifest is invalid JSON or has invalid fields."
        }
    }

    $quickCheckCode = @'
import json, pathlib, sqlite3, sys
backup_uri = pathlib.Path(sys.argv[1]).resolve().as_uri() + "?mode=ro"
connection = sqlite3.connect(backup_uri, uri=True, timeout=60)
try:
    connection.execute("PRAGMA busy_timeout=60000")
    rows = [row[0] for row in connection.execute("PRAGMA quick_check")]
finally:
    connection.close()
print(json.dumps({"rows": rows}))
'@
    try {
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $quickOutput = $quickCheckCode | & $pythonPath - $latestBackup.FullName 2>&1
            $quickExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorAction
        }
        if ($quickExitCode -ne 0) { throw "SQLite could not open the latest backup" }
        $quickResult = (($quickOutput -join "") | ConvertFrom-Json)
        $actualQuickCheck = @($quickResult.rows) -join "; "
        if (@($quickResult.rows).Count -ne 1 -or [string]$quickResult.rows[0] -ne "ok") {
            Add-Issue $issues "backup_quick_check_failed" "The latest backup failed SQLite quick_check."
        }
    }
    catch {
        Add-Issue $issues "backup_quick_check_error" "SQLite quick_check could not be completed for the latest backup."
    }
}

if ($null -ne $lastRunUtc -and ($now - $lastRunUtc).TotalMinutes -gt $BackupCompletionTimeoutMinutes) {
    $completionToleranceUtc = $lastRunUtc.AddMinutes(-1)
    if ($null -eq $latestCreatedUtc -or $latestCreatedUtc -lt $completionToleranceUtc) {
        Add-Issue $issues "backup_timeout_no_output" "The latest scheduled run produced no backup within the completion timeout."
    }
}

$previousStatus = $null
if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
    try { $previousStatus = Get-Content -Raw -LiteralPath $statusPath | ConvertFrom-Json } catch { $previousStatus = $null }
}
$issueCodes = @($issues | ForEach-Object { $_.code } | Sort-Object -Unique)
$fingerprint = $issueCodes -join ","
$ok = $issues.Count -eq 0
$report = [ordered]@{
    schema_version = 1
    checked_at_utc = $now.ToString("o")
    ok = $ok
    task = [ordered]@{
        name = $TaskName
        exists = $taskExists
        enabled = $taskEnabled
        state = $taskState
        last_run_time_utc = if ($null -eq $lastRunUtc) { $null } else { $lastRunUtc.ToString("o") }
        next_run_time_utc = if ($null -eq $nextRunUtc) { $null } else { $nextRunUtc.ToString("o") }
        last_task_result = $lastTaskResult
    }
    latest_backup = if ($null -eq $latestBackup) { $null } else { [ordered]@{
        file_name = $latestBackup.Name
        created_at_utc = $latestCreatedUtc.ToString("o")
        age_hours = [Math]::Round(($now - $latestCreatedUtc).TotalHours, 3)
        size_bytes = $latestBackup.Length
        manifest_verified = $manifestVerified
        sha256_verified = $hashVerified
        size_verified = $sizeVerified
        quick_check = $actualQuickCheck
    } }
    thresholds = [ordered]@{
        max_backup_age_hours = $MaxBackupAgeHours
        completion_timeout_minutes = $BackupCompletionTimeoutMinutes
    }
    issues = @($issues)
    issue_fingerprint = $fingerprint
    alert = [ordered]@{
        configured = -not [string]::IsNullOrWhiteSpace($AlertWebhookUrlEnvironmentVariable)
        attempted = $false
        delivered = $false
        error = $null
        last_alert_at_utc = $null
    }
}

$shouldAlert = $false
if (-not $ok -and -not [string]::IsNullOrWhiteSpace($AlertWebhookUrlEnvironmentVariable)) {
    $previousFingerprint = if ($null -eq $previousStatus -or $null -eq $previousStatus.issue_fingerprint) { $null } else { [string]$previousStatus.issue_fingerprint }
    $previousAlertUtc = if ($null -eq $previousStatus -or $null -eq $previousStatus.alert -or $null -eq $previousStatus.alert.last_alert_at_utc) { $null } else { Convert-ToUtcOrNull $previousStatus.alert.last_alert_at_utc }
    $shouldAlert = $previousFingerprint -ne $fingerprint -or $null -eq $previousAlertUtc -or ($now - $previousAlertUtc).TotalHours -ge $AlertRepeatHours
    if ($null -ne $previousAlertUtc) { $report.alert.last_alert_at_utc = $previousAlertUtc.ToString("o") }
}

$statusDirectory = Split-Path -Parent $statusPath
if (-not (Test-Path -LiteralPath $statusDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $statusDirectory -Force | Out-Null
}
$statusTemp = "$statusPath.partial-$PID"
try {
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statusTemp -Encoding UTF8
    Move-Item -LiteralPath $statusTemp -Destination $statusPath -Force

    if ($shouldAlert) {
        $report.alert.attempted = $true
        $webhookUrl = [Environment]::GetEnvironmentVariable($AlertWebhookUrlEnvironmentVariable)
        if ([string]::IsNullOrWhiteSpace($webhookUrl)) {
            $report.alert.error = "The configured webhook environment variable is empty."
        }
        else {
            try {
                $payload = [ordered]@{
                    event = "recognition_backup_health_failed"
                    checked_at_utc = $report.checked_at_utc
                    task_name = $TaskName
                    issue_codes = $issueCodes
                    latest_backup_file = if ($null -eq $latestBackup) { $null } else { $latestBackup.Name }
                } | ConvertTo-Json -Depth 4
                Invoke-RestMethod -Uri $webhookUrl -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 15 | Out-Null
                $report.alert.delivered = $true
                $report.alert.last_alert_at_utc = $now.ToString("o")
            }
            catch {
                $report.alert.error = "Webhook delivery failed."
            }
        }
        $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statusTemp -Encoding UTF8
        Move-Item -LiteralPath $statusTemp -Destination $statusPath -Force
    }
}
finally {
    if (Test-Path -LiteralPath $statusTemp) { Remove-Item -LiteralPath $statusTemp -Force -ErrorAction SilentlyContinue }
}

$report | ConvertTo-Json -Depth 8
if (-not $ok -or ($report.alert.attempted -and -not $report.alert.delivered)) { exit 2 }
exit 0
