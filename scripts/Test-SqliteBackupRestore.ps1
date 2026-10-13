[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$LiveRoot = "C:\Server\zhaojunjie\recognition-card-system",
    [string]$BackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite",
    [string]$RehearsalRoot = "C:\Server\zhaojunjie\restore-tests\recognition-card-system-sqlite",
    [string]$Python = "C:\Server\zhaojunjie\recognition-card-system\.venv\Scripts\python.exe",
    [string[]]$RequiredTables = @(
        "attractions",
        "employees",
        "user_accounts",
        "recognition_records",
        "deduction_records",
        "sick_leave_records",
        "audit_logs"
    ),
    [switch]$KeepCopy,
    [switch]$DryRun,
    [switch]$TestMode,
    [string]$TestRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedLiveRoot = "C:\Server\zhaojunjie\recognition-card-system"
$expectedBackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite"
$expectedRehearsalRoot = "C:\Server\zhaojunjie\restore-tests\recognition-card-system-sqlite"
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

$livePath = Get-NormalizedPath $LiveRoot
$backupPath = Get-NormalizedPath $BackupRoot
$rehearsalPath = Get-NormalizedPath $RehearsalRoot

if (-not $TestMode) {
    Assert-ExactPath $livePath $expectedLiveRoot "live root"
    Assert-ExactPath $backupPath $expectedBackupRoot "backup root"
    Assert-ExactPath $rehearsalPath $expectedRehearsalRoot "rehearsal root"
    Assert-ExactPath $Python $expectedPython "production Python"
}
else {
    if ([string]::IsNullOrWhiteSpace($TestRoot)) { throw "TestRoot is required with TestMode" }
    $testPath = Get-NormalizedPath $TestRoot
    foreach ($path in @($livePath, $backupPath, $rehearsalPath)) {
        if (-not (Test-IsChildPath $path $testPath)) { throw "Every test path must be inside TestRoot: $path" }
    }
}

if ($livePath.Equals($backupPath, [StringComparison]::OrdinalIgnoreCase) -or
    $livePath.Equals($rehearsalPath, [StringComparison]::OrdinalIgnoreCase) -or
    $backupPath.Equals($rehearsalPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Live, backup, and rehearsal roots must be distinct"
}
if ((Test-IsChildPath $rehearsalPath $livePath) -or (Test-IsChildPath $livePath $rehearsalPath)) {
    throw "Rehearsal root must not overlap live root"
}

$sourceBackup = Get-NormalizedPath $BackupFile
if (-not (Test-IsChildPath $sourceBackup $backupPath)) { throw "Backup file must be inside the dedicated backup root" }
if (-not (Test-Path -LiteralPath $sourceBackup -PathType Leaf)) { throw "Backup file not found: $sourceBackup" }
if ([IO.Path]::GetFileName($sourceBackup) -notmatch $backupNamePattern) { throw "Backup filename is not managed by the online backup script" }
$pythonPath = Resolve-PythonExecutable $Python

if ($RequiredTables.Count -eq 0) { throw "At least one required table must be specified" }
foreach ($tableName in $RequiredTables) {
    if ($tableName -notmatch '^[A-Za-z][A-Za-z0-9_]{0,62}$') { throw "Unsafe table name: $tableName" }
}

$sourceHash = (Get-FileHash -LiteralPath $sourceBackup -Algorithm SHA256).Hash.ToLowerInvariant()
$manifestPath = "$sourceBackup.manifest.json"
$manifestVerified = $false
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    if (-not (Test-IsChildPath $manifestPath $backupPath)) { throw "Manifest escaped backup root" }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace([string]$manifest.sha256)) { throw "Backup manifest has no SHA256" }
    if (-not $sourceHash.Equals(([string]$manifest.sha256).ToLowerInvariant(), [StringComparison]::Ordinal)) {
        throw "Backup SHA256 does not match its manifest"
    }
    $manifestVerified = $true
}

