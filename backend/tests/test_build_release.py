from __future__ import annotations

import importlib.util
import re
import os
import subprocess
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("missing", ["Deploy-RecognitionRelease.ps1", "server_release.py", "deployment_runtime.py"])
def test_missing_deployment_dependency_refuses_source_collection(tmp_path, monkeypatch, missing):
    mod = load_build_release()
    backend = tmp_path / "backend"
    backend.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in mod.SCRIPT_FILES:
        (scripts / name).write_text("deployment source", encoding="utf-8")
    (scripts / missing).unlink()
    monkeypatch.setattr(mod, "BACKEND_ROOT", backend)
    monkeypatch.setattr(mod, "SCRIPTS_ROOT", scripts)
    with pytest.raises(RuntimeError, match="required deployment scripts missing"):
        mod.release_sources()


def load_build_release():
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("linked_root", [False, True])
def test_release_source_reparse_is_rejected_before_traversing_external_tree(tmp_path, monkeypatch, linked_root):
    mod = load_build_release()
    backend = tmp_path / "backend"
    backend.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in mod.SCRIPT_FILES:
        (scripts / name).write_text("deployment source", encoding="utf-8")
    outside = tmp_path / "private"
    outside.mkdir()
    sentinel = outside / "sentinel-private.txt"
    sentinel.write_text("must not enter release", encoding="utf-8")
    if linked_root:
        link = backend / "app"
    else:
        app = backend / "app"
        app.mkdir()
        link = app / "linked-outside"
    if os.name == "nt":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(mod, "BACKEND_ROOT", backend)
    monkeypatch.setattr(mod, "SCRIPTS_ROOT", scripts)
    try:
        with pytest.raises(RuntimeError, match="symlinks or reparse points"):
            mod.release_sources()
        assert sentinel.read_text(encoding="utf-8") == "must not enter release"
    finally:
        # Remove only this link; the target's sentinel remains untouched.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_release_package_excludes_local_secrets_and_uses_app_version(tmp_path: Path) -> None:
    mod = load_build_release()
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "secrets.json").write_text("{}", encoding="utf-8")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("ok", encoding="utf-8")
    (tmp_path / "app" / "service.log").write_text("log", encoding="utf-8")
    data = tmp_path / "data_v2"
    data.mkdir()
    (data / "recognition_v2.db").write_bytes(b"db")
    local_data = tmp_path / ".codex-local-data-test"
    local_data.mkdir()
    (local_data / "runtime.json").write_text("{}", encoding="utf-8")

    assert mod.should_include(tmp_path / ".env", tmp_path) is False
    assert mod.should_include(tmp_path / "secrets.json", tmp_path) is False
    assert mod.should_include(venv / "pyvenv.cfg", tmp_path) is False
    assert mod.should_include(tmp_path / "app" / "service.log", tmp_path) is False
    assert mod.should_include(data / "recognition_v2.db", tmp_path) is False
    assert mod.should_include(local_data / "runtime.json", tmp_path) is False
    assert mod.should_include(app_dir / "main.py", tmp_path) is True
    version = mod.read_app_version()
    assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", version), version
    assert mod.package_name(version, version.replace(".", "")[:8]) == f"recognition-v{version}.zip"


