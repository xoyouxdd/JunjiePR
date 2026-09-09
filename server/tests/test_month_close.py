from __future__ import annotations

import os
from datetime import date
from io import BytesIO
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-month-close-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_models import AuditLog, Employee, EmployeeRoleAssignment, MonthClosure, Role, UserAccount, UserSession  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def restore_test_accounts() -> None:
    with SessionLocal() as db:
        for login_account, password in (
            ("CMTEST01", "1234"),
            ("TATEST01", "1234"),
            ("GSMTEST01", "1234"),
            ("TAGSMTEST01", "1234"),
            ("HR-HEAT", "1234"),
            ("HR01", "HR123"),
        ):
            account = db.query(UserAccount).filter_by(login_account=login_account).one()
            account.password_hash = hash_password(password)
            account.enabled = True
            account.must_change_password = False
            account.failed_attempts = 0
            account.locked_until = None
            db.query(UserSession).filter_by(account_id=account.id).delete(synchronize_session=False)
        tagsm = db.query(Employee).filter_by(employee_no="TAGSMTEST01").one()
        tagsm_role = db.query(Role).filter_by(code="TA_GSM").one()
        assignment = db.query(EmployeeRoleAssignment).filter_by(employee_id=tagsm.id, status="active").one()
        assignment.role_id = tagsm_role.id
        db.commit()


def make_png() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return stream.getvalue()


def make_pdf() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PDF")
    return stream.getvalue()


def test_month_close_is_scoped_to_circle_hr_and_reopen_is_controlled() -> None:
    month = "2099-01"
    with TestClient(app) as client:
        restore_test_accounts()
        login(client, "HR-HEAT")
        circles = client.get("/api/options").json()["employee_circles"]
        heat_id = next(row["id"] for row in circles if row["name"] == "热力追踪")

        assert client.post(f"/api/month-closes/{month}/close", json={"attraction_id": heat_id, "reason": "月度核对完成"}).status_code == 200
        closed = client.get(f"/api/month-closes/{month}", params={"attraction_id": heat_id})
        assert closed.status_code == 200
        assert closed.json()["is_closed"] is True

        client.post("/api/logout")
        login(client, "TAGSMTEST01")
        assert client.post(f"/api/month-closes/{month}/close", json={"attraction_id": heat_id, "reason": "越权"}).status_code == 403
        assert client.post(f"/api/month-closes/{month}/reopen", json={"attraction_id": heat_id, "reason": "越权"}).status_code == 403

        client.post("/api/logout")
        login(client, "GSMTEST01")
        assert client.post(f"/api/month-closes/{month}/reopen", json={"attraction_id": heat_id, "reason": "GSM越权重开"}).status_code == 403

        client.post("/api/logout")
        login(client, "HR01", "HR123")
        dwarf_id = next(row["id"] for row in client.get("/api/options").json()["employee_circles"] if row["name"] == "矮人迷宫")
        assert client.post(f"/api/month-closes/{month}/reopen", json={"attraction_id": heat_id}).status_code == 400
        reopened = client.post(f"/api/month-closes/{month}/reopen", json={"attraction_id": heat_id, "reason": "景点圈HR复查后重开"})
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["is_closed"] is False

        global_month = "2099-03"
        global_close = client.post(f"/api/month-closes/{global_month}/close", json={"attraction_id": None, "reason": "全部景点圈月结"})
        assert global_close.status_code == 200, global_close.text
        assert client.get(f"/api/month-closes/{global_month}", params={"attraction_id": dwarf_id}).json()["effective_scope"] == "全部景点圈"
        assert client.post(f"/api/month-closes/{global_month}/close", json={"attraction_id": None, "reason": "重复关闭"}).status_code == 409
        assert client.post(f"/api/month-closes/{global_month}/reopen", json={"attraction_id": None, "reason": "全局复查重开"}).status_code == 200

    with SessionLocal() as db:
        actions = [row.action for row in db.query(AuditLog).filter(AuditLog.entity_type == "month_close").all()]
        assert "关闭月结" in actions
        assert "重新开启月结" in actions
        assert db.query(MonthClosure).filter(MonthClosure.closure_month == global_month, MonthClosure.attraction_id.is_(None)).count() == 1


