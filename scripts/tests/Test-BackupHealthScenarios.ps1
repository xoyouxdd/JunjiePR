[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$opsRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$healthScript = Join-Path $opsRoot "Test-SqliteBackupHealth.ps1"
$installerScript = Join-Path $opsRoot "Install-DailyBackupHealthTask.ps1"
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("recognition-backup-health-{0}" -f [Guid]::NewGuid().ToString("N"))
$backupRoot = Join-Path $testRoot "backups"
$taskStatusFile = Join-Path $testRoot "task-status.json"
$fakePython = Join-Path $testRoot "fake-sqlite-check.cmd"
$copiedHealthScript = Join-Path $testRoot "Test-SqliteBackupHealth.ps1"
$now = [DateTime]::UtcNow

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Assertion failed: $Message" }
}

function Write-TaskStatus([Nullable[DateTime]]$LastRunUtc, [long]$LastResult = 0) {
    [ordered]@{
        exists = $true
        enabled = $true
        state = "Ready"
        last_run_time_utc = if ($null -ne $LastRunUtc) { $LastRunUtc.ToString("o") } else { $null }
        next_run_time_utc = $now.AddHours(20).ToString("o")
        last_task_result = $LastResult
    } | ConvertTo-Json | Set-Content -LiteralPath $taskStatusFile -Encoding UTF8
}

function Write-BackupFixture([DateTime]$CreatedUtc, [string]$ManifestQuickCheck = "ok") {
    Get-ChildItem -LiteralPath $backupRoot -File -Filter "recognition_v2-*" -ErrorAction SilentlyContinue |
        Remove-Item -Force
    $backupFile = Join-Path $backupRoot ("recognition_v2-{0}.db" -f $CreatedUtc.ToString("yyyyMMddTHHmmss-fff"))
    [IO.File]::WriteAllBytes($backupFile, [Text.Encoding]::UTF8.GetBytes("isolated health-check fixture"))
    (Get-Item -LiteralPath $backupFile).LastWriteTimeUtc = $CreatedUtc
    $hash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $info = Get-Item -LiteralPath $backupFile
    [ordered]@{
        schema_version = 1
        created_at_utc = $CreatedUtc.ToString("o")
        source_database = "data_v2/recognition_v2.db"
        backup_file = $info.Name
        size_bytes = $info.Length
        sha256 = $hash
        quick_check = $ManifestQuickCheck
        sqlite_version = "fixture"
        retention_days = 30
    } | ConvertTo-Json | Set-Content -LiteralPath "$backupFile.manifest.json" -Encoding UTF8
    return $backupFile
}

function Invoke-HealthCheck {
    $output = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $healthScript `
        -BackupRoot $backupRoot -Python $fakePython -TestMode -TestRoot $testRoot `
        -TaskStatusFile $taskStatusFile -NowUtc $now.ToString("o") 2>&1
    $exitCode = $LASTEXITCODE
    $report = ($output -join [Environment]::NewLine) | ConvertFrom-Json
    return [pscustomobject]@{ exit_code = $exitCode; report = $report }
}

function Has-Issue($Result, [string]$Code) {
    return @($Result.report.issues | Where-Object { $_.code -eq $Code }).Count -eq 1
}