def test_release_whitelist_excludes_tests_and_docs() -> None:
    mod = load_build_release()
    names = {archive_name for _, archive_name in mod.release_sources()}

    assert "backend/app/main.py" in names
    assert "backend/requirements.txt" in names
    assert {"scripts/Deploy-RecognitionRelease.ps1", "scripts/server_release.py", "scripts/deployment_runtime.py"} <= names
    # Test-only dependencies must never reach a production server.
    assert "backend/requirements-dev.txt" not in names
    assert not any("requirements-dev" in name for name in names)
    assert "scripts/seed_level_accounts.py" not in names
    assert not any(name.startswith("backend/tests/") for name in names)
    assert not any(name.startswith("scripts/tests/") for name in names)
    assert not any(name.startswith("docs/") for name in names)

    # The production lock carries runtime deps only; test deps live in the dev lock,
    # which inherits it so both files stay on one set of pinned versions.
    production = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
    development = (ROOT / "backend" / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "pytest" not in production
    assert "httpx" not in production
    assert "-r requirements.txt" in development
    assert "pytest==8.3.4" in development
    assert "httpx==0.28.1" in development

    mod.ensure_release_contract(mod.read_app_version())
    notes = mod.render_release_notes(mod.read_app_version()).decode("utf-8")
    assert f"# 更新记录 V{mod.read_app_version()}" in notes
    # The notes must render the current release's own items, not a stale copy.
    from app.changelog import RELEASES  # render_release_notes put backend/ on sys.path

    for item in RELEASES[0]["items"]:
        assert item["summary"] in notes


def test_release_manifest_binds_each_file_to_commit_and_hash(tmp_path: Path) -> None:
    mod = load_build_release()
    source = tmp_path / "example.txt"
    source.write_text("release evidence", encoding="utf-8")

    manifest = mod.build_manifest([(source, "docs/example.txt")], "1.2.3", "a" * 40)

    assert manifest["app_version"] == "1.2.3"
    assert manifest["git_commit"] == "a" * 40
    assert manifest["files"] == [
        {
            "path": "docs/example.txt",
            "size": len(b"release evidence"),
            "sha256": "55721654F9F5EE40E2ACD466F488DE43BCF890EE0D7FCB20272C5BFD0708EA8C",
        }
    ]


def test_deployment_script_requires_approved_commit_and_external_health_check() -> None:
    source = (ROOT / "scripts" / "Deploy-RecognitionRelease.ps1").read_text(encoding="utf-8")
    assert "[string]$ExpectedGitCommit" in source
    assert '$manifest.git_commit -ne $ExpectedGitCommit' in source
    assert "https://124.220.229.9:28176/health" in source
    assert "Confirm-ExternalHealth $manifest.app_version" in source
    assert "-SkipCertificateCheck" in source


def test_deployment_script_targets_the_real_production_layout() -> None:
    source = (ROOT / "scripts" / "Deploy-RecognitionRelease.ps1").read_text(encoding="utf-8")
    assert '$LiveRoot = "C:\\Server\\zhaojunjie\\recognition-card-system\\backend"' in source
    assert (
        '$BackupScript = "C:\\Server\\zhaojunjie\\recognition-card-system\\scripts'
        '\\Invoke-SqliteOnlineBackup.ps1"'
    ) in source


def test_deployment_script_rolls_back_from_the_moment_the_live_app_moves() -> None:
    source = (ROOT / "scripts" / "Deploy-RecognitionRelease.ps1").read_text(encoding="utf-8")
    # The rollback gate must not depend on a flag set after the swap completes.
    assert "$swapped" not in source
    assert 'if ($oldAppMoved -and (Test-Path -LiteralPath (Join-Path $rollback "app")))' in source
    swap = source.index('Move-Item -LiteralPath $oldApp -Destination (Join-Path $rollback "app")')
    flag = source.index("$oldAppMoved = $true")
    restore = source.index('Move-Item -LiteralPath $newApp -Destination $oldApp')
    assert swap < flag < restore
    # The rollback path must never let Stop-App replace the original failure.
    assert "try { Stop-App }" in source
    assert source.rindex("throw $failure") > source.index("try { Stop-App }")


def test_repository_configures_no_automated_workflows() -> None:
    """The project deliberately builds and deploys by hand; CI is not used.

    Releases are built locally from a clean tree and deployed only after an
    explicit human instruction, so no workflow may reintroduce automation.
    """
    workflows = ROOT / ".github" / "workflows"
    found = sorted(p.name for p in workflows.glob("*.y*ml")) if workflows.is_dir() else []
    assert not found, f"本项目约定不使用 CI，但发现工作流: {found}"

def test_packaging_runs_the_full_suite_first() -> None:
    """Without CI, packaging is the last gate that can catch a broken build."""
    source = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
    assert "def run_test_suite()" in source
    assert "tests" in source and "run_all.py" in source
    assert "refusing to build a release package" in source
    body = source[source.index("def main() -> None:"):]
    assert body.index("run_test_suite()") < body.index("release_sources()")
