from __future__ import annotations

import os
from pathlib import Path
import tempfile


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-employee-number-change-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "isolated-test-only"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "isolated-admin-only"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_crypto import verify_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Employee, EmployeeNumberHistory, UserAccount, UserSession  # noqa: E402


def login_admin(client: TestClient) -> None:
    response = client.post("/api/login", json={"employee_no": "HR01", "password": "isolated-admin-only"})
    assert response.status_code == 200, response.text


def create_employee(client: TestClient, number: str = "1234567") -> int:
    response = client.post(
        "/api/hr/employees",
        json={"employee_no": number, "name": "员工号迁移测试", "role_code": "CM", "attraction_id": ""},
    )
    assert response.status_code == 200, response.text
    return int(response.json()["employee"]["id"])


def test_number_change_keeps_identity_history_and_password_when_not_reset() -> None:
    with TestClient(app) as client:
        login_admin(client)
        employee_id = create_employee(client)
        with SessionLocal() as db:
            account = db.query(UserAccount).filter_by(employee_id=employee_id).one()
            original_hash = account.password_hash
            db.add(UserSession(account_id=account.id, token_hash="a" * 64, expires_at=__import__("datetime").datetime.now()))
            db.commit()
        response = client.post(
            f"/api/hr/employees/{employee_id}/employee-number",
            json={"new_employee_no": "7654321", "reason": "隔离测试：实习转正"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["sessions_revoked"] == 1
        search = client.get("/api/hr/employee-number-targets", params={"keyword": "1234567"})
        assert search.status_code == 200
        assert search.json()["items"][0]["id"] == employee_id
        assert search.json()["items"][0]["matched_historical_no"] == "1234567"
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id)
        account = db.query(UserAccount).filter_by(employee_id=employee_id).one()
        history = db.query(EmployeeNumberHistory).filter_by(employee_id=employee_id).one()
        assert employee.employee_no == "7654321"
        assert account.login_account == "7654321"
        assert account.password_hash == original_hash
        assert verify_password("4567", account.password_hash)
        assert history.old_employee_no == "1234567"
        assert history.new_employee_no == "7654321"
        assert db.query(UserSession).filter_by(account_id=account.id).count() == 0


def test_number_change_can_reset_to_new_suffix_and_rejects_collisions() -> None:
    with TestClient(app) as client:
        login_admin(client)
        employee_id = create_employee(client, "2345678")
        duplicate_id = create_employee(client, "3456789")
        duplicate = client.post(
            f"/api/hr/employees/{employee_id}/employee-number",
            json={"new_employee_no": "3456789", "reason": "隔离测试：重复号"},
        )
        assert duplicate.status_code == 400
        changed = client.post(
            f"/api/hr/employees/{employee_id}/employee-number",
            json={"new_employee_no": "8765432", "reason": "隔离测试：正式工号", "reset_password": True},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["password_reset"] is True
        assert duplicate_id != employee_id
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(employee_id=employee_id).one()
        assert verify_password("5432", account.password_hash)
        assert account.must_change_password is True
