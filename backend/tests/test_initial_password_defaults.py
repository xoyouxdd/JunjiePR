from __future__ import annotations

import os
from pathlib import Path
import tempfile


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-initial-password-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "isolated-test-only"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "isolated-admin-only"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_crypto import default_initial_password, verify_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import UserAccount  # noqa: E402


def test_employee_number_suffix_is_the_default_initial_password() -> None:
    assert default_initial_password("1000001") == "0001"
    assert default_initial_password("1840760") == "0760"


def test_default_initial_password_requires_a_seven_digit_employee_number() -> None:
    for value in ("", "123456", "12345678", "ABC1234"):
        try:
            default_initial_password(value)
        except ValueError:
            continue
        raise AssertionError(f"Expected ValueError for {value!r}")


def test_new_employee_uses_employee_number_suffix_when_password_is_omitted() -> None:
    with TestClient(app) as client:
        signed_in = client.post(
            "/api/login",
            json={"employee_no": "HR01", "password": "isolated-admin-only"},
        )
        assert signed_in.status_code == 200
        created = client.post(
            "/api/hr/employees",
            json={
                "employee_no": "1234567",
                "name": "初始密码测试员工",
                "role_code": "CM",
                "attraction_id": "",
            },
        )
        assert created.status_code == 200
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(login_account="1234567").one()
        assert verify_password(default_initial_password("1234567"), account.password_hash)
        assert account.must_change_password is True
