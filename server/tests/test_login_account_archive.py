from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-login-archive-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "isolated-test-only"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "isolated-admin-only"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import AuditLog, Employee, RecognitionRecord, RecognitionType, UserAccount  # noqa: E402


ROOT = Path(__file__).parents[1]


def login(client: TestClient, employee_no: str, password: str) -> None:
    response = client.post("/api/login", json={"employee_no": employee_no, "password": password})
    assert response.status_code == 200, response.text


def mark_eligible_with_business_history() -> tuple[int, int]:
    """Create a retained recognition row and an account eligible for removal."""
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
        account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
        admin = db.query(Employee).filter_by(employee_no="HR01").one()
        recognition_type = db.query(RecognitionType).first()
        assert recognition_type is not None
        employee.is_active = False
        employee.terminated_on = (date.today() - timedelta(days=8)).isoformat()
        employee.updated_at = datetime.now() - timedelta(days=8)
        db.add(
            RecognitionRecord(
                employee_id=employee.id,
                employee_no=employee.employee_no,
                employee_name=employee.name,
                employee_role_snapshot="CM",
                employee_role_code_snapshot="CM",
                home_attraction_id=employee.attraction_id,
                home_attraction_name=employee.attraction.name,
                occurred_attraction_id=employee.attraction_id,
                recognition_date=date.today().isoformat(),
                recognition_month=date.today().strftime("%Y-%m"),
                recognition_type_id=recognition_type.id,
                recognition_type_name=recognition_type.name,
                content="隔离留档测试",
                recognizer_employee_id=admin.id,
                recognizer_name=admin.name,
                recognizer_role_snapshot="最高管理员",
                recognizer_role_code_snapshot="SYSTEM_ADMIN",
                operator_employee_id=admin.id,
                operator_name=admin.name,
                operator_role_snapshot="最高管理员",
                operator_role_code_snapshot="SYSTEM_ADMIN",
                source="admin",
                fraction=Decimal("0.50"),
                credited_fraction=Decimal("0.50"),
                status="confirmed",
            )
        )
        db.commit()
        return employee.id, account.id


def test_remove_login_after_seven_days_keeps_employee_and_business_archive() -> None:
    with TestClient(app) as client:
        employee_id, account_id = mark_eligible_with_business_history()
        login(client, "HR01", "isolated-admin-only")
        directory = client.get("/api/hr/employees")
        assert directory.status_code == 200, directory.text
        item = next(row for row in directory.json() if row["id"] == employee_id)
        assert item["account_deletion_eligible"] is True
        removed = client.request(
            "DELETE",
            f"/api/hr/employees/{employee_id}/account",
            json={"reason": "隔离测试：删除登录账号，保留业务档案"},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["archive_retained"] is True
        with SessionLocal() as db:
            employee = db.get(Employee, employee_id)
            assert employee is not None
            assert employee.account_deleted_at is not None
            assert db.get(UserAccount, account_id) is None
            assert db.query(RecognitionRecord).filter_by(employee_id=employee_id).count() == 1
            assert db.query(AuditLog).filter_by(action="删除停用登录账号", entity_id=str(employee_id)).count() == 1
        rejected_login = client.post("/api/login", json={"employee_no": "CMTEST01", "password": "isolated-test-only"})
        assert rejected_login.status_code == 401
        refreshed = client.get("/api/hr/employees")
        item = next(row for row in refreshed.json() if row["id"] == employee_id)
        assert item["account_deleted_at"]
        assert item["account_deletion_eligible"] is False
        assert "账号已删除" in item["account_deletion_reason"]


def test_delete_endpoint_enforces_the_full_seven_day_wait() -> None:
    with TestClient(app) as client:
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no="CMTEST02").one()
            employee.is_active = False
            employee.terminated_on = (date.today() - timedelta(days=6)).isoformat()
            db.commit()
            employee_id = employee.id
        login(client, "HR01", "isolated-admin-only")
        response = client.request("DELETE", f"/api/hr/employees/{employee_id}/account", json={})
        assert response.status_code == 400
        assert "起可删除" in response.json()["detail"]
        with SessionLocal() as db:
            assert db.query(UserAccount).filter_by(employee_id=employee_id).count() == 1


def test_hr_archive_ui_and_export_marker_are_present() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    router = (ROOT / "app" / "routers" / "v2.py").read_text(encoding="utf-8")
    assert "data-delete-login-account" in script
    assert "删除登录账号" in script
    assert "账号已删除·留档" in script
    assert '"/hr/employees/{employee_id}/account"' in router
    assert "business_history_retained" in router
    assert "e.account_deleted_at AS account_deleted_at" in router
