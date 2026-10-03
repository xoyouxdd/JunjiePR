from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-acting-duty-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal, migrate_legacy_duty_assignments  # noqa: E402
from app.v2_models import (  # noqa: E402
    Employee,
    EmployeeActingDuty,
    EmployeeRoleAssignment,
    GroupLeaderAssignment,
    GroupMembership,
    RecognitionRecord,
    Role,
    SystemAlert,
    WorkGroup,
)
from app.v2_services import base_role_at, current_leader_for_employee, role_at  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    client.post("/api/logout")
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return stream.getvalue()


def employee_id(employee_no: str) -> int:
    with SessionLocal() as db:
        return db.query(Employee).filter_by(employee_no=employee_no).one().id


def recognition_form(client: TestClient, *, recognizer_no: str, key: str, target_no: str | None = None) -> dict:
    options = client.get("/api/options").json()
    venue_id = next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")
    circle_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
    type_id = next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")
    today = date.today().isoformat()
    recognizers = client.get("/api/recognizers", params={"attraction_id": circle_id, "recognition_date": today}).json()
    recognizer = next(row for row in recognizers if row.get("employee_no") == recognizer_no)
    data = {
        "recognition_date": today,
        "occurred_attraction_id": str(venue_id),
        "recognition_type_id": str(type_id),
        "recognizer_employee_id": str(recognizer["id"]),
        "content": "代理职务测试",
        "idempotency_key": key,
    }
    if target_no:
        data["employee_id"] = str(employee_id(target_no))
    return data


def test_acting_ta_keeps_base_identity_and_original_group() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        me = client.get("/api/me").json()
        assert me["role_code"] == "TA_SUPERVISOR"
        assert me["base_role_code"] == "CM"
        assert me["role_label"] == "CM · 代理TA主管"
        assert {"SELF_RECOGNITION", "EMPLOYEE_ADD", "REVIEW_DIRECT"}.issubset(me["permissions"])
        assert me["leader_name"] == "测试主管"
        with SessionLocal() as db:
            ta_id = db.query(Employee).filter_by(employee_no="TATEST01").one().id
            assert base_role_at(db, ta_id).code == "CM"
            assert role_at(db, ta_id).code == "TA_SUPERVISOR"
            assert current_leader_for_employee(db, ta_id).employee_no == "SUPTEST01"


def test_ta_self_recognition_goes_to_original_leader_and_cannot_name_self() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        own_recognizer = recognition_form(client, recognizer_no="TATEST01", key="ta-self-own")
        denied = client.post("/api/recognitions", data=own_recognizer, files={"image": ("proof.png", png_bytes(), "image/png")})
        assert denied.status_code == 400, denied.text
        assert "本人" in denied.json()["detail"]

        allowed = client.post(
            "/api/recognitions",
            data=recognition_form(client, recognizer_no="SUPTEST01", key="ta-self-ok"),
            files={"image": ("proof.png", png_bytes(), "image/png")},
        )
        assert allowed.status_code == 200, allowed.text
        record_id = allowed.json()["record"]["id"]
        with SessionLocal() as db:
            row = db.get(RecognitionRecord, record_id)
            assert row.status == "pending"
            assert row.employee_acting_duty_code == "TA_SUPERVISOR"
            assert row.assigned_reviewer_id == db.query(Employee).filter_by(employee_no="SUPTEST01").one().id

        # The TA cannot see their own record in their own review queue.
        queue = client.get("/api/reviews").json()["items"]
        assert record_id not in {item["id"] for item in queue}

        login(client, "SUPTEST01")
        queue = client.get("/api/reviews").json()["items"]
        assert record_id in {item["id"] for item in queue}
        reviewed = client.post(f"/api/reviews/{record_id}", json={"action": "confirm"})
        assert reviewed.status_code == 200, reviewed.text


