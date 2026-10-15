"""Environment guards, independent backup/restore and visible environment identity."""
from pathlib import Path
import hashlib
import json
import sqlite3
import os
import subprocess

import pytest

from app import runtime_environment as runtime


def local_env(tmp_path):
    return {"RECOGNITION_ENVIRONMENT": "test", "RECOGNITION_V2_DATA_DIR": str(tmp_path / "isolated")}


def test_production_defaults_and_explicit_custom_data_remain_supported(tmp_path):
    assert runtime.environment_kind({}) == "production"
    assert runtime.backup_health_path({}) == runtime.PRODUCTION_BACKUP_HEALTH_FILE
    env = {"RECOGNITION_ENVIRONMENT": "production", "RECOGNITION_V2_DATA_DIR": str(tmp_path / "custom")}
    runtime.validate_runtime_paths(environ=env)
    assert runtime.backup_health_path(env) == runtime.PRODUCTION_BACKUP_HEALTH_FILE


def test_alternate_data_defaults_local_backup_without_creating_paths(tmp_path):
    env = {"RECOGNITION_V2_DATA_DIR": str(tmp_path / "isolated")}
    assert runtime.environment_kind(env) == "local"
    assert runtime.backup_health_path(env) == tmp_path / "isolated/backups/backup-health-status.json"
    runtime.validate_runtime_paths(environ=env)
    assert not (tmp_path / "isolated").exists()


@pytest.mark.parametrize("failure", ["default_data", "default_data_child", "server_data", "external_health", "health_is_data", "health_is_db", "invalid_mode", "test_in_production"])
def test_unsafe_configuration_fails_before_any_directory_write(tmp_path, failure):
    env = local_env(tmp_path)
    if failure == "default_data":env["RECOGNITION_V2_DATA_DIR"] = str(runtime.DEFAULT_DATA_DIR)
    elif failure == "default_data_child":env["RECOGNITION_V2_DATA_DIR"] = str(runtime.DEFAULT_DATA_DIR / "test-child")
    elif failure == "server_data":env["RECOGNITION_V2_DATA_DIR"] = str(runtime.PRODUCTION_ROOT / "recognition-card-system/data_v2")
    elif failure == "external_health":env["RECOGNITION_BACKUP_HEALTH_STATUS_FILE"] = str(tmp_path / "outside/health.json")
    elif failure == "health_is_data":env["RECOGNITION_BACKUP_HEALTH_STATUS_FILE"] = env["RECOGNITION_V2_DATA_DIR"]
    elif failure == "health_is_db":env["RECOGNITION_BACKUP_HEALTH_STATUS_FILE"] = str(tmp_path / "isolated/recognition_v2.db")
    elif failure == "invalid_mode":env["RECOGNITION_ENVIRONMENT"] = "unknown"
    else:env.update(RECOGNITION_ENVIRONMENT="production", RECOGNITION_ENABLE_TEST_ACCOUNTS="1")
    with pytest.raises(RuntimeError):runtime.validate_runtime_paths(environ=env)
    assert not (tmp_path / "isolated").exists()
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("name", ["files", "exports", "backups", "recognition_v2.db", "recognition_v2.db-wal", "recognition_v2.db-shm"])
def test_linked_storage_is_rejected(tmp_path, name):
    data, other = tmp_path / "data", tmp_path / "other"
    data.mkdir(); other.mkdir()
    try:(data / name).symlink_to(other, target_is_directory=True)
    except OSError:
        if os.name != "nt":raise
        # Windows junction creation does not require Developer Mode/admin rights.
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "New-Item -ItemType Junction -Path $env:FZ_TEST_LINK_PATH -Target $env:FZ_TEST_TARGET_PATH | Out-Null"],
            env={**os.environ, "FZ_TEST_LINK_PATH": str(data / name), "FZ_TEST_TARGET_PATH": str(other)},
            capture_output=True, timeout=20)
        assert result.returncode == 0, "Could not create isolated junction fixture"
    with pytest.raises(RuntimeError):runtime.validate_runtime_paths(environ={**local_env(tmp_path), "RECOGNITION_V2_DATA_DIR": str(data)})


def test_test_initialization_requires_opt_in_and_independent_data(tmp_path):
    env = local_env(tmp_path)
    with pytest.raises(RuntimeError):runtime.require_test_initialization(env)
    env["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
    assert runtime.require_test_initialization(env) == tmp_path / "isolated"


def test_local_manual_snapshot_restore_preserves_source_and_other_directory(tmp_path, monkeypatch):
    env = local_env(tmp_path)
    for key, value in env.items():monkeypatch.setenv(key, value)
    monkeypatch.delenv("RECOGNITION_BACKUP_HEALTH_STATUS_FILE", raising=False)
    from app import backup_management as backups
    data = runtime.data_directory(env)
    data.mkdir()
    source = data / "recognition_v2.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY, content TEXT)")
        db.execute("INSERT INTO fixture VALUES (1, 'isolated verification')")
    sentinel = tmp_path / "other-environment.txt"
    sentinel.write_bytes(b"must remain untouched")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(backups, "DB_PATH", source)
    monkeypatch.setattr(backups, "BACKUP_HEALTH_STATUS_PATH", runtime.backup_health_path(env))
    name = backups._create_backup()
    snapshot = data / "backups" / name
    manifest = json.loads(snapshot.with_name(name + ".manifest.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == manifest["sha256"]
    restored = data / "restore-rehearsal.db"
    with sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(restored) as dest:
        src.backup(dest)
        assert dest.execute("SELECT content FROM fixture").fetchone()[0] == "isolated verification"
        assert dest.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert sentinel.read_bytes() == b"must remain untouched"
    monkeypatch.setattr(backups, "BACKUP_HEALTH_STATUS_PATH", tmp_path / "external/health.json")
    with pytest.raises(RuntimeError):backups._create_backup()
    assert not (tmp_path / "external").exists()


def test_environment_banner_on_login_and_main_only_for_nonproduction(tmp_path, monkeypatch):
    from app.main import render_static_page, BASE_DIR
    monkeypatch.setenv("RECOGNITION_ENVIRONMENT", "test")
    for name in ("login.html", "index.html", "rotation.html", "rotation-screen.html"):
        response = render_static_page(BASE_DIR / "app/static" / name)
        assert response.body.count(b'class="environment-banner"') == 1
        assert b'data-runtime-environment="test"' in response.body
        assert "隔离测试" in response.body.decode()
        assert "unsafe-inline" not in response.headers.get("content-security-policy", "")
    monkeypatch.setenv("RECOGNITION_ENVIRONMENT", "production")
    assert b'class="environment-banner"' not in render_static_page(BASE_DIR / "app/static/login.html").body
