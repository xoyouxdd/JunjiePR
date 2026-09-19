"""Reset passwords must still satisfy the system's own password policy.

Resets follow the documented "登录账号后四位" rule, but登录账号 has no format
constraint (String(50)), and scripts/seed_level_accounts.py creates single
character login accounts. Without a guard those accounts would be reset to a
one character password — below password_policy_error()'s four character floor —
which the owner could never change back through /api/password.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
import tempfile

import pytest


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-password-reset-guard-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "guard-default-only"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "guard-admin-only"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.security import password_policy_error  # noqa: E402
from app.v2_crypto import account_reset_password, hash_password, verify_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Employee, EmployeeRoleAssignment, Role, UserAccount  # noqa: E402


ADMIN_ACCOUNT = "HR01"
ADMIN_PASSWORD = "guard-admin-only"


def sign_in_admin(client: TestClient) -> None:
    signed_in = client.post(
        "/api/login",
        json={"employee_no": ADMIN_ACCOUNT, "password": ADMIN_PASSWORD},
    )
    assert signed_in.status_code == 200, signed_in.text


def seed_short_login_account(login_account: str, name: str, role_code: str) -> int:
    """Mirror scripts/seed_level_accounts.py: employee_no doubles as登录账号."""
    today = date.today().isoformat()
    with SessionLocal() as db:
        role = db.query(Role).filter(Role.code == role_code).one()
        employee = Employee(employee_no=login_account, name=name, is_active=True, hired_on=today)
        db.add(employee)
        db.flush()
        db.add(
            EmployeeRoleAssignment(
                employee_id=employee.id,
                role_id=role.id,
                starts_on=today,
                assignment_type="permanent",
                status="active",
                reason="短登录账号守卫用例",
            )
        )
        db.add(
            UserAccount(
                employee_id=employee.id,
                login_account=login_account,
                password_hash=hash_password("9999"),
                enabled=True,
                must_change_password=False,
                credential_initialized=True,
            )
        )
        db.commit()
        return employee.id


def password_hash_of(login_account: str) -> str:
    with SessionLocal() as db:
        return db.query(UserAccount).filter_by(login_account=login_account).one().password_hash


def test_account_reset_password_returns_the_login_account_suffix() -> None:
    assert account_reset_password("1840760") == "0760"
    assert account_reset_password("CMTEST02") == "ST02"
    assert account_reset_password("HR01") == "HR01"
    assert password_policy_error(account_reset_password("1840760"), "CM") is None


def test_account_reset_password_rejects_login_accounts_below_the_policy_floor() -> None:
    for value in ("", " ", "1", "HR", "abc"):
        with pytest.raises(ValueError):
            account_reset_password(value)


def test_seven_digit_employee_reset_yields_a_policy_compliant_suffix() -> None:
    employee_no = "1840760"
    with TestClient(app) as client:
        sign_in_admin(client)
        created = client.post(
            "/api/hr/employees",
            json={
                "employee_no": employee_no,
                "name": "后四位重置正常员工",
                "role_code": "CM",
                "attraction_id": "",
            },
        )
        assert created.status_code == 200, created.text
        reset = client.post(
            "/api/accounts/reset-password",
            json={"employee_no": employee_no, "name": "后四位重置正常员工"},
        )
        assert reset.status_code == 200, reset.text
        body = reset.json()
        assert body["temporary_password"] == "0760"
        assert body["password_rule"] == "登录账号后四位"
        assert body["must_change_password"] is True
        assert password_policy_error(body["temporary_password"], "CM") is None
    assert verify_password("0760", password_hash_of(employee_no))


def test_employee_reset_rejects_login_accounts_shorter_than_four_characters() -> None:
    login_account = "1"
    seed_short_login_account(login_account, "演示短账号CM", "CM")
    before = password_hash_of(login_account)
    with TestClient(app) as client:
        sign_in_admin(client)
        reset = client.post(
            "/api/accounts/reset-password",
            json={"employee_no": login_account, "name": "演示短账号CM"},
        )
        assert reset.status_code == 400, reset.text
        assert "不足四位" in reset.json()["detail"]
    assert password_hash_of(login_account) == before
    assert verify_password("9999", before)


def test_circle_hr_reset_rejects_short_login_accounts_the_same_way() -> None:
    login_account = "6"
    employee_id = seed_short_login_account(login_account, "演示短账号圈HR", "HR_CIRCLE")
    before = password_hash_of(login_account)
    with TestClient(app) as client:
        sign_in_admin(client)
        reset = client.post(f"/api/admin/circle-hr-accounts/{employee_id}/reset-password")
        assert reset.status_code == 400, reset.text
        assert "不足四位" in reset.json()["detail"]
    assert password_hash_of(login_account) == before
