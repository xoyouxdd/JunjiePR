[CmdletBinding()]
param(
    [string]$LiveRoot = "C:\Server\zhaojunjie\recognition-card-system",
    [string]$BackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite",
    [string]$Python = "C:\Server\zhaojunjie\recognition-card-system\.venv\Scripts\python.exe",
    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30,
    [switch]$IncludeRecoveryBundle,
    [switch]$DryRun,
    [switch]$TestMode,
    [string]$TestRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$expectedLiveRoot = "C:\Server\zhaojunjie\recognition-card-system"
$expectedBackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite"
$expectedPython = "C:\Server\zhaojunjie\recognition-card-system\.venv\Scripts\python.exe"
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
$backupHelper = Join-Path $PSScriptRoot 'sqlite_backup.py'
if (-not (Test-Path -LiteralPath $backupHelper -PathType Leaf)) { throw "Shared SQLite backup helper not found: $backupHelper" }

function Invoke-BackupCore([string[]]$Arguments) {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $pythonPath -B $backupHelper @Arguments 2>&1
        $pythonExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($pythonExitCode -ne 0) { throw "SQLite $($Arguments[0]) failed: $($output -join [Environment]::NewLine)" }
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

$expired = @(Get-ExpiredBackupFiles)
foreach ($item in $expired) {
    $resolvedItem = Get-NormalizedPath $item.FullName
    if (-not (Test-IsChildPath $resolvedItem $backupPath)) { throw "Retention target escaped backup root: $resolvedItem" }
    if ($item.Name -notmatch $backupNamePattern) { throw "Retention target name is not managed by this script: $($item.Name)" }
}

if ($DryRun) {
    $sourceCheck = Invoke-BackupCore @('check', $sourceDb)
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
$manifestFile = "$backupFile.manifest.json"
foreach ($target in @($backupFile, $manifestFile)) {
    if (-not (Test-IsChildPath $target $backupPath)) { throw "Backup target escaped backup root: $target" }
    if (Test-Path -LiteralPath $target) { throw "Backup target already exists: $target" }
}

$backupResult = Invoke-BackupCore @('backup', $sourceDb, $backupFile, [string]$RetentionDays)

$recoveryResult = $null
if ($IncludeRecoveryBundle) {
    $helper = Join-Path $PSScriptRoot 'recovery_bundle.py'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'Recovery bundle helper missing' }
    if (-not $TestMode) {
        $configDir = Join-Path $livePath 'ops\recovery-config'
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
        $scheduler = New-Object -ComObject Schedule.Service
        $scheduler.Connect()
        foreach ($taskName in @('RecognitionCardSystem','RecognitionCardSystemWatchdog','RecognitionCardSystem-SQLiteBackup','RecognitionCardSystem-SQLiteBackupHealth')) {
            $taskXml = [string]$scheduler.GetFolder('\').GetTask($taskName).Xml
            [IO.File]::WriteAllText((Join-Path $configDir "$taskName.xml"), $taskXml, [Text.UTF8Encoding]::new($false))
        }
        $launchDir = Join-Path $configDir 'service-scripts'
        New-Item -ItemType Directory -Path $launchDir -Force | Out-Null
        foreach ($launchName in @('run_public_server.ps1','ensure_public_server.ps1')) {
            $launchSource = Join-Path $livePath ('scripts\'+$launchName)
            if (Test-Path -LiteralPath $launchSource -PathType Leaf) { Copy-Item -LiteralPath $launchSource -Destination (Join-Path $launchDir $launchName) -Force }
        }
        $runtime = [ordered]@{
            environment = 'production'
            live_root = $livePath
            backup_root = $backupPath
            python = $pythonPath
            python_version = [string](& $pythonPath --version)
            environment_variable_names = @('RECOGNITION_ENVIRONMENT','RECOGNITION_V2_DATA_DIR','RECOGNITION_BACKUP_HEALTH_STATUS_FILE')
        }
        $runtime | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $configDir 'runtime.json') -Encoding UTF8
        & $pythonPath -m pip freeze --all | Set-Content -LiteralPath (Join-Path $configDir 'installed-packages.txt') -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { throw 'Installed dependency inventory failed' }
    }
    $recoveryOutput = & $pythonPath $helper capture --live-root $livePath --database $backupFile --store (Join-Path $backupPath 'recovery')
    if ($LASTEXITCODE -ne 0) { throw 'Recovery capture failed; database snapshot retained, old backups not pruned' }
    $recoveryResult = (($recoveryOutput -join '') | ConvertFrom-Json)
    if (-not $recoveryResult.ok) { throw 'Recovery capture validation failed' }
}

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
    sha256 = $backupResult.sha256
    size_bytes = $backupResult.size_bytes
    retention_days = $RetentionDays
    deleted = $deleted
    recovery_bundle = $recoveryResult
} | ConvertTo-Json -Depth 4
