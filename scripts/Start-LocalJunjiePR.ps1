$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $workspace "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"
$dataDir = Join-Path $backend ".codex-local-data-20260910"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Local Python environment not found: $python"
}

$env:RECOGNITION_V2_DATA_DIR = $dataDir

$listener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
if ($listener) {
    $listener.OwningProcess | Sort-Object -Unique | ForEach-Object {
        $process = Get-Process -Id $_ -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -eq "python") {
            Stop-Process -Id $_ -Force
        }
    }
    Start-Sleep -Seconds 1
}

Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000") `
    -WorkingDirectory $backend `
    -WindowStyle Hidden
