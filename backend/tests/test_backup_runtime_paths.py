"""Backups must target the application layout actually used by deployment."""
import ast
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def deployment_root():
    tree = ast.parse((SCRIPTS / "server_release.py").read_text(encoding="utf-8"))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "ROOT" for t in n.targets))
    return PureWindowsPath(ast.literal_eval(assignment.value.args[0]))


@pytest.mark.parametrize("name,has_live_root", [
    ("Invoke-SqliteOnlineBackup.ps1", True),
    ("Test-SqliteBackupHealth.ps1", False),
    ("Test-SqliteBackupRestore.ps1", True),
])
def test_production_backup_defaults_and_guards_match_deployment(name, has_live_root):
    source = (SCRIPTS / name).read_text(encoding="utf-8")
    expected = {"Python": deployment_root() / ".venv/Scripts/python.exe"}
    if has_live_root:
        expected["LiveRoot"] = deployment_root()
    for parameter, path in expected.items():
        for pattern in [rf'\[string\]\${parameter}\s*=\s*"([^"]+)"',
                        rf'\$expected{parameter}\s*=\s*"([^"]+)"']:
            assert PureWindowsPath(re.search(pattern, source).group(1)) == path


def run_ps(name, *arguments, script_dir=SCRIPTS):
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(script_dir / name), *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        env={**os.environ, "PSModulePath": str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules")})
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.lstrip("\ufeff"))


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"), reason="Windows operations scripts")
def test_real_flat_layout_backup_health_and_restore_preserve_source(tmp_path):
    live, backups, restore = tmp_path / "live", tmp_path / "backups", tmp_path / "restore"
    (live / "app").mkdir(parents=True)
    # Scheduled operations live outside the app tree, which may have just
    # rolled back to a release that does not ship the shared helper.
    ops = live / "ops"
    ops.mkdir()
    script_name = "Invoke-SqliteOnlineBackup.ps1"
    shutil.copy2(SCRIPTS / script_name, ops / script_name)
    source_helper = SCRIPTS.parent / "backend/app/sqlite_backup.py"
    installed_helper = ops / "sqlite_backup.py"
    shutil.copy2(source_helper, installed_helper)
    assert hashlib.sha256(installed_helper.read_bytes()).digest() == hashlib.sha256(source_helper.read_bytes()).digest()
    assert not (live / "app/sqlite_backup.py").exists()
    install = run_ps("Install-DailyBackupTask.ps1", "-BackupScript", ops / script_name,
        "-DryRun", "-TestMode", "-TestRoot", tmp_path)
    assert install["ok"] and str(ops / script_name) in install["definition"]["arguments"]
    (live / "data_v2").mkdir()
    (live / ".venv").mkdir()
    backups.mkdir()
    database = live / "data_v2/recognition_v2.db"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY, content TEXT)")
        db.execute("INSERT INTO fixture VALUES (1, 'isolated backup evidence')")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    common = ["-Python", sys.executable, "-TestMode", "-TestRoot", tmp_path, "-BackupRoot", backups]
    old = backups / "recognition_v2-20200101T000000-000.db"
    unmanaged = backups / "manual-evidence.db"
    for copy in (old, unmanaged):
        copy.write_bytes(database.read_bytes())
        os.utime(copy, (1577836800, 1577836800))
    plan = run_ps(script_name, "-LiveRoot", live, "-DryRun", *common, script_dir=ops)
    assert plan["source_quick_check"] == "ok" and plan["would_delete"] == [old.name]
    assert sorted(backups.iterdir()) == sorted([old, unmanaged])
    backup = run_ps(script_name, "-LiveRoot", live, *common, script_dir=ops)
    assert backup["ok"] and backup["quick_check"] == "ok" and backup["deleted"] == [old.name]
    assert not old.exists() and unmanaged.is_file()
    assert Path(backup["backup_file"]).is_file()
    task_status = tmp_path / "task-status.json"
    task_status.write_text(json.dumps({"exists": True, "enabled": True, "state": "Ready",
        "last_run_time_utc": datetime.now(timezone.utc).isoformat(),
        "next_run_time_utc": None, "last_task_result": 0}), encoding="utf-8")
    health = run_ps("Test-SqliteBackupHealth.ps1", "-TaskStatusFile", task_status, *common)
    assert health["ok"] and not health["issues"]
    assert health["latest_backup"]["manifest_verified"] and health["latest_backup"]["quick_check"] == "ok"
    restored = run_ps("Test-SqliteBackupRestore.ps1", "-LiveRoot", live,
        "-BackupFile", backup["backup_file"], "-RehearsalRoot", restore, "-RequiredTables", "fixture", *common)
    assert restored["ok"] and restored["manifest_verified"] and restored["quick_check"] == "ok"
    assert not restored["rehearsal_copy_kept"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert Path(backup["backup_file"]).is_file() and not list(restore.iterdir())

    # Exercise selection and argument forwarding without a real scheduled task.
    old.write_bytes(database.read_bytes())
    os.utime(old, (1577836800, 1577836800))
    newer_unmanaged = datetime.now(timezone.utc).timestamp() + 60
    os.utime(unmanaged, (newer_unmanaged, newer_unmanaged))
    probe = tmp_path / "restore-probe.ps1"
    probe.write_text(
        "param([string]$BackupFile, [string]$BackupRoot, [string]$RehearsalRoot)\n"
        "@{file=$BackupFile; backup_root=$BackupRoot; rehearsal_root=$RehearsalRoot} | ConvertTo-Json\n",
        encoding="utf-8",
    )
    scheduled = run_ps("Run-MonthlyRestoreRehearsal.ps1", "-BackupRoot", backups,
        "-RehearsalRoot", restore, "-RestoreScript", probe)
    assert Path(scheduled["file"]) == Path(backup["backup_file"])
    assert Path(scheduled["backup_root"]) == backups
    assert Path(scheduled["rehearsal_root"]) == restore