def test_leaders_cannot_credit_an_acting_ta_but_gsm_can() -> None:
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        denied = client.post("/api/recognitions", data=recognition_form(client, recognizer_no="GSMTEST01", key="sup-to-ta", target_no="TATEST01"))
        assert denied.status_code == 403, denied.text

        login(client, "GSMTEST01")
        allowed = client.post("/api/recognitions", data=recognition_form(client, recognizer_no="GSMTEST01", key="gsm-to-ta", target_no="TATEST01"))
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["record"]["status"] == "confirmed"


def test_legacy_ta_role_rows_are_split_and_rejoin_the_previous_group() -> None:
    today = date.today()
    with TestClient(app):
        with SessionLocal() as db:
            roles = {role.code: role for role in db.query(Role).all()}
            circle_id = db.query(Employee).filter_by(employee_no="CMTEST01").one().attraction_id
            supervisor = db.query(Employee).filter_by(employee_no="SUPTEST01").one()
            group = db.query(GroupLeaderAssignment).filter_by(leader_employee_id=supervisor.id, status="active").first().group
            legacy = Employee(employee_no="LEGACYTA01", name="旧TA", attraction_id=circle_id, is_active=True)
            unknown = Employee(employee_no="LEGACYTA02", name="未知本职TA", attraction_id=circle_id, is_active=True)
            db.add_all([legacy, unknown])
            db.flush()
            started = (today - timedelta(days=10)).isoformat()
            db.add_all(
                [
                    EmployeeRoleAssignment(employee_id=legacy.id, role_id=roles["CM"].id, starts_on=(today - timedelta(days=100)).isoformat(), ends_on=started, status="expired"),
                    EmployeeRoleAssignment(employee_id=legacy.id, role_id=roles["TA_SUPERVISOR"].id, starts_on=started, ends_on=(today + timedelta(days=30)).isoformat(), assignment_type="temporary", return_role_id=roles["CM"].id, status="active"),
                    GroupMembership(group_id=group.id, employee_id=legacy.id, starts_on=(today - timedelta(days=100)).isoformat(), ends_on=started, status="ended"),
                    EmployeeRoleAssignment(employee_id=unknown.id, role_id=roles["TA_SUPERVISOR"].id, starts_on=started, status="active"),
                ]
            )
            db.commit()
            unresolved = migrate_legacy_duty_assignments(db)

            assert base_role_at(db, legacy.id).code == "CM"
            assert role_at(db, legacy.id).code == "TA_SUPERVISOR"
            duty = db.query(EmployeeActingDuty).filter_by(employee_id=legacy.id).one()
            assert duty.starts_on == started and duty.status == "active"
            assert current_leader_for_employee(db, legacy.id).id == supervisor.id

            assert {item["employee_id"] for item in unresolved} == {unknown.id}
            assert role_at(db, unknown.id).code == "TA_SUPERVISOR"
            assert db.query(SystemAlert).filter_by(alert_type="acting_duty_migration", employee_id=unknown.id).count() == 1


def test_import_day_duty_rows_use_the_base_role_returned_to_afterwards() -> None:
    today = date.today()
    with TestClient(app):
        with SessionLocal() as db:
            roles = {role.code: role for role in db.query(Role).all()}
            circle_id = db.query(Employee).filter_by(employee_no="CMTEST01").one().attraction_id
            imported = Employee(employee_no="IMPORTTA01", name="导入日TA", attraction_id=circle_id, is_active=True)
            db.add(imported)
            db.flush()
            go_live = (today - timedelta(days=50)).isoformat()
            back_to_tr = (today - timedelta(days=46)).isoformat()
            db.add_all(
                [
                    EmployeeRoleAssignment(employee_id=imported.id, role_id=roles["TA_SUPERVISOR"].id, starts_on=go_live, ends_on=back_to_tr, status="expired"),
                    EmployeeRoleAssignment(employee_id=imported.id, role_id=roles["TR"].id, starts_on=back_to_tr, status="active"),
                ]
            )
            db.commit()
            unresolved = migrate_legacy_duty_assignments(db)
            assert imported.id not in {item["employee_id"] for item in unresolved}
            assert base_role_at(db, imported.id, go_live).code == "TR"
            assert role_at(db, imported.id, go_live).code == "TA_SUPERVISOR"
            assert role_at(db, imported.id).code == "TR"