def test_closed_circle_blocks_record_mutations() -> None:
    month = "2099-02"
    with TestClient(app) as client:
        restore_test_accounts()
        login(client, "GSMTEST01")
        options = client.get("/api/options").json()
        circle_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
        venue_id = next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")
        type_id = next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")
        deduction_type_id = next(row["id"] for row in options["deduction_types"] if row["code"] == "SAFETY")
        statement_id = next(row["id"] for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        target_id = client.get("/api/employee-targets", params={"usage": "recognition", "keyword": "CMTEST01"}).json()["items"][0]["id"]
        gsm_id = client.get("/api/me").json()["id"]

        created = client.post(
            "/api/recognitions",
            data={
                "recognition_date": f"{month}-15",
                "occurred_attraction_id": venue_id,
                "recognition_type_id": type_id,
                "recognizer_employee_id": client.get("/api/me").json()["id"],
                "content": "月结前测试",
                "employee_id": target_id,
                "idempotency_key": "month-close-record-before",
            },
        )
        assert created.status_code == 200, created.text
        record_id = created.json()["record"]["id"]
        deduction = client.post(
            "/api/deductions",
            data={
                "employee_id": target_id,
                "deduction_type_id": deduction_type_id,
                "deduction_level_id": statement_id,
                "occurred_on": f"{month}-14",
                "description": "月结前扣分",
                "idempotency_key": "month-close-deduction-before",
            },
            files={"document": ("statement.pdf", make_pdf(), "application/pdf")},
        )
        assert deduction.status_code == 200, deduction.text
        deduction_id = deduction.json()["record"]["id"]

        client.post("/api/logout")
        login(client, "CMTEST01")
        pending = client.post(
            "/api/recognitions",
            data={
                "recognition_date": f"{month}-13",
                "occurred_attraction_id": venue_id,
                "recognition_type_id": type_id,
                "recognizer_employee_id": gsm_id,
                "content": "月结前待复核",
                "employee_id": target_id,
                "idempotency_key": "month-close-pending-before",
            },
            files={"image": ("recognition.png", make_png(), "image/png")},
        )
        assert pending.status_code == 200, pending.text
        pending_id = pending.json()["record"]["id"]

        client.post("/api/logout")
        login(client, "TATEST01")
        sick_leave = client.post(
            "/api/sick-leaves",
            data={
                "employee_id": target_id,
                "leave_start_date": f"{month}-12",
                "leave_end_date": f"{month}-12",
                "leave_days": "0.5",
                "idempotency_key": "month-close-sick-before",
            },
            files={"proof": ("proof.pdf", make_pdf(), "application/pdf")},
        )
        assert sick_leave.status_code == 200, sick_leave.text
        sick_leave_id = client.get("/api/sick-leaves", params={"month": month}).json()[0]["id"]

        client.post("/api/logout")
        login(client, "HR-HEAT")
        blocked_close = client.post(f"/api/month-closes/{month}/close", json={"attraction_id": circle_id, "reason": "冻结测试月份"})
        assert blocked_close.status_code == 409
        assert blocked_close.json()["detail"]["code"] == "MONTH_CLOSE_CHECKLIST_INCOMPLETE"
        client.post("/api/logout")
        login(client, "TATEST01")
        assert client.post(f"/api/reviews/{pending_id}", json={"action": "confirm"}).status_code == 200
        client.post("/api/logout")
        login(client, "HR-HEAT")
        assert client.post(f"/api/month-closes/{month}/close", json={"attraction_id": circle_id, "reason": "冻结测试月份"}).status_code == 200

        client.post("/api/logout")
        login(client, "GSMTEST01")
        blocked = client.post(
            "/api/recognitions",
            data={
                "recognition_date": f"{month}-16",
                "occurred_attraction_id": venue_id,
                "recognition_type_id": type_id,
                "recognizer_employee_id": client.get("/api/me").json()["id"],
                "content": "冻结后不得新增",
                "employee_id": target_id,
                "idempotency_key": "month-close-record-after",
            },
        )
        assert blocked.status_code == 423
        assert blocked.json()["detail"]["code"] == "MONTH_CLOSED"
        assert client.delete(f"/api/recognitions/{record_id}").status_code == 423
        blocked_deduction = client.post(
            "/api/deductions",
            data={
                "employee_id": target_id,
                "deduction_type_id": deduction_type_id,
                "deduction_level_id": statement_id,
                "occurred_on": f"{month}-18",
                "description": "冻结后不得扣分",
                "idempotency_key": "month-close-deduction-after",
            },
            files={"document": ("statement.pdf", make_pdf(), "application/pdf")},
        )
        assert blocked_deduction.status_code == 423
        assert client.post(f"/api/deductions/{deduction_id}/void", json={"reason": "冻结后不得作废"}).status_code == 423

        client.post("/api/logout")
        login(client, "TATEST01")
        blocked_sick = client.post(
            "/api/sick-leaves",
            data={
                "employee_id": target_id,
                "leave_start_date": f"{month}-19",
                "leave_end_date": f"{month}-19",
                "leave_days": "0.5",
                "idempotency_key": "month-close-sick-after",
            },
            files={"proof": ("proof.pdf", make_pdf(), "application/pdf")},
        )
        assert blocked_sick.status_code == 423
        assert client.post(f"/api/sick-leaves/{sick_leave_id}/void", json={"reason": "冻结后不得作废"}).status_code == 423
        assert client.post(f"/api/reviews/{pending_id}", json={"action": "confirm"}).status_code == 423


def test_group_display_uses_new_leader_but_keeps_previous_leader_for_transition() -> None:
    with TestClient(app) as client:
        restore_test_accounts()
        login(client, "HR-HEAT")
        groups = client.get("/api/hr/groups").json()
        leaders = client.get("/api/hr/leader-options").json()
        group = next(row for row in groups if row["leader_id"])
        candidate = next(
            row for row in leaders
            if row["attraction_id"] == group["attraction_id"] and row["id"] != group["leader_id"]
        )
        moved = client.post(
            f"/api/hr/groups/{group['id']}/transfer",
            json={
                "revision": group["revision"],
                "new_leader_id": candidate["id"],
                "effective_date": date.today().isoformat(),
                "reason": "测试动态组名展示",
            },
        )
        assert moved.status_code == 200, moved.text
        current = next(row for row in client.get("/api/hr/groups").json() if row["id"] == group["id"])
        assert current["stored_name"] == group["stored_name"]
        assert current["name"] == f"{candidate['name']}工作组"
        assert current["previous_leader_name"] == group["leader_name"]
        assert current["previous_leader_until"]
