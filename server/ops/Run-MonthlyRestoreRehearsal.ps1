[CmdletBinding()]
param(
    [string]$BackupRoot = "C:\Server\zhaojunjie\backups\recognition-card-system-sqlite",
    [string]$RehearsalRoot = "C:\Server\zhaojunjie\restore-tests\recognition-card-system-sqlite",
    [string]$RestoreScript = "C:\Server\zhaojunjie\recognition-card-system\ops\Test-SqliteBackupRestore.ps1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RestoreScript -PathType Leaf)) { throw "Restore rehearsal script was not found." }
if (-not (Test-Path -LiteralPath $BackupRoot -PathType Container)) { throw "Backup root was not found." }

$backup = Get-ChildItem -LiteralPath $BackupRoot -File -Filter "recognition_v2-*.db" |
    Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if ($null -eq $backup) { throw "No managed SQLite backup is available for the rehearsal." }

# The delegated script copies the backup into a separate rehearsal directory,
# checks its SHA-256 and SQLite integrity, and removes the temporary copy by default.
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $RestoreScript `
    -BackupFile $backup.FullName -BackupRoot $BackupRoot -RehearsalRoot $RehearsalRoot
if ($LASTEXITCODE -ne 0) { throw "Restore rehearsal failed." }
