"""Backups must target the application layout actually used by deployment."""
import ast
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


def run_ps(name, *arguments):
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(SCRIPTS / name), *map(str, arguments)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        env={**os.environ, "PSModulePath": str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules")})
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.lstrip("\ufeff"))


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"), reason="Windows operations scripts")
def test_real_flat_layout_backup_health_and_restore_preserve_source(tmp_path):
    live, backups, restore = tmp_path / "live", tmp_path / "backups", tmp_path / "restore"
    (live / "app").mkdir(parents=True)
    (live / "data_v2").mkdir()
    (live / ".venv").mkdir()
    backups.mkdir()
    database = live / "data_v2/recognition_v2.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY, content TEXT)")
        db.execute("INSERT INTO fixture VALUES (1, 'isolated backup evidence')")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    common = ["-Python", sys.executable, "-TestMode", "-TestRoot", tmp_path, "-BackupRoot", backups]
    backup = run_ps("Invoke-SqliteOnlineBackup.ps1", "-LiveRoot", live, *common)
    assert backup["ok"] and backup["quick_check"] == "ok" and backup["deleted"] == []
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
