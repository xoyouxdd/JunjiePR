"""Exercise actual publisher upload/hash checks with every transport mocked."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import pytest


PROJECT = Path(__file__).resolve().parents[2]
PUBLISHER = PROJECT / "scripts" / "Publish-RecognitionRelease.ps1"


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def release_inputs():
    folder = PROJECT / "output" / "refactor-validation" / "phase3" / ("publisher-" + uuid4().hex)
    scripts = folder / "scripts"
    scripts.mkdir(parents=True)
    entries = []
    for name in ("server_release.py", "deployment_runtime.py"):
        content = ("isolated " + name).encode("utf-8")
        (scripts / name).write_bytes(content)
        entries.append({"path": "scripts/" + name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest().upper()})
    yield folder, entries
    # All files belong to this test's ordinary-ACL directory under the project.
    assert folder.resolve().is_relative_to(PROJECT / "output")
    shutil.rmtree(folder)


def exercise(folder, entries, fault=""):
    shell = shutil.which("pwsh")
    assert shell, "PowerShell 7 is required to validate the publisher contract"
    source = PUBLISHER.read_text(encoding="utf-8-sig")
    block = source[source.index('    $deploymentId ='):source.index("    $remotePython =")]
    inputs = folder / "input.json"
    inputs.write_text(json.dumps({"files": entries}), encoding="utf-8")
    harness = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$repo = REPO
$manifest = Get-Content -LiteralPath INPUTS -Raw -Encoding UTF8 | ConvertFrom-Json
$remoteIncoming = 'C:/isolated-release/.deploy-incoming'
$remote = 'mock-only'
$sshOptions = @()
$commit = 'a' * 40
$sha = 'b' * 64
$package = [pscustomobject]@{Name='candidate.zip';BaseName='candidate';FullName='mock-only-package.zip'}
$script:transportCalls = [Collections.Generic.List[object]]::new()
$fault = FAULT
function Invoke-Transport([string]$Executable, [string[]]$Arguments, [string]$Description) {
    $script:transportCalls.Add([pscustomobject]@{executable=$Executable;arguments=$Arguments;description=$Description})
    if ($Description -eq 'Deployment script directory preparation') { return }
    if ($Executable -eq 'ssh') {
        $encoded = ($Arguments[-1] -split ' ')[-1]
        $query = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded))
        $name = if ($query.Contains('deployment_runtime.py')) {'deployment_runtime.py'} else {'server_release.py'}
        if ($fault -eq 'runtime-readback' -and $name -eq 'deployment_runtime.py') { return ('0' * 64) }
        if ($fault -eq 'malformed-readback') { return 'not-a-hash' }
        return (Get-FileHash -LiteralPath (Join-Path $repo "scripts/$name") -Algorithm SHA256).Hash
    }
}
$failure = $null
try { BODY } catch { $failure = $_.Exception.Message }
[pscustomobject]@{error=$failure;calls=@($script:transportCalls)} | ConvertTo-Json -Depth 7 -Compress
"""
    harness = harness.replace("REPO", ps_literal(folder)).replace("INPUTS", ps_literal(inputs)).replace("FAULT", ps_literal(fault)).replace("BODY", block)
    result = subprocess.run([shell, "-NoProfile", "-Command", harness], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_matching_package_sources_are_uploaded_and_verified_before_package(release_inputs):
    result = exercise(*release_inputs)
    assert result["error"] is None
    assert [row["executable"] for row in result["calls"]] == ["ssh", "scp", "ssh", "scp", "ssh", "scp"]
    assert result["calls"][-1]["description"] == "Release package upload"
    assert "deployment_runtime.py" in result["calls"][3]["arguments"][0]
    assert "a" * 40 + "-" + "b" * 64 in result["calls"][1]["arguments"][1]
    assert "b" * 64 + ".zip" in result["calls"][-1]["arguments"][1]


def test_local_script_must_match_manifest_before_upload(release_inputs):
    folder, entries = release_inputs
    entries[0]["sha256"] = "0" * 64
    result = exercise(folder, entries)
    assert "does not match packaged source" in result["error"]
    assert not result["calls"]


@pytest.mark.parametrize("fault", ["runtime-readback", "malformed-readback"])
def test_wrong_remote_source_never_uploads_package_or_reaches_deployment(release_inputs, fault):
    result = exercise(*release_inputs, fault=fault)
    assert "hash mismatch" in result["error"]
    assert all(row["description"] != "Release package upload" for row in result["calls"])
