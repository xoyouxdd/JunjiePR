[CmdletBinding()]
param(
    [string]$TaskName = "RecognitionCardSystem-SQLiteBackupHealth",
    [string]$BackupTaskName = "RecognitionCardSystem-SQLiteBackup",
    [string]$HealthScript = "C:\Server\zhaojunjie\recognition-card-system\ops\Test-SqliteBackupHealth.ps1",
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyAt = "03:30",
    [ValidateRange(1, 168)]
    [int]$MaxBackupAgeHours = 26,
    [ValidateRange(1, 1440)]
    [int]$BackupCompletionTimeoutMinutes = 60,
    [string]$AlertWebhookUrlEnvironmentVariable,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$TestMode,
    [string]$TestRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedHealthScript = "C:\Server\zhaojunjie\recognition-card-system\ops\Test-SqliteBackupHealth.ps1"

function Get-NormalizedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "Path must not be empty" }
    return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Test-IsChildPath([string]$Child, [string]$Parent) {
    $childPath = Get-NormalizedPath $Child
    $parentPath = Get-NormalizedPath $Parent
    return $childPath.StartsWith($parentPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

$scriptPath = Get-NormalizedPath $HealthScript
if (-not $TestMode) {
    if (-not $scriptPath.Equals((Get-NormalizedPath $expectedHealthScript), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected production health script path"
    }
}
else {
    if (-not $DryRun) { throw "TestMode only supports DryRun" }
    if ([string]::IsNullOrWhiteSpace($TestRoot)) { throw "TestRoot is required with TestMode" }
    if (-not (Test-IsChildPath $scriptPath (Get-NormalizedPath $TestRoot))) { throw "Test health script must be inside TestRoot" }
}
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "Backup health script not found: $scriptPath" }

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
$arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" -TaskName `"$BackupTaskName`" -MaxBackupAgeHours $MaxBackupAgeHours -BackupCompletionTimeoutMinutes $BackupCompletionTimeoutMinutes"
if (-not [string]::IsNullOrWhiteSpace($AlertWebhookUrlEnvironmentVariable)) {
    if ($AlertWebhookUrlEnvironmentVariable -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "Invalid alert environment variable name" }
    $arguments += " -AlertWebhookUrlEnvironmentVariable `"$AlertWebhookUrlEnvironmentVariable`""
}

$definition = [ordered]@{
    task_name = $TaskName
    monitors_task = $BackupTaskName
    daily_at = $DailyAt
    max_backup_age_hours = $MaxBackupAgeHours
    completion_timeout_minutes = $BackupCompletionTimeoutMinutes
    executable = $powershellExe
    arguments = $arguments
    principal = "SYSTEM"
    webhook_secret_storage = if ([string]::IsNullOrWhiteSpace($AlertWebhookUrlEnvironmentVariable)) { "not_configured" } else { "environment_variable" }
}

if ($DryRun) {
    [pscustomobject]@{ ok = $true; dry_run = $true; definition = $definition } | ConvertTo-Json -Depth 5
    return
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and -not $Force) { throw "Scheduled task already exists. Use Force only after reviewing the existing task." }

$action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Daily -At $time
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Validates Recognition Card System backups and returns a non-zero result on failure; no credentials stored."
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force:$Force | Out-Null

[pscustomobject]@{
    ok = $true
    dry_run = $false
    task_name = $TaskName
    monitors_task = $BackupTaskName
    daily_at = $DailyAt
    principal = "SYSTEM"
} | ConvertTo-Json -Depth 4