def test_hr_confirms_base_of_unresolved_legacy_ta_and_keeps_the_group() -> None:
    today = date.today()
    with TestClient(app) as client:
        with SessionLocal() as db:
            roles = {role.code: role for role in db.query(Role).all()}
            circle_id = db.query(Employee).filter_by(employee_no="CMTEST01").one().attraction_id
            legacy = Employee(employee_no="LEGACYTA03", name="旧TA未定本职", attraction_id=circle_id, is_active=True)
            member = db.query(Employee).filter_by(employee_no="TRTEST01").one()
            db.add(legacy)
            db.flush()
            db.add(EmployeeRoleAssignment(employee_id=legacy.id, role_id=roles["TA_SUPERVISOR"].id, starts_on=(today - timedelta(days=30)).isoformat(), status="active"))
            group = WorkGroup(name="旧TA工作组", attraction_id=circle_id, status="active")
            db.add(group)
            db.flush()
            db.add(GroupLeaderAssignment(group_id=group.id, leader_employee_id=legacy.id, starts_on=(today - timedelta(days=30)).isoformat(), status="active"))
            db.query(GroupMembership).filter_by(employee_id=member.id, status="active").update({GroupMembership.status: "ended", GroupMembership.ends_on: today.isoformat()})
            db.add(GroupMembership(group_id=group.id, employee_id=member.id, starts_on=today.isoformat(), status="active"))
            db.commit()
            legacy_id, group_id = legacy.id, group.id

        login(client, "HR01", "HR123")
        confirmed = client.put(f"/api/hr/employees/{legacy_id}", json={"role_code": "TR", "reason": "HR确认本职"})
        assert confirmed.status_code == 200, confirmed.text
        with SessionLocal() as db:
            assert base_role_at(db, legacy_id).code == "TR"
            assert role_at(db, legacy_id).code == "TA_SUPERVISOR"
            assert db.query(GroupLeaderAssignment).filter_by(group_id=group_id, leader_employee_id=legacy_id, status="active").count() == 1
            assert db.get(WorkGroup, group_id).status == "active"


def test_hr_sets_and_ends_a_duty_without_moving_the_group() -> None:
    with TestClient(app) as client:
        login(client, "HR01", "HR123")
        cm_id = employee_id("CMTEST02")
        with SessionLocal() as db:
            before_group = current_leader_for_employee(db, cm_id).employee_no
        set_duty = client.put(f"/api/hr/employees/{cm_id}", json={"role_code": "TA_SUPERVISOR", "group_id": None, "leader_id": None, "reason": "代理TA"})
        assert set_duty.status_code == 200, set_duty.text
        with SessionLocal() as db:
            assert role_at(db, cm_id).code == "TA_SUPERVISOR"
            assert base_role_at(db, cm_id).code == "CM"
            assert current_leader_for_employee(db, cm_id).employee_no == before_group

        bad = client.put(f"/api/hr/employees/{cm_id}", json={"role_code": "TA_GSM", "reason": "本职不符"})
        assert bad.status_code == 400, bad.text

        ended = client.put(f"/api/hr/employees/{cm_id}", json={"role_code": "CM", "reason": "结束代理"})
        assert ended.status_code == 200, ended.text
        with SessionLocal() as db:
            assert role_at(db, cm_id).code == "CM"
            assert current_leader_for_employee(db, cm_id).employee_no == before_group


def test_leader_cannot_join_the_group_they_lead() -> None:
    with TestClient(app) as client:
        login(client, "HR01", "HR123")
        ta_id = employee_id("TATEST01")
        with SessionLocal() as db:
            led_group = db.query(GroupLeaderAssignment).filter_by(leader_employee_id=ta_id, status="active").first().group_id
        response = client.put(f"/api/hr/employees/{ta_id}", json={"group_id": led_group, "leader_id": ta_id, "reason": "错误分组"})
        assert response.status_code == 400, response.text
        with SessionLocal() as db:
            assert db.query(WorkGroup).get(led_group) is not None
            assert current_leader_for_employee(db, ta_id).employee_no == "SUPTEST01"
