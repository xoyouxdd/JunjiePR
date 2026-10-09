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

    # The packager owns the full test gate and refuses to build on failure.
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

    $deploymentId = "$commit-$sha"
    $remoteSources = "$remoteIncoming/$deploymentId"
    $remoteScript = "$remoteSources/junjiepr-server-release.py"
    $remoteRuntime = "$remoteSources/deployment_runtime.py"
    $remotePackage = "$remoteIncoming/$($package.BaseName)-$sha.zip"
    $deploymentFiles = @(
        @{ LocalName = 'server_release.py'; RemotePath = $remoteScript },
        @{ LocalName = 'deployment_runtime.py'; RemotePath = $remoteRuntime }
    )
    foreach ($deploymentFile in $deploymentFiles) {
        $localScript = Get-Item -LiteralPath (Join-Path $repo "scripts\$($deploymentFile.LocalName)")
        $manifestFiles = @($manifest.files | Where-Object { $_.path -eq "scripts/$($deploymentFile.LocalName)" })
        if ($manifestFiles.Count -ne 1) { throw "Deployment script missing or duplicated in package manifest: $($deploymentFile.LocalName)" }
        $localScriptHash = (Get-FileHash -LiteralPath $localScript.FullName -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($localScriptHash -ne $manifestFiles[0].sha256.ToUpperInvariant() -or $localScript.Length -ne $manifestFiles[0].size) {
            throw "Deployment script does not match packaged source: $($deploymentFile.LocalName)"
        }
        $deploymentFile.LocalPath = $localScript.FullName
        $deploymentFile.ExpectedHash = $localScriptHash
    }
    $remoteSourcesWin = $remoteSources.Replace('/', '\')
    $prepareSources = "New-Item -ItemType Directory -Path '$remoteSourcesWin' -Force | Out-Null"
    $prepareCommand = 'powershell.exe -NoProfile -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($prepareSources))
    Invoke-Transport 'ssh' ($sshOptions + @($remote, $prepareCommand)) 'Deployment script directory preparation'
    foreach ($deploymentFile in $deploymentFiles) {
        $localScriptHash = $deploymentFile.ExpectedHash
        Invoke-Transport 'scp' ($sshOptions + @($deploymentFile.LocalPath, "${remote}:$($deploymentFile.RemotePath)")) "Deployment script upload: $($deploymentFile.LocalName)"
        $remotePathWin = $deploymentFile.RemotePath.Replace('/', '\')
        $hashQuery = "(Get-FileHash -LiteralPath '$remotePathWin' -Algorithm SHA256).Hash"
        $hashCommand = 'powershell.exe -NoProfile -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($hashQuery))
        $hashReadback = @(Invoke-Transport 'ssh' ($sshOptions + @($remote, $hashCommand)) "Deployment script hash readback: $($deploymentFile.LocalName)")
        $uploadedHash = ($hashReadback -join '').Trim()
        if ($uploadedHash -notmatch '^[0-9A-Fa-f]{64}$' -or $uploadedHash.ToUpperInvariant() -ne $localScriptHash) {
            throw "Uploaded deployment script hash mismatch: $($deploymentFile.LocalName)"
        }
    }
    Invoke-Transport 'scp' ($sshOptions + @($package.FullName, "${remote}:$remotePackage")) 'Release package upload'

    $remotePython = 'C:\Server\zhaojunjie\recognition-card-system\.venv\Scripts\python.exe'
    $remoteScriptWin = $remoteScript.Replace('/', '\')
    $remotePackageWin = $remotePackage.Replace('/', '\')
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
