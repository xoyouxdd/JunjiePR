from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-rotation-access-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.rotation.access import DEFAULT_SCREEN_ACCOUNT, DEFAULT_SCREEN_PASSWORD, SCREEN_COOKIE  # noqa: E402
from app.rotation.models import RotationScreenAccount  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Attraction, AuditLog, Employee, EmployeeRoleAssignment, Role, UserAccount  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234"):
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def access(client: TestClient, account: str) -> dict:
    client.cookies.clear()
    login(client, account)
    response = client.get("/api/rotation/access")
    assert response.status_code == 200, response.text
    return response.json()


def add_supervisor_in(circle_name: str, employee_no: str) -> None:
    with SessionLocal() as db:
        attraction = db.query(Attraction).filter_by(name=circle_name).one()
        role = db.query(Role).filter_by(code="SUPERVISOR").one()
        employee = Employee(employee_no=employee_no, name="其他圈主管", attraction_id=attraction.id, is_active=True, hired_on=date.today().isoformat())
        db.add(employee)
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=role.id, starts_on=date.today().isoformat(), assignment_type="permanent", status="active", reason="轮岗测试"))
        db.add(UserAccount(employee_id=employee.id, login_account=employee_no, password_hash=hash_password("1234"), enabled=True, must_change_password=False))
        db.commit()


def enable_login(employee_no: str) -> None:
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no=employee_no).one()
        account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        db.commit()


def test_screen_account_is_seeded_outside_the_employee_table() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            account = db.query(RotationScreenAccount).filter_by(login_account=DEFAULT_SCREEN_ACCOUNT).one()
            heat = db.query(Attraction).filter_by(name="热力追踪").one()
            assert account.attraction_id == heat.id
            assert db.query(Employee).filter_by(employee_no=DEFAULT_SCREEN_ACCOUNT).count() == 0
            assert db.query(UserAccount).filter_by(login_account=DEFAULT_SCREEN_ACCOUNT).count() == 0


def test_screen_login_only_reaches_screen_endpoints() -> None:
    with TestClient(app) as client:
        body = login(client, DEFAULT_SCREEN_ACCOUNT, DEFAULT_SCREEN_PASSWORD)
        assert body["redirect"] == "/rotation/screen"
        assert client.cookies.get(SCREEN_COOKIE)
        assert client.cookies.get("rc_v2_session") is None

        me = client.get("/api/rotation/screen/me")
        assert me.status_code == 200
        assert me.json()["attraction_name"] == "热力追踪"

        # 大屏账号不能进入员工功能，也不能做轮岗管理。
        for path in ("/api/me", "/api/action-center", "/api/rotation/access", "/api/rotation/screen-account"):
            assert client.get(path).status_code == 401, path

        assert client.post("/api/rotation/screen/logout").status_code == 200
        assert client.get("/api/rotation/screen/me").status_code == 401


def test_employee_session_cannot_use_screen_endpoints() -> None:
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        assert client.get("/api/rotation/screen/me").status_code == 401


def test_screen_login_locks_after_repeated_wrong_passwords() -> None:
    with TestClient(app) as client:
        for _ in range(5):
            response = client.post("/api/login", json={"employee_no": DEFAULT_SCREEN_ACCOUNT, "password": "wrong"})
            assert response.status_code == 401
        response = client.post("/api/login", json={"employee_no": DEFAULT_SCREEN_ACCOUNT, "password": DEFAULT_SCREEN_PASSWORD})
        assert response.status_code == 423
    with SessionLocal() as db:
        account = db.query(RotationScreenAccount).filter_by(login_account=DEFAULT_SCREEN_ACCOUNT).one()
        account.locked_until = None
        account.failed_attempts = 0
        db.commit()


def test_rotation_roles_follow_ta_supervisor_and_above_in_heat_circle() -> None:
    with TestClient(app) as client:
        add_supervisor_in("矮人迷宫", "SUPDWARF01")
        for account in ("CMTEST01", "TRTEST01"):
            result = access(client, account)
            assert result == {"attraction_name": "热力追踪", "can_manage": False, "can_view_self": True}, account
        # 代理TA主管：本职 CM 仍可看本人轮岗，同时可以管理。
        assert access(client, "TATEST01") == {"attraction_name": "热力追踪", "can_manage": True, "can_view_self": True}
        for account in ("SUPTEST01", "TAGSMTEST01", "GSMTEST01", "AMTEST01", "OMTEST01"):
            result = access(client, account)
            assert result["can_manage"] is True, account
        for account in ("HR01", "HR-HEAT"):
            enable_login(account)
            result = access(client, account)
            assert result["can_manage"] is False and result["can_view_self"] is False, account
        assert access(client, "SUPDWARF01")["can_manage"] is False
        assert client.get("/api/rotation/screen-account").status_code == 403


def test_manager_resets_screen_password_and_old_screen_sessions_end() -> None:
    with TestClient(app) as screen, TestClient(app) as manager:
        login(screen, DEFAULT_SCREEN_ACCOUNT, DEFAULT_SCREEN_PASSWORD)
        assert screen.get("/api/rotation/screen/me").status_code == 200

        login(manager, "CMTEST01")
        assert manager.post("/api/rotation/screen-account/password", json={"new_password": "5678"}).status_code == 403
        manager.cookies.clear()
        login(manager, "SUPTEST01")
        assert manager.post("/api/rotation/screen-account/password", json={"new_password": "12"}).status_code == 400
        response = manager.post("/api/rotation/screen-account/password", json={"new_password": "5678"})
        assert response.status_code == 200, response.text
        info = manager.get("/api/rotation/screen-account").json()
        assert info["login_account"] == DEFAULT_SCREEN_ACCOUNT
        assert info["password_changed_at"]

        assert screen.get("/api/rotation/screen/me").status_code == 401
        screen.cookies.clear()
        assert screen.post("/api/login", json={"employee_no": DEFAULT_SCREEN_ACCOUNT, "password": DEFAULT_SCREEN_PASSWORD}).status_code == 401
        login(screen, DEFAULT_SCREEN_ACCOUNT, "5678")

    with SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪").one()
        audit = db.query(AuditLog).filter_by(action="重置大屏密码").one()
        assert audit.attraction_id == heat.id
