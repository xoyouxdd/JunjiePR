#Requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo 'backend\.venv\Scripts\python.exe'
$key = Join-Path $HOME '.ssh\junjiepr_deploy'
$remote = 'Administrator@124.220.229.9'
$remoteRoot = 'C:/Server/zhaojunjie/recognition-card-system'
$remoteIncoming = "$remoteRoot/.deploy-incoming"
$sshOptions = @('-i', $key, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10')

function Invoke-Transport([string]$Executable, [string[]]$Arguments, [string]$Description) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        & $Executable @Arguments
        if ($LASTEXITCODE -eq 0) { return }
        if ($attempt -eq 3) { throw "$Description failed after 3 attempts" }
        Start-Sleep -Seconds (2 * $attempt)
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Local Python missing: $python" }
if (-not (Test-Path -LiteralPath $key -PathType Leaf)) { throw "Deployment SSH key missing: $key" }
Push-Location $repo
try {
    $status = & git status --porcelain
    if ($LASTEXITCODE -ne 0 -or $status) { throw 'Release requires a clean Git working tree' }
    $commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') { throw 'Cannot determine Git commit' }

    & $python backend\tests\run_all.py
    if ($LASTEXITCODE -ne 0) { throw 'Full test run failed' }
    & $python scripts\build_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release build failed' }

    $versionMatch = Select-String -LiteralPath (Join-Path $repo 'backend\app\version.py') -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $versionMatch) { throw 'APP_VERSION missing' }
    $version = $versionMatch.Matches[0].Groups[1].Value
    $package = Get-Item -LiteralPath (Join-Path $repo "recognition-v$version.zip")
    $sha = (Get-FileHash -LiteralPath $package.FullName -Algorithm SHA256).Hash.ToUpperInvariant()

    Add-Type -AssemblyName System.IO.Compression
    $archive = [IO.Compression.ZipFile]::OpenRead($package.FullName)
    try {
        $manifestEntry = $archive.GetEntry('release-manifest.json')
        if (-not $manifestEntry) { throw 'Package manifest missing' }
        $reader = [IO.StreamReader]::new($manifestEntry.Open())
        try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json }
        finally { $reader.Dispose() }
    }
    finally { $archive.Dispose() }
    if ($manifest.git_commit -ne $commit -or $manifest.app_version -ne $version) {
        throw 'Package manifest does not match current commit and version'
    }

    $remoteScript = "$remoteIncoming/junjiepr-server-release.py"
    $remotePackage = "$remoteIncoming/$($package.Name)"
    Invoke-Transport 'scp' ($sshOptions + @((Join-Path $repo 'scripts\server_release.py'), "${remote}:$remoteScript")) 'Server deployment script upload'
    Invoke-Transport 'scp' ($sshOptions + @($package.FullName, "${remote}:$remotePackage")) 'Release package upload'

    $remotePython = 'C:\Server\zhaojunjie\recognition-card-system\.venv\Scripts\python.exe'
    $remoteScriptWin = 'C:\Server\zhaojunjie\recognition-card-system\.deploy-incoming\junjiepr-server-release.py'
    $remotePackageWin = "C:\Server\zhaojunjie\recognition-card-system\.deploy-incoming\$($package.Name)"
    $command = "$remotePython $remoteScriptWin $remotePackageWin $commit $sha"
    Invoke-Transport 'ssh' ($sshOptions + @($remote, "$command --preflight")) 'Server release preflight'
    & ssh @sshOptions $remote $command
    if ($LASTEXITCODE -ne 0) { throw 'Server deployment failed; inspect rollback output' }

    $readback = Join-Path $env:TEMP "junjiepr-release-manifest-$([Guid]::NewGuid().ToString('N')).json"
    try {
        Invoke-Transport 'scp' ($sshOptions + @("${remote}:$remoteRoot/release-manifest.json", $readback)) 'Remote manifest readback'
        $deployed = Get-Content -LiteralPath $readback -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($deployed.git_commit -ne $commit -or $deployed.app_version -ne $version) {
            throw 'Remote manifest does not match released commit and version'
        }
    }
    finally { if (Test-Path -LiteralPath $readback) { Remove-Item -LiteralPath $readback -Force } }
    $health = Invoke-RestMethod -Uri 'https://124.220.229.9:28176/health' -SkipCertificateCheck -TimeoutSec 15
    if ($health.ok -ne $true -or $health.version -ne $version) { throw 'Public health readback failed' }
    Write-Output "RELEASE_VERIFIED version=$version commit=$commit package_sha256=$sha"
}
finally { Pop-Location }
