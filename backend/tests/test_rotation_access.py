"""Device login, PIN management, and retired rotation entry points."""
from datetime import date
import os
from pathlib import Path
import tempfile

import pytest
from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-rotation-access-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.database.connection import SessionLocal  # noqa: E402
from app.rotation.access import ACCOUNT_COOKIE  # noqa: E402
from app.rotation.models import RotationAccount  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_models import Attraction, AuditLog, Employee, EmployeeRoleAssignment, Role, UserAccount  # noqa: E402


SCREEN = ("6666666", "1243")
SCREEN_API = "/api/rotation-screen"


def login(client: TestClient, account: str, password: str = "1234"):
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def add_other_circle_gsm() -> str:
    number = "GSMDWARF01"
    with SessionLocal() as db:
        attraction = db.query(Attraction).filter_by(name="矮人迷宫").one()
        role = db.query(Role).filter_by(code="GSM").one()
        employee = Employee(employee_no=number, name="其他景点经理", attraction_id=attraction.id,
                            is_active=True, hired_on=date.today().isoformat())
        db.add(employee)
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=role.id, starts_on=date.today().isoformat(),
                                      assignment_type="permanent", status="active", reason="轮岗测试"))
        db.add(UserAccount(employee_id=employee.id, login_account=number, password_hash=hash_password("1234"),
                           enabled=True, must_change_password=False))
        db.commit()
    return number


@pytest.fixture
def restore_screen_pin():
    yield
    with SessionLocal() as db:
        account = db.query(RotationAccount).filter_by(login_account=SCREEN[0]).one()
        account.password_hash = hash_password(SCREEN[1])
        account.password_changed_at = None
        account.locked_until = None
        account.failed_attempts = 0
        db.commit()


def test_screen_account_is_seeded_outside_the_employee_table():
    with TestClient(app), SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪").one()
        account = db.query(RotationAccount).filter_by(login_account=SCREEN[0]).one()
        assert account.kind == "screen" and account.attraction_id == heat.id
        assert db.query(Employee).filter_by(employee_no=SCREEN[0]).count() == 0
        assert db.query(UserAccount).filter_by(login_account=SCREEN[0]).count() == 0


def test_device_login_is_scoped_to_formal_screen_and_logout_revokes_session():
    with TestClient(app) as client:
        body = login(client, *SCREEN)
        assert body["redirect"] == "/rotation/screen"
        assert client.cookies.get(ACCOUNT_COOKIE)
        assert client.cookies.get("rc_v2_session") is None
        assert client.get(f"{SCREEN_API}/me").json()["attraction_name"] == "热力追踪"
        for path in ("/api/me", "/api/action-center", "/api/options", "/api/rotation-board/whoami",
                     f"{SCREEN_API}/accounts"):
            assert client.get(path).status_code == 401, path
        assert client.post(f"{SCREEN_API}/logout").status_code == 200
        assert client.get(f"{SCREEN_API}/me").status_code == 401


def test_device_login_locks_after_repeated_wrong_passwords(restore_screen_pin):
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/login", json={"employee_no": SCREEN[0], "password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"employee_no": SCREEN[0], "password": SCREEN[1]}).status_code == 423


def test_only_heat_gsm_can_manage_device_pins():
    with TestClient(app) as client:
        for number in ("TRTEST01", "CMTEST01", "SUPTEST01", "TAGSMTEST01", add_other_circle_gsm()):
            client.cookies.clear()
            login(client, number)
            assert client.get(f"{SCREEN_API}/accounts").status_code == 403, number
            assert client.post(f"{SCREEN_API}/accounts/{SCREEN[0]}/password",
                               json={"new_password": "5678"}).status_code == 403, number
        client.cookies.clear()
        login(client, "GSMTEST01")
        response = client.get(f"{SCREEN_API}/accounts")
        assert response.status_code == 200, response.text
        assert {row["login_account"]: row["kind"] for row in response.json()["items"]} == {SCREEN[0]: "screen"}


def test_heat_gsm_resets_screen_pin_and_old_sessions_end(restore_screen_pin):
    with TestClient(app) as screen, TestClient(app) as manager:
        login(screen, *SCREEN)
        login(manager, "GSMTEST01")
        url = f"{SCREEN_API}/accounts/{SCREEN[0]}/password"
        assert manager.post(url, json={"new_password": "12"}).status_code == 400
        for account in ("0000000", "7777777"):
            assert manager.post(f"{SCREEN_API}/accounts/{account}/password",
                                json={"new_password": "5678"}).status_code == 404
        assert manager.post(url, json={"new_password": "5678"}).status_code == 200
        items = {row["login_account"]: row for row in manager.get(f"{SCREEN_API}/accounts").json()["items"]}
        assert items[SCREEN[0]]["password_changed_at"]

        assert screen.get(f"{SCREEN_API}/me").status_code == 401
        screen.cookies.clear()
        assert screen.post("/api/login", json={"employee_no": SCREEN[0], "password": SCREEN[1]}).status_code == 401
        login(screen, SCREEN[0], "5678")

    with SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪").one()
        gsm = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
        audit = db.query(AuditLog).filter_by(action="重置休息室大屏密码").one()
        assert audit.attraction_id == heat.id
        assert audit.operator_id == gsm.id


def test_employee_entry_and_formal_rotation_pages():
    with TestClient(app) as client:
        login(client, "TRTEST01")
        assert client.get("/api/me").json()["rotation_entry"] is True
        client.cookies.clear()
        login(client, "CMTEST01")
        assert client.get("/api/me").json()["rotation_entry"] is True
        assert client.get("/api/rotation-board/whoami").json()["capabilities"]["can_operate"] is False
        for path, script in (("/rotation-board", "static/js/rotation.js"),
                             ("/rotation/screen", "../static/js/rotation-screen.js")):
            page = client.get(path)
            assert page.status_code == 200 and script in page.text and "__STATIC_CACHE_VERSION__" not in page.text


def test_legacy_rotation_page_and_api_are_retired():
    assert not any(route.path.startswith("/api/rotation/") for route in app.routes)
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        assert client.get("/rotation").status_code == 404
        for path in ("enter", "adjust/login", "adjust/grants", "test-clock"):
            assert client.post(f"/api/rotation/{path}", json={}).status_code == 404
