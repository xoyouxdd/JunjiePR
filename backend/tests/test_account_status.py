from __future__ import annotations

import os
from pathlib import Path
import tempfile


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-account-status-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "isolated-test-only"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "isolated-admin-only"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Employee, UserAccount  # noqa: E402


ROOT = Path(__file__).parents[1]


def login(client: TestClient, employee_no: str, password: str) -> None:
    response = client.post("/api/login", json={"employee_no": employee_no, "password": password})
    assert response.status_code == 200, response.text


def test_highest_admin_sees_all_regular_accounts_but_never_hr_accounts() -> None:
    with TestClient(app) as client:
        login(client, "HR01", "isolated-admin-only")
        response = client.get("/api/hr/account-status")
        assert response.status_code == 200, response.text
        payload = response.json()
        by_number = {row["employee_no"]: row for row in payload["items"]}
        assert payload["scope_label"] == "全部常规账号"
        assert "CMTEST01" in by_number
        assert "GSMTEST01" in by_number
        assert "HR-HEAT" not in by_number
        assert "password_hash" not in by_number["CMTEST01"]
        assert by_number["CMTEST01"]["login_status"] == "从未登录"
        assert by_number["CMTEST01"]["password_status"] == "历史状态未记录"


def test_circle_hr_reads_all_circles_frontline_and_lead_only() -> None:
    with TestClient(app) as client:
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no="HR-HEAT").one()
            account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
            account.password_hash = hash_password("isolated-circle-hr")
            account.enabled = True
            account.must_change_password = False
            account.credential_initialized = True
            db.commit()
        login(client, "HR-HEAT", "isolated-circle-hr")
        response = client.get("/api/hr/account-status")
        assert response.status_code == 200, response.text
        payload = response.json()
        by_number = {row["employee_no"]: row for row in payload["items"]}
        assert payload["scope_label"] == "全部景点圈的 CM/TR、TALEAD、Lead"
        assert "CMTEST01" in by_number
        assert "SUPTEST01" in by_number
        assert "GSMTEST01" not in by_number
        assert "HR01" not in by_number


def test_password_change_and_reset_are_reflected_without_disclosing_password() -> None:
    with TestClient(app) as client:
        login(client, "CMTEST01", "isolated-test-only")
        changed = client.post(
            "/api/password",
            json={"current_password": "isolated-test-only", "new_password": "Isolated#2026", "confirm_password": "Isolated#2026"},
        )
        assert changed.status_code == 200, changed.text
        client.post("/api/logout")
        login(client, "HR01", "isolated-admin-only")
        status = client.get("/api/hr/account-status")
        assert status.status_code == 200, status.text
        cm = next(row for row in status.json()["items"] if row["employee_no"] == "CMTEST01")
        assert cm["password_status"] == "已修改密码"
        assert cm["password_changed_at"]
        assert cm["login_status"] == "已登录"
        reset = client.post("/api/accounts/reset-password", json={"employee_no": "CMTEST01", "name": "测试CM甲"})
        assert reset.status_code == 200, reset.text
        refreshed = client.get("/api/hr/account-status")
        cm_after_reset = next(row for row in refreshed.json()["items"] if row["employee_no"] == "CMTEST01")
        assert cm_after_reset["password_status"] == "待本人修改初始/重置密码"
        assert cm_after_reset["password_changed_at"] == ""


def test_account_status_panel_is_lazy_collapsed_at_the_bottom_with_circle_filter() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")

    assert "api('/api/hr/account-status')" in script
    assert "账号状态与登录情况" in script
    assert "不会显示真实密码" in script
    assert "<details id=\"accountStatusPanel\"" in script
    assert "按需展开查看，不影响员工管理操作。" in script
    assert "accountStatusAttraction" in script
    assert "全部景点圈" in script
    assert "${accountStatusPanel()}</div>`" in script
    assert "accountStatusFilter" in script
    assert ".account-status-panel" in css
