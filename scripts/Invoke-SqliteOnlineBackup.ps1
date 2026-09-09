[CmdletBinding()]
param(
    [string]$LiveRoot = "C:\Server\zhaojunjie\recognition-card-system\backend",
    [string]$BackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite",
    [string]$Python = "C:\Server\zhaojunjie\recognition-card-system\backend\.venv\Scripts\python.exe",
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30,
    [switch]$DryRun,
    [switch]$TestMode,
    [string]$TestRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedLiveRoot = "C:\Server\zhaojunjie\recognition-card-system\backend"
$expectedBackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite"
$expectedPython = "C:\Server\zhaojunjie\recognition-card-system\backend\.venv\Scripts\python.exe"
$backupNamePattern = '^recognition_v2-\d{8}T\d{6}-\d{3}\.db(?:\.manifest\.json)?$'

function Get-NormalizedPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { throw "Path must not be empty" }
    return [IO.Path]::GetFullPath($Path).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Test-IsChildPath([string]$Child, [string]$Parent) {
    $childPath = Get-NormalizedPath $Child
    $parentPath = Get-NormalizedPath $Parent
    $prefix = $parentPath + [IO.Path]::DirectorySeparatorChar
    return $childPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
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

$livePath = Get-NormalizedPath $LiveRoot
$backupPath = Get-NormalizedPath $BackupRoot

if (-not $TestMode) {
    Assert-ExactPath $livePath $expectedLiveRoot "live root"
    Assert-ExactPath $backupPath $expectedBackupRoot "backup root"
    Assert-ExactPath $Python $expectedPython "production Python"
}
else {
    if ([string]::IsNullOrWhiteSpace($TestRoot)) { throw "TestRoot is required with TestMode" }
    $testPath = Get-NormalizedPath $TestRoot
    if (-not (Test-IsChildPath $livePath $testPath)) { throw "Test live root must be inside TestRoot" }
    if (-not (Test-IsChildPath $backupPath $testPath)) { throw "Test backup root must be inside TestRoot" }
    if ($livePath.Equals($backupPath, [StringComparison]::OrdinalIgnoreCase)) { throw "Live root and backup root must differ" }
}

if (-not (Test-Path -LiteralPath $livePath -PathType Container)) { throw "Live root not found: $livePath" }
$sourceDb = Get-NormalizedPath (Join-Path $livePath "data_v2\recognition_v2.db")
if (-not (Test-IsChildPath $sourceDb $livePath)) { throw "Database path escaped live root" }
if (-not (Test-Path -LiteralPath $sourceDb -PathType Leaf)) { throw "Source database not found: $sourceDb" }
$pythonPath = Resolve-PythonExecutable $Python

$verifyCode = @'
import json, pathlib, sqlite3, sys
source_uri = pathlib.Path(sys.argv[1]).resolve().as_uri() + "?mode=ro"
connection = sqlite3.connect(source_uri, uri=True, timeout=60)
try:
    connection.execute("PRAGMA busy_timeout=60000")
    rows = [row[0] for row in connection.execute("PRAGMA quick_check")]
finally:
    connection.close()
if rows != ["ok"]:
    raise SystemExit("quick_check failed: " + "; ".join(rows))
print(json.dumps({"quick_check": "ok"}))
'@

function Invoke-SourceQuickCheck {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = $verifyCode | & $pythonPath - $sourceDb 2>&1
        $pythonExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($pythonExitCode -ne 0) { throw "Source database quick_check failed: $($output -join [Environment]::NewLine)" }
    return (($output -join "") | ConvertFrom-Json)
}

function Get-ExpiredBackupFiles {
    if (-not (Test-Path -LiteralPath $backupPath -PathType Container)) { return @() }
    $cutoff = [DateTime]::UtcNow.AddDays(-$RetentionDays)
    return @(
        Get-ChildItem -LiteralPath $backupPath -File | Where-Object {
            $_.Name -match $backupNamePattern -and $_.LastWriteTimeUtc -lt $cutoff
        }
    )
}

$sourceCheck = Invoke-SourceQuickCheck
$expired = @(Get-ExpiredBackupFiles)
foreach ($item in $expired) {
    $resolvedItem = Get-NormalizedPath $item.FullName
    if (-not (Test-IsChildPath $resolvedItem $backupPath)) { throw "Retention target escaped backup root: $resolvedItem" }
    if ($item.Name -notmatch $backupNamePattern) { throw "Retention target name is not managed by this script: $($item.Name)" }
}

if ($DryRun) {
    [pscustomobject]@{
        ok = $true
        dry_run = $true
        source_db = $sourceDb
        source_quick_check = $sourceCheck.quick_check
        backup_root = $backupPath
        retention_days = $RetentionDays
        would_delete = @($expired | ForEach-Object { $_.Name })
    } | ConvertTo-Json -Depth 4
    return
}

if (-not (Test-Path -LiteralPath $backupPath -PathType Container)) {
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
}
if (-not (Test-IsChildPath (Join-Path $backupPath "probe") $backupPath)) { throw "Invalid backup root" }

$stamp = Get-Date -Format "yyyyMMddTHHmmss-fff"
$backupFile = Get-NormalizedPath (Join-Path $backupPath "recognition_v2-$stamp.db")
$partialFile = "$backupFile.partial-$PID"
$manifestFile = "$backupFile.manifest.json"
foreach ($target in @($backupFile, $partialFile, $manifestFile)) {
    if (-not (Test-IsChildPath $target $backupPath)) { throw "Backup target escaped backup root: $target" }
    if (Test-Path -LiteralPath $target) { throw "Backup target already exists: $target" }
}

$backupCode = @'
import json, pathlib, sqlite3, sys
source_path, target_path = sys.argv[1], sys.argv[2]
source_uri = pathlib.Path(source_path).resolve().as_uri() + "?mode=ro"
source = sqlite3.connect(source_uri, uri=True, timeout=60)
target = sqlite3.connect(target_path, timeout=60)
try:
    source.execute("PRAGMA busy_timeout=60000")
    target.execute("PRAGMA busy_timeout=60000")
    source.backup(target, pages=256, sleep=0.10)
    target.commit()
    rows = [row[0] for row in target.execute("PRAGMA quick_check")]
finally:
    target.close()
    source.close()
if rows != ["ok"]:
    raise SystemExit("backup quick_check failed: " + "; ".join(rows))
print(json.dumps({"quick_check": "ok", "sqlite_version": sqlite3.sqlite_version}))
'@

$backupComplete = $false
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $backupOutput = $backupCode | & $pythonPath - $sourceDb $partialFile 2>&1
        $pythonExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($pythonExitCode -ne 0) { throw "SQLite online backup failed: $($backupOutput -join [Environment]::NewLine)" }
    $backupResult = (($backupOutput -join "") | ConvertFrom-Json)
    Move-Item -LiteralPath $partialFile -Destination $backupFile

    $hash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $backupInfo = Get-Item -LiteralPath $backupFile
    $manifest = [ordered]@{
        schema_version = 1
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        source_database = "data_v2/recognition_v2.db"
        backup_file = $backupInfo.Name
        size_bytes = $backupInfo.Length
        sha256 = $hash
        quick_check = $backupResult.quick_check
        sqlite_version = $backupResult.sqlite_version
        retention_days = $RetentionDays
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestFile -Encoding UTF8
    $backupComplete = $true

    $deleted = @()
    foreach ($item in $expired) {
        $resolvedItem = Get-NormalizedPath $item.FullName
        if (-not (Test-IsChildPath $resolvedItem $backupPath)) { throw "Unsafe retention target: $resolvedItem" }
        if ($item.Name -notmatch $backupNamePattern) { throw "Unsafe retention filename: $($item.Name)" }
        Remove-Item -LiteralPath $resolvedItem -Force
        $deleted += $item.Name
    }

    [pscustomobject]@{
        ok = $true
        dry_run = $false
        backup_file = $backupFile
        manifest_file = $manifestFile
        quick_check = $backupResult.quick_check
        sha256 = $hash
        size_bytes = $backupInfo.Length
        retention_days = $RetentionDays
        deleted = $deleted
    } | ConvertTo-Json -Depth 4
}
catch {
    if (-not $backupComplete) {
        foreach ($partialTarget in @($partialFile, $backupFile, $manifestFile)) {
            if ((Test-Path -LiteralPath $partialTarget) -and (Test-IsChildPath $partialTarget $backupPath)) {
                Remove-Item -LiteralPath $partialTarget -Force -ErrorAction SilentlyContinue
            }
        }
    }
    throw
}
