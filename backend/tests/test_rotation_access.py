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
from app.rotation.access import ACCOUNT_COOKIE  # noqa: E402
from app.rotation.models import RotationAccount  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Attraction, AuditLog, Employee, EmployeeRoleAssignment, Role, UserAccount  # noqa: E402


SCREEN = ("6666666", "1243")
ROTATION_SUPERVISOR = ("7777777", "7777")
ROTATION_MANAGER = ("8888888", "8888")


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


def enable_login(employee_no: str) -> None:
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no=employee_no).one()
        account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        db.commit()


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


def test_rotation_accounts_are_seeded_outside_the_employee_table() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            heat = db.query(Attraction).filter_by(name="热力追踪").one()
            kinds = {row.login_account: row.kind for row in db.query(RotationAccount).all()}
            assert kinds == {"6666666": "screen", "7777777": "supervisor", "8888888": "manager"}
            for login_account in kinds:
                assert db.query(RotationAccount).filter_by(login_account=login_account).one().attraction_id == heat.id
                assert db.query(Employee).filter_by(employee_no=login_account).count() == 0
                assert db.query(UserAccount).filter_by(login_account=login_account).count() == 0


def test_screen_account_only_reaches_screen_endpoints() -> None:
    with TestClient(app) as client:
        body = login(client, *SCREEN)
        assert body["redirect"] == "/rotation/screen"
        assert client.cookies.get(ACCOUNT_COOKIE)
        assert client.cookies.get("rc_v2_session") is None

        me = client.get("/api/rotation/screen/me")
        assert me.status_code == 200
        assert me.json()["attraction_name"] == "热力追踪"

        for path in ("/api/me", "/api/action-center", "/api/rotation/access", "/api/rotation/whoami", "/api/rotation/accounts"):
            assert client.get(path).status_code == 401, path

        assert client.post("/api/rotation/logout").status_code == 200
        assert client.get("/api/rotation/screen/me").status_code == 401


def test_rotation_supervisor_and_manager_accounts_only_reach_rotation() -> None:
    with TestClient(app) as client:
        for (account, password), kind in ((ROTATION_SUPERVISOR, "supervisor"), (ROTATION_MANAGER, "manager")):
            client.cookies.clear()
            assert login(client, account, password)["redirect"] == "/rotation"
            assert client.cookies.get("rc_v2_session") is None
            whoami = client.get("/api/rotation/whoami")
            assert whoami.status_code == 200
            assert whoami.json()["kind"] == kind
            assert client.get("/api/rotation/accounts").status_code == 200
            # 不是员工账号：PR 系统其他功能全部拒绝，也不能当大屏。
            for path in ("/api/me", "/api/action-center", "/api/options", "/api/rotation/access", "/api/rotation/screen/me"):
                assert client.get(path).status_code == 401, (account, path)


def test_employee_session_cannot_use_screen_endpoints() -> None:
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        assert client.get("/api/rotation/screen/me").status_code == 401


def test_rotation_account_login_locks_after_repeated_wrong_passwords() -> None:
    with TestClient(app) as client:
        for _ in range(5):
            response = client.post("/api/login", json={"employee_no": SCREEN[0], "password": "wrong"})
            assert response.status_code == 401
        response = client.post("/api/login", json={"employee_no": SCREEN[0], "password": SCREEN[1]})
        assert response.status_code == 423
    with SessionLocal() as db:
        account = db.query(RotationAccount).filter_by(login_account=SCREEN[0]).one()
        account.locked_until = None
        account.failed_attempts = 0
        db.commit()


def test_rotation_roles_for_employee_accounts() -> None:
    with TestClient(app) as client:
        add_supervisor_in("矮人迷宫", "SUPDWARF01")
        for account in ("CMTEST01", "TRTEST01"):
            assert access(client, account) == {"attraction_name": "热力追踪", "can_manage": False, "can_view_self": True}, account
            assert client.get("/api/rotation/whoami").status_code == 403
        # 代理TA主管：本职 CM 仍可看本人轮岗，同时可以管理。
        assert access(client, "TATEST01") == {"attraction_name": "热力追踪", "can_manage": True, "can_view_self": True}
        # 主管及以上不限景点圈。
        for account in ("SUPTEST01", "TAGSMTEST01", "GSMTEST01", "SUPDWARF01"):
            assert access(client, account)["can_manage"] is True, account
            assert client.get("/api/rotation/whoami").json()["kind"] == "employee"
        # AM、OM 和 HR 不使用轮岗。
        for account in ("AMTEST01", "OMTEST01"):
            result = access(client, account)
            assert result["can_manage"] is False and result["can_view_self"] is False, account
        for account in ("HR01", "HR-HEAT"):
            enable_login(account)
            result = access(client, account)
            assert result["can_manage"] is False and result["can_view_self"] is False, account
        assert client.get("/api/rotation/accounts").status_code == 403


def test_manager_resets_account_password_and_old_sessions_end() -> None:
    with TestClient(app) as screen, TestClient(app) as manager:
        login(screen, *SCREEN)
        assert screen.get("/api/rotation/screen/me").status_code == 200

        login(manager, "CMTEST01")
        assert manager.post(f"/api/rotation/accounts/{SCREEN[0]}/password", json={"new_password": "5678"}).status_code == 403
        manager.cookies.clear()
        login(manager, *ROTATION_SUPERVISOR)
        assert manager.post(f"/api/rotation/accounts/{SCREEN[0]}/password", json={"new_password": "12"}).status_code == 400
        assert manager.post("/api/rotation/accounts/0000000/password", json={"new_password": "5678"}).status_code == 404
        response = manager.post(f"/api/rotation/accounts/{SCREEN[0]}/password", json={"new_password": "5678"})
        assert response.status_code == 200, response.text
        items = {row["login_account"]: row for row in manager.get("/api/rotation/accounts").json()["items"]}
        assert items[SCREEN[0]]["password_changed_at"]

        assert screen.get("/api/rotation/screen/me").status_code == 401
        screen.cookies.clear()
        assert screen.post("/api/login", json={"employee_no": SCREEN[0], "password": SCREEN[1]}).status_code == 401
        login(screen, SCREEN[0], "5678")

    with SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪").one()
        audit = db.query(AuditLog).filter_by(action="重置休息室大屏密码").one()
        assert audit.attraction_id == heat.id
        assert audit.operator_id is None