$stamp = Get-Date -Format "yyyyMMddTHHmmss-fff"
$rehearsalDir = Get-NormalizedPath (Join-Path $rehearsalPath ("restore-check-{0}-{1}" -f $stamp, ([Guid]::NewGuid().ToString("N").Substring(0, 8))))
$rehearsalDb = Get-NormalizedPath (Join-Path $rehearsalDir "recognition_v2.db")
if (-not (Test-IsChildPath $rehearsalDir $rehearsalPath)) { throw "Rehearsal directory escaped its dedicated root" }
if ((Test-IsChildPath $rehearsalDb $livePath) -or $rehearsalDb.Equals((Get-NormalizedPath (Join-Path $livePath "data_v2\recognition_v2.db")), [StringComparison]::OrdinalIgnoreCase)) {
    throw "Rehearsal database overlaps production"
}

if ($DryRun) {
    [pscustomobject]@{
        ok = $true
        dry_run = $true
        backup_file = $sourceBackup
        sha256 = $sourceHash
        manifest_verified = $manifestVerified
        planned_rehearsal_database = $rehearsalDb
        required_tables = $RequiredTables
    } | ConvertTo-Json -Depth 4
    return
}

if (-not (Test-Path -LiteralPath $rehearsalPath -PathType Container)) {
    New-Item -ItemType Directory -Path $rehearsalPath -Force | Out-Null
}
New-Item -ItemType Directory -Path $rehearsalDir | Out-Null

$verifyCode = @'
import json, pathlib, sqlite3, sys
database_path = pathlib.Path(sys.argv[1]).resolve()
required = sys.argv[2:]
uri = database_path.as_uri() + "?mode=ro"
connection = sqlite3.connect(uri, uri=True, timeout=60)
try:
    connection.execute("PRAGMA busy_timeout=60000")
    checks = [row[0] for row in connection.execute("PRAGMA quick_check")]
    existing = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
finally:
    connection.close()
missing = sorted(set(required) - existing)
if checks != ["ok"]:
    raise SystemExit("quick_check failed: " + "; ".join(checks))
if missing:
    raise SystemExit("required tables missing: " + ", ".join(missing))
print(json.dumps({"quick_check": "ok", "required_tables": required, "missing_tables": []}))
'@

$copyHash = $null
try {
    Copy-Item -LiteralPath $sourceBackup -Destination $rehearsalDb
    $copyHash = (Get-FileHash -LiteralPath $rehearsalDb -Algorithm SHA256).Hash.ToLowerInvariant()
    if (-not $copyHash.Equals($sourceHash, [StringComparison]::Ordinal)) { throw "Rehearsal copy SHA256 mismatch" }

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $verifyOutput = $verifyCode | & $pythonPath - $rehearsalDb @RequiredTables 2>&1
        $pythonExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($pythonExitCode -ne 0) { throw "Restore rehearsal verification failed: $($verifyOutput -join [Environment]::NewLine)" }
    $result = (($verifyOutput -join "") | ConvertFrom-Json)

    [pscustomobject]@{
        ok = $true
        dry_run = $false
        source_backup = $sourceBackup
        source_sha256 = $sourceHash
        copied_sha256 = $copyHash
        manifest_verified = $manifestVerified
        quick_check = $result.quick_check
        required_tables = $result.required_tables
        missing_tables = $result.missing_tables
        rehearsal_copy_kept = [bool]$KeepCopy
        rehearsal_directory = $(if ($KeepCopy) { $rehearsalDir } else { $null })
    } | ConvertTo-Json -Depth 4
}
finally {
    if (-not $KeepCopy -and (Test-Path -LiteralPath $rehearsalDir)) {
        $resolvedCleanup = (Resolve-Path -LiteralPath $rehearsalDir).Path
        if (-not (Test-IsChildPath $resolvedCleanup $rehearsalPath)) { throw "Unsafe rehearsal cleanup target: $resolvedCleanup" }
        if ([IO.Path]::GetFileName($resolvedCleanup) -notmatch '^restore-check-\d{8}T\d{6}-\d{3}-[a-f0-9]{8}$') {
            throw "Unsafe rehearsal cleanup directory name"
        }
        Remove-Item -LiteralPath $resolvedCleanup -Recurse -Force
    }
}
