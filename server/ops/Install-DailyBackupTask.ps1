[CmdletBinding()]
param(
    [string]$TaskName = "RecognitionCardSystem-SQLiteBackup",
    [string]$DailyAt = "02:15",
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30,
    [string]$BackupScript = "C:\Server\zhaojunjie\recognition-card-system\ops\Invoke-SqliteOnlineBackup.ps1",
    [switch]$Force,
    [switch]$DryRun,
    [switch]$TestMode,
    [string]$TestRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedBackupScript = "C:\Server\zhaojunjie\recognition-card-system\ops\Invoke-SqliteOnlineBackup.ps1"

if ($TestMode -and -not $DryRun) { throw "TestMode only supports DryRun and cannot register a scheduled task" }

function Get-NormalizedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "Path must not be empty" }
    return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Test-IsChildPath([string]$Child, [string]$Parent) {
    $childPath = Get-NormalizedPath $Child
    $parentPath = Get-NormalizedPath $Parent
    return $childPath.StartsWith($parentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

if ($TaskName -notmatch '^[A-Za-z0-9_.-]{1,100}$') { throw "Unsafe task name" }
if ($DailyAt -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') { throw "DailyAt must use 24-hour HH:mm format" }
if ($BackupScript.Contains('"')) { throw "Backup script path contains an invalid quote" }

$scriptPath = Get-NormalizedPath $BackupScript
if (-not $TestMode) {
    if (-not $scriptPath.Equals((Get-NormalizedPath $expectedBackupScript), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected production backup script path"
    }
}
else {
    if ([string]::IsNullOrWhiteSpace($TestRoot)) { throw "TestRoot is required with TestMode" }
    if (-not (Test-IsChildPath $scriptPath (Get-NormalizedPath $TestRoot))) {
        throw "Test backup script must be inside TestRoot"
    }
}
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "Backup script not found: $scriptPath" }

$time = [DateTime]::MinValue
$parsed = [DateTime]::TryParseExact(
    $DailyAt,
    "HH:mm",
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::None,
    [ref]$time
)
if (-not $parsed) { throw "Invalid DailyAt value" }

$powershellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) { throw "Windows PowerShell executable not found" }
$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" -RetentionDays $RetentionDays"

$definition = [ordered]@{
    task_name = $TaskName
    daily_at = $DailyAt
    retention_days = $RetentionDays
    executable = $powershellExe
    arguments = $arguments
    principal = "SYSTEM"
    logon_type = "ServiceAccount"
    run_level = "Highest"
}

if ($DryRun) {
    [pscustomobject]@{ ok = $true; dry_run = $true; definition = $definition } | ConvertTo-Json -Depth 5
    return
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    throw "Scheduled task already exists. Use Force only after reviewing the existing task."
}

$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Online SQLite backup for Recognition Card System; no credentials stored."
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force:$Force | Out-Null

[pscustomobject]@{
    ok = $true
    dry_run = $false
    task_name = $TaskName
    daily_at = $DailyAt
    retention_days = $RetentionDays
    principal = "SYSTEM"
} | ConvertTo-Json -Depth 4
