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
from app.rotation.models import RotationAccount, RotationRosterEntry, RotationRosterUpload  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Attraction, AuditLog, Employee, EmployeeRoleAssignment, Role, UserAccount  # noqa: E402


SCREEN = ("6666666", "1243")
MEMBER_NO = "1234567"


def login(client: TestClient, account: str, password: str = "1234"):
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def relogin(client: TestClient, account: str) -> None:
    client.cookies.clear()
    login(client, account)


def enter(client: TestClient, account: str):
    return client.post("/api/rotation/enter", json={"account": account})


def enable_login(employee_no: str) -> None:
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no=employee_no).one()
        account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        db.commit()


def add_employee(circle_name: str | None, employee_no: str, role_code: str) -> None:
    with SessionLocal() as db:
        attraction = db.query(Attraction).filter_by(name=circle_name).one() if circle_name else None
        role = db.query(Role).filter_by(code=role_code).one()
        employee = Employee(employee_no=employee_no, name=f"测试{role_code}", attraction_id=attraction.id if attraction else None, is_active=True, hired_on=date.today().isoformat())
        db.add(employee)
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=role.id, starts_on=date.today().isoformat(), assignment_type="permanent", status="active", reason="轮岗测试"))
        db.add(UserAccount(employee_id=employee.id, login_account=employee_no, password_hash=hash_password("1234"), enabled=True, must_change_password=False))
        db.commit()


def add_roster_member(employee_no: str, name: str) -> None:
    with SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪").one()
        uploader = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
        today = date.today().isoformat()
        upload = RotationRosterUpload(attraction_id=heat.id, scope="day", start_date=today, end_date=today, file_name="模拟名单.xlsx", entry_count=1, uploaded_by_id=uploader.id)
        db.add(upload)
        db.flush()
        db.add(RotationRosterEntry(upload_id=upload.id, attraction_id=heat.id, work_date=today, employee_no=employee_no, name=name, cell_raw="07:15-16:15"))
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


def test_entry_is_only_for_heat_circle_tr_and_gsm() -> None:
    with TestClient(app) as client:
        add_employee("矮人迷宫", "TRDWARF01", "TR")
        add_employee("矮人迷宫", "GSMDWARF01", "GSM")
        for account in ("TRTEST01", "GSMTEST01"):
            relogin(client, account)
            assert client.get("/api/rotation/access").json()["can_enter"] is True, account
        for account in ("CMTEST01", "TATEST01", "SUPTEST01", "TAGSMTEST01", "AMTEST01", "OMTEST01", "TRDWARF01", "GSMDWARF01"):
            relogin(client, account)
            assert client.get("/api/rotation/access").json()["can_enter"] is False, account
            assert enter(client, "7777777").status_code == 403, account
        for account in ("HR01", "HR-HEAT"):
            enable_login(account)
            relogin(client, account)
            assert client.get("/api/rotation/access").json()["can_enter"] is False, account
        client.cookies.clear()
        assert enter(client, "7777777").status_code == 401


def test_entry_switches_between_simulated_accounts_without_password() -> None:
    with TestClient(app) as client:
        add_roster_member(MEMBER_NO, "模拟员工甲")
        login(client, "TRTEST01")
        assert enter(client, "").status_code == 400
        assert enter(client, "0000000").status_code == 404

        body = enter(client, "7777777").json()
        assert body["kind"] == "supervisor" and body["redirect"] == "/rotation"
        assert client.get("/api/rotation/whoami").json()["kind"] == "supervisor"
        assert client.get("/api/rotation/accounts").status_code == 200

        body = enter(client, "6666666").json()
        assert body["redirect"] == "/rotation/screen"
        assert client.get("/api/rotation/screen/me").status_code == 200
        assert client.get("/api/rotation/accounts").status_code == 403

        body = enter(client, MEMBER_NO).json()
        assert body == {"ok": True, "kind": "member", "name": "模拟员工甲", "redirect": "/rotation"}
        whoami = client.get("/api/rotation/whoami").json()
        assert whoami["employee_no"] == MEMBER_NO and whoami["name"] == "模拟员工甲" and whoami["test_mode"] is True
        assert client.get("/api/rotation/accounts").status_code == 403
        assert client.get("/api/rotation/screen/me").status_code == 403
        # 模拟会话不影响 PR 员工登录。
        assert client.get("/api/me").status_code == 200

    with SessionLocal() as db:
        entries = db.query(AuditLog).filter_by(action="进入轮岗测试").count()
        assert entries >= 3


def test_screen_device_logs_in_directly_and_only_reaches_rotation() -> None:
    with TestClient(app) as client:
        body = login(client, *SCREEN)
        assert body["redirect"] == "/rotation/screen"
        assert client.cookies.get(ACCOUNT_COOKIE)
        assert client.cookies.get("rc_v2_session") is None
        assert client.get("/api/rotation/screen/me").status_code == 200
        for path in ("/api/me", "/api/action-center", "/api/options", "/api/rotation/access"):
            assert client.get(path).status_code == 401, path
        assert client.get("/api/rotation/accounts").status_code == 403
        assert client.post("/api/rotation/logout").status_code == 200
        assert client.get("/api/rotation/screen/me").status_code == 401


def test_direct_login_locks_after_repeated_wrong_passwords() -> None:
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/login", json={"employee_no": SCREEN[0], "password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"employee_no": SCREEN[0], "password": SCREEN[1]}).status_code == 423
    with SessionLocal() as db:
        account = db.query(RotationAccount).filter_by(login_account=SCREEN[0]).one()
        account.locked_until = None
        account.failed_attempts = 0
        db.commit()


def test_rotation_supervisor_resets_screen_password_and_old_sessions_end() -> None:
    with TestClient(app) as screen, TestClient(app) as manager:
        login(screen, *SCREEN)
        login(manager, "GSMTEST01")
        enter(manager, "8888888")
        assert manager.post(f"/api/rotation/accounts/{SCREEN[0]}/password", json={"new_password": "12"}).status_code == 400
        assert manager.post("/api/rotation/accounts/0000000/password", json={"new_password": "5678"}).status_code == 404
        assert manager.post(f"/api/rotation/accounts/{SCREEN[0]}/password", json={"new_password": "5678"}).status_code == 200
        items = {row["login_account"]: row for row in manager.get("/api/rotation/accounts").json()["items"]}
        assert items[SCREEN[0]]["password_changed_at"]

        assert screen.get("/api/rotation/screen/me").status_code == 401
        screen.cookies.clear()
        assert screen.post("/api/login", json={"employee_no": SCREEN[0], "password": SCREEN[1]}).status_code == 401
        login(screen, SCREEN[0], "5678")

    with SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪").one()
        gsm = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
        audit = db.query(AuditLog).filter_by(action="重置休息室大屏密码").one()
        assert audit.attraction_id == heat.id
        assert audit.operator_id == gsm.id
