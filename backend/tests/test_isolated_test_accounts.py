"""An explicit fresh fixture covers all real roles without touching existing data."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/seed_isolated_test_accounts.py"
ROLES = {"CM", "TR", "SUPERVISOR", "TA_SUPERVISOR", "GSM", "TA_GSM", "AM", "OM",
         "HR_ADMIN", "HR_CIRCLE", "SYSTEM_ADMIN"}


def run_seed(env):
    # The parent decodes UTF-8; force the child to emit UTF-8 on Windows too,
    # including rejection paths whose Chinese diagnostics use stderr.
    return subprocess.run([sys.executable, str(SCRIPT)], env={**env, "PYTHONUTF8": "1"}, capture_output=True,
                          text=True, encoding="utf-8", timeout=45)


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    data = tmp_path_factory.mktemp("all-role-fixture") / "runtime"
    password, admin_password = secrets.token_urlsafe(12), secrets.token_urlsafe(12)
    env = {**os.environ, "PYTHONUTF8": "1", "RECOGNITION_ENVIRONMENT": "test",
           "RECOGNITION_V2_DATA_DIR": str(data), "RECOGNITION_ENABLE_TEST_ACCOUNTS": "1",
           "RECOGNITION_TEST_DEFAULT_PASSWORD": password, "RECOGNITION_TEST_ADMIN_PASSWORD": admin_password,
           "RECOGNITION_BACKUP_HEALTH_STATUS_FILE": str(data / "backups/backup-health-status.json")}
    result = run_seed(env)
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout and admin_password not in result.stdout
    manifest = json.loads(result.stdout)
    assert {a["role"] for a in manifest["accounts"]} == ROLES
    # Bind the app to the newly created fixture before any database imports.
    os.environ.update({k:v for k,v in env.items() if k.startswith("RECOGNITION_")})
    return data, env, manifest


def test_repeated_initialization_preserves_database_and_passwords(fixture):
    data, env, manifest = fixture
    database = data / "recognition_v2.db"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = run_seed({**env, "RECOGNITION_TEST_DEFAULT_PASSWORD": secrets.token_urlsafe(12)})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["reused"] is True
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("role", sorted(ROLES))
def test_all_eleven_role_logins_and_sensitive_permission_boundary(fixture, role):
    from fastapi.testclient import TestClient
    from app.main import app
    _, env, manifest = fixture
    account = next(a for a in manifest["accounts"] if a["role"] == role)
    password = env["RECOGNITION_TEST_ADMIN_PASSWORD" if role in {"SYSTEM_ADMIN", "HR_ADMIN"} else "RECOGNITION_TEST_DEFAULT_PASSWORD"]
    with TestClient(app) as client:
        assert client.post("/api/login", json={"employee_no": account["login_account"], "password": password}).status_code == 200
        me = client.get("/api/me").json()
        assert me["role_code"] == role and me["base_role_code"] == account["base_role"]
        assert client.get("/api/options").status_code == 200
        assert client.get("/api/action-center").status_code == 200
        assert client.get("/api/admin/backup-health/manual-status").status_code == (200 if role == "SYSTEM_ADMIN" else 403)
        if role in {"TA_SUPERVISOR", "TA_GSM"}:
            assert me["base_role_code"] == ("CM" if role == "TA_SUPERVISOR" else "SUPERVISOR")
            assert role in me["duty_role_codes"]
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/me").status_code == 401


def test_future_expired_acting_and_cross_circle_hr_boundary(fixture):
    from fastapi.testclient import TestClient
    from app.main import app
    _, env, manifest = fixture
    with TestClient(app) as client:
        for number, expected in (("TAFUTURETEST01", "CM"), ("TAEXPIREDTEST01", "TR")):
            assert client.post("/api/login", json={"employee_no": number, "password": env["RECOGNITION_TEST_DEFAULT_PASSWORD"]}).status_code == 200
            me = client.get("/api/me").json()
            assert me["role_code"] == expected and me["duty_role_codes"] == []
            client.post("/api/logout")
        assert client.post("/api/login", json={"employee_no": "CIRCLEHRTEST01", "password": env["RECOGNITION_TEST_DEFAULT_PASSWORD"]}).status_code == 200
        own = client.get("/api/me").json()["attraction_id"]
        options = client.get("/api/options").json()
        assert {c["id"] for c in options["employee_circles"]} == {own}
        other = next(a["attraction_id"] for a in manifest["accounts"] if a["login_account"] == "CMTESTCROSS01")
        assert client.get(f"/api/month-closes/2026-08?attraction_id={other}").status_code == 403
        client.post("/api/logout")


@pytest.mark.parametrize("failure", ["production", "missing_switch", "existing_data"])
def test_initializer_fails_without_mutating_unapproved_directory(tmp_path, failure):
    data = tmp_path / "data"
    env = {**os.environ, "RECOGNITION_ENVIRONMENT": "test", "RECOGNITION_V2_DATA_DIR": str(data),
           "RECOGNITION_ENABLE_TEST_ACCOUNTS": "1", "RECOGNITION_TEST_DEFAULT_PASSWORD": secrets.token_urlsafe(12),
           "RECOGNITION_TEST_ADMIN_PASSWORD": secrets.token_urlsafe(12),
           "RECOGNITION_BACKUP_HEALTH_STATUS_FILE": str(data / "backups/backup-health-status.json")}
    if failure == "production":env["RECOGNITION_ENVIRONMENT"] = "production"
    elif failure == "missing_switch":env["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "0"
    else:
        data.mkdir()
        (data / "do-not-touch.txt").write_bytes(b"original data")
    result = run_seed(env)
    assert result.returncode != 0
    assert not (data / "recognition_v2.db").exists()
    if failure == "existing_data":assert (data / "do-not-touch.txt").read_bytes() == b"original data"
    else:assert not data.exists()
