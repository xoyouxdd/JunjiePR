from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-operations-test-"))
HEALTH_FILE = TEST_DATA_DIR / "backup-health-status.json"
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"
os.environ["RECOGNITION_BACKUP_HEALTH_STATUS_FILE"] = str(HEALTH_FILE)

from app.main import app  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import AppNotification, Employee, UserAccount  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def prepare_health_report() -> None:
    HEALTH_FILE.write_text(
        json.dumps(
            {
                "ok": False,
                "checked_at_utc": "2026-08-19T00:00:00Z",
                "task": {"exists": True, "enabled": True, "state": "Ready", "last_task_result": 0},
                "latest_backup": {"file_name": "recognition_v2-20260819T000000-000.db", "age_hours": 30, "quick_check": "ok", "sha256_verified": True},
                "issues": [{"code": "backup_stale", "message": "The latest backup is older than the allowed age."}],
                "alert": {"configured": True, "attempted": True, "delivered": True, "last_alert_at_utc": "2026-08-19T00:00:00Z"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def enable_circle_hr_test_login() -> None:
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no="HR-HEAT").one()
        account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        db.commit()


def test_action_center_respects_five_level_boundaries_and_operations_is_admin_only() -> None:
    prepare_health_report()
    with TestClient(app) as client:
        with SessionLocal() as db:
            cm = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            db.add(AppNotification(employee_id=cm.id, notification_type="review", title="测试审批", body="已确认", target_path="/"))
            db.commit()

        login(client, "CMTEST01")
        cm_actions = client.get("/api/action-center")
        assert cm_actions.status_code == 200
        assert cm_actions.json()["role"] == "CM"
        assert any(item["type"] == "approval_notification" for item in cm_actions.json()["items"])
        assert client.get("/api/admin/operations-health").status_code == 403

        client.post("/api/logout")
        login(client, "TATEST01")
        assert client.get("/api/action-center").json()["role"] == "TA_SUPERVISOR"

        client.post("/api/logout")
        login(client, "TAGSMTEST01")
        tagsm_actions = client.get("/api/action-center")
        assert tagsm_actions.status_code == 200
        assert tagsm_actions.json()["role"] == "TA_GSM"
        assert all(item["tab"] != "operations" for item in tagsm_actions.json()["items"])

        client.post("/api/logout")
        login(client, "GSMTEST01")
        assert client.get("/api/action-center").json()["role"] == "GSM"

        enable_circle_hr_test_login()
        client.post("/api/logout")
        login(client, "HR-HEAT")
        assert client.get("/api/action-center").json()["role"] == "HR_CIRCLE"
        assert client.get("/api/admin/operations-health").status_code == 403

        client.post("/api/logout")
        login(client, "HR01", "HR123")
        admin_actions = client.get("/api/action-center")
        assert admin_actions.status_code == 200
        assert any(item["type"] == "backup_health" for item in admin_actions.json()["items"])
        operations = client.get("/api/admin/operations-health")
        assert operations.status_code == 200
        payload = operations.json()
        assert payload["backup"]["ok"] is False
        assert payload["backup"]["issues"][0]["code"] == "backup_stale"
        assert "path" not in json.dumps(payload, ensure_ascii=False).lower()


def test_readiness_check_is_read_only_and_reports_aggregate_counts_only() -> None:
    with TestClient(app):
        database = TEST_DATA_DIR / "recognition_v2.db"
        output = TEST_DATA_DIR / "readiness.json"
        script = Path(__file__).parents[1] / "ops" / "verify_readiness.py"
        result = subprocess.run(
            [sys.executable, str(script), "--database", str(database), "--output", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_ok"] is True
    assert report["quick_check"] == ["ok"]
    assert report["counts"]["employees"] >= 1
    assert "CMTEST01" not in output.read_text(encoding="utf-8")


def test_action_center_and_operations_views_are_present_in_the_app_shell() -> None:
    script = (Path(__file__).parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "['actionCenter','待办']" in script
    assert "renderActionCenter" in script
    assert "待办中心" in script
    assert "data-action-pane-tab=\"review\"" in script
    assert "['upgradeReview','待审核']" not in script
    assert "密码管理" in script
    assert "if(has('PASSWORD_RESET'))return renderAccountReset();" in script
    assert "renderOperations" in script
    assert "/api/admin/operations-health" in script
    assert "renderGovernance" in script
    assert "/api/governance/appeals" in script


def test_statistics_detail_response_keeps_employee_identity_for_title() -> None:
    with TestClient(app) as client:
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            employee_id = employee.id
        login(client, "GSMTEST01")
        response = client.get(f"/api/statistics/details?month=2026-08&employee_ids={employee_id}&effective_only=true")
    assert response.status_code == 200, response.text
    detail = response.json()["details"][str(employee_id)]
    assert detail["employee_name"]
    assert detail["employee_no"] == "CMTEST01"
    assert detail["role_name"]