$results = [Collections.Generic.List[object]]::new()
try {
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    @'
@echo off
if "%FAKE_SQLITE_QUICKCHECK_FAIL%"=="1" (
  echo {"rows":["corrupt"]}
) else (
  echo {"rows":["ok"]}
)
'@ | Set-Content -LiteralPath $fakePython -Encoding ASCII
    Copy-Item -LiteralPath $healthScript -Destination $copiedHealthScript

    Write-BackupFixture -CreatedUtc $now.AddMinutes(-5) | Out-Null
    Write-TaskStatus -LastRunUtc $now.AddMinutes(-10)
    $healthy = Invoke-HealthCheck
    Assert-True ($healthy.exit_code -eq 0) "healthy scenario must exit 0"
    Assert-True ([bool]$healthy.report.ok) "healthy scenario must report ok"
    $results.Add([pscustomobject]@{ scenario = "healthy"; passed = $true })

    Write-TaskStatus -LastRunUtc $null
    $neverRun = Invoke-HealthCheck
    Assert-True ($neverRun.exit_code -eq 2) "never-run scenario must exit 2"
    Assert-True (Has-Issue $neverRun "task_never_run") "never-run issue must be present"
    $results.Add([pscustomobject]@{ scenario = "task_never_run"; passed = $true })

    Write-TaskStatus -LastRunUtc $now.AddMinutes(-10) -LastResult 1
    $failed = Invoke-HealthCheck
    Assert-True ($failed.exit_code -eq 2) "failed-task scenario must exit 2"
    Assert-True (Has-Issue $failed "task_last_run_failed") "failed-task issue must be present"
    $results.Add([pscustomobject]@{ scenario = "task_last_run_failed"; passed = $true })

    Write-BackupFixture -CreatedUtc $now.AddHours(-3) | Out-Null
    Write-TaskStatus -LastRunUtc $now.AddHours(-2)
    $timeout = Invoke-HealthCheck
    Assert-True ($timeout.exit_code -eq 2) "timeout scenario must exit 2"
    Assert-True (Has-Issue $timeout "backup_timeout_no_output") "timeout issue must be present"
    $results.Add([pscustomobject]@{ scenario = "backup_timeout_no_output"; passed = $true })

    Write-BackupFixture -CreatedUtc $now.AddMinutes(-5) -ManifestQuickCheck "failed" | Out-Null
    Write-TaskStatus -LastRunUtc $now.AddMinutes(-10)
    $manifest = Invoke-HealthCheck
    Assert-True ($manifest.exit_code -eq 2) "manifest scenario must exit 2"
    Assert-True (Has-Issue $manifest "manifest_quick_check_failed") "manifest quick_check issue must be present"
    $results.Add([pscustomobject]@{ scenario = "manifest_quick_check_failed"; passed = $true })

    Write-BackupFixture -CreatedUtc $now.AddMinutes(-5) | Out-Null
    $env:FAKE_SQLITE_QUICKCHECK_FAIL = "1"
    $quickCheck = Invoke-HealthCheck
    Remove-Item Env:FAKE_SQLITE_QUICKCHECK_FAIL
    Assert-True ($quickCheck.exit_code -eq 2) "quick_check scenario must exit 2"
    Assert-True (Has-Issue $quickCheck "backup_quick_check_failed") "backup quick_check issue must be present"
    $results.Add([pscustomobject]@{ scenario = "backup_quick_check_failed"; passed = $true })

    $installOutput = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installerScript `
        -HealthScript $copiedHealthScript -TestMode -TestRoot $testRoot -DryRun
    Assert-True ($LASTEXITCODE -eq 0) "health task installer dry-run must exit 0"
    $installResult = ($installOutput -join [Environment]::NewLine) | ConvertFrom-Json
    Assert-True ([bool]$installResult.ok -and [bool]$installResult.dry_run) "installer dry-run must report ok"
    $results.Add([pscustomobject]@{ scenario = "installer_dry_run"; passed = $true })

    [pscustomobject]@{
        ok = $true
        scenarios_passed = $results.Count
        scenarios = @($results)
        test_root = $testRoot
    } | ConvertTo-Json -Depth 5
}
finally {
    Remove-Item Env:FAKE_SQLITE_QUICKCHECK_FAIL -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)
        $resolvedTest = [IO.Path]::GetFullPath($testRoot)
        if ($resolvedTest.StartsWith($resolvedTemp + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($resolvedTest) -match '^recognition-backup-health-[a-f0-9]{32}$') {
            Remove-Item -LiteralPath $resolvedTest -Recurse -Force
        }
    }
}
