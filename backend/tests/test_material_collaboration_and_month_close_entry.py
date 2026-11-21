from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta
from importlib import import_module
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-material-collaboration-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.deduction_materials import process_next_deduction_material_job
from app.v2_crypto import hash_password  # noqa: E402
from app.database.reference_data import ROLE_PERMISSION_CODES
from app.database.connection import SessionLocal
from app.v2_models import AuditLog, DeductionRecord, MonthClosure, UserAccount, UserSession  # noqa: E402
from app.services.performance_details import monthly_member_score_rows

# Test the retained legacy route under a test-only grant, not a production role change.
ROLE_PERMISSION_CODES["TA_SUPERVISOR"] = (*ROLE_PERMISSION_CODES["TA_SUPERVISOR"], "SICK_REGISTER")


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def make_pdf() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (16, 16), "white").save(stream, format="PDF")
    return stream.getvalue()


def restore_circle_hr_test_login() -> None:
    """Circle HR accounts intentionally use one-time passwords when seeded."""
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(login_account="HR-HEAT").one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        account.failed_attempts = 0
        account.locked_until = None
        db.query(UserSession).filter_by(account_id=account.id).delete(synchronize_session=False)
        db.commit()


def create_pending_material(client: TestClient) -> dict:
    options = client.get("/api/options").json()
    target = next(
        row
        for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"]
        if row["employee_no"] == "CMTEST01"
    )
    response = client.post(
        "/api/deductions",
        data={
            "employee_id": str(target["id"]),
            "deduction_type_id": str(options["deduction_types"][0]["id"]),
            "deduction_level_id": str(next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")["id"]),
            "occurred_on": date.today().isoformat(),
            "description": "TA GSM/GSM协作补材料测试",
        },
    )
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["status"] == "pending_material"
    return record


def test_advanced_deduction_uses_registration_day_and_month_only_for_new_records(monkeypatch) -> None:
    class RegistrationClock(datetime):
        @classmethod
        def now(cls):
            return cls(2037, 4, 2, 9, 15)

    monkeypatch.setattr(import_module("app.routers.deductions"), "datetime", RegistrationClock)
    monkeypatch.setattr(import_module("app.main"), "start_deduction_material_worker", lambda: None)
    incident = "2037-03-29"
    effective = "2037-04-02"
    doc = {"document": ("statement.pdf", make_pdf(), "application/pdf")}
    with TestClient(app) as client:
        login(client, "TAGSMTEST01")
        options = client.get("/api/options").json()
        levels = {row["code"]: row["id"] for row in options["deduction_levels"]}
        employee = next(
            row for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        form = {
            "employee_id": str(employee["id"]), "deduction_type_id": str(options["deduction_types"][0]["id"]),
            "occurred_on": incident, "description": "跨月生效日期测试",
        }
        response = client.post("/api/deductions", data={**form, "deduction_level_id": str(levels["STATEMENT"])})
        assert response.status_code == 200, response.text
        ordinary = response.json()["record"]
        assert ordinary["occurred_on"] == incident and ordinary["incident_on"] is None
        with SessionLocal() as db:
            db.add(MonthClosure(closure_month=incident[:7], status="closed"))
            db.commit()

        advanced_ids = set()
        for role, code in [("TAGSMTEST01", "MEMO"), ("GSMTEST01", "WARNING_1"), ("GSMTEST01", "WARNING_2")]:
            client.post("/api/logout")
            login(client, role)
            response = client.post("/api/deductions", data={**form, "deduction_level_id": str(levels[code])}, files=doc)
            assert response.status_code == 200, response.text
            record = response.json()["record"]
            assert record["occurred_on"] == effective and record["incident_on"] == incident
            assert record["submitted_at"].startswith(effective)
            advanced_ids.add(record["id"])

        source_month = client.get("/api/declaration-statistics", params={"month": incident[:7]}).json()["records"]
        decision_month = client.get("/api/declaration-statistics", params={"month": effective[:7]}).json()["records"]
        assert {row["id"] for row in source_month} == {ordinary["id"]}
        assert {row["id"] for row in decision_month} == advanced_ids
        assert all(row["actual_points"] > 0 and row["deduction_month"] == effective[:7] for row in decision_month)

        pending_form = {**form, "occurred_on": "2037-03-30", "deduction_level_id": str(levels["MEMO"])}
        pending = client.post("/api/deductions", data=pending_form)
        assert pending.status_code == 200, pending.text
        assert pending.json()["record"]["actual_points"] == 0
        duplicate = client.post("/api/deductions", data=pending_form)
        assert duplicate.status_code == 409 and duplicate.json()["detail"]["code"] == "PENDING_MATERIAL_EXISTS"
        material_path = f"/api/deductions/{pending.json()['record']['id']}/material"
        supplement = client.post(material_path, files=doc)
        assert supplement.status_code == 200, supplement.text
        assert process_next_deduction_material_job()
        ready = client.get(material_path + "-status").json()["record"]
        assert ready["material_status"] == "ready" and ready["occurred_on"] == effective
        assert ready["incident_on"] == pending_form["occurred_on"] and ready["actual_points"] > 0

        with SessionLocal() as db:
            db.add(MonthClosure(closure_month=effective[:7], status="closed"))
            db.commit()
        blocked = client.post("/api/deductions", data=pending_form, files=doc)
        assert blocked.status_code == 423 and blocked.json()["detail"]["code"] == "MONTH_CLOSED"


def test_tagsm_and_gsm_can_filter_and_complete_shared_pending_material() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        record = create_pending_material(client)
        assert record["material_uploaded_by_name"] == ""
        own_queue = client.get("/api/deductions/pending-materials", params={"scope": "mine"})
        assert own_queue.status_code == 200, own_queue.text
        assert own_queue.json()["scope"] == "mine"
        queued = next(row for row in own_queue.json()["items"] if row["id"] == record["id"])
        assert queued["material_uploaded_by_name"] == ""

        client.post("/api/logout")
        login(client, "TAGSMTEST01")
        uploader_name = client.get("/api/me").json()["name"]
        assert uploader_name != record["submitter_name"]
        all_queue = client.get("/api/deductions/pending-materials", params={"scope": "all"})
        assert all_queue.status_code == 200, all_queue.text
        assert all_queue.json()["scope"] == "all"
        assert any(row["id"] == record["id"] for row in all_queue.json()["items"])
        status = client.get(f"/api/deductions/{record['id']}/material-status")
        assert status.status_code == 200, status.text
        supplemented = client.post(
            f"/api/deductions/{record['id']}/material",
            files={"document": ("statement.pdf", make_pdf(), "application/pdf")},
        )
        assert supplemented.status_code == 200, supplemented.text
        submitted_record = supplemented.json()["record"]
        assert submitted_record["material_status"] == "processing"
        assert submitted_record["material_uploaded_by_name"] == uploader_name
        assert submitted_record["submitter_name"] == record["submitter_name"]

        client.post("/api/logout")
        login(client, "HR01", "HR123")
        statistics = client.get("/api/declaration-statistics", params={"keyword": record["employee_no"]})
        assert statistics.status_code == 200, statistics.text
        listed = next(row for row in statistics.json()["records"] if row["id"] == record["id"])
        assert listed["material_uploaded_by_name"] == uploader_name
        assert listed["submitter_name"] == record["submitter_name"]

        # Model a historical record with a named audit event but no uploader columns.
        with SessionLocal() as db:
            stored = db.get(DeductionRecord, record["id"])
            month = stored.deduction_month
            event = db.query(AuditLog).filter_by(
                entity_type="deduction", entity_id=str(stored.id), action="补充扣分材料",
            ).one()
            for entity_type, entity_id, action, created_at in [
                ("deduction", event.entity_id, event.action, event.created_at - timedelta(seconds=1)),
                ("deduction", event.entity_id, "登记扣分", event.created_at),
                ("deduction_upgrade", event.entity_id, event.action, event.created_at),
                ("deduction", str(stored.id + 1000), event.action, event.created_at),
            ]:
                db.add(AuditLog(
                    entity_type=entity_type, entity_id=entity_id, action=action,
                    operator_name="不应显示的操作人", created_at=created_at,
                ))
            stored.material_uploaded_by = stored.material_uploaded_by_name = stored.material_uploaded_at = None
            db.commit()

        statistics = client.get("/api/declaration-statistics", params={"month": month, "keyword": record["employee_no"]})
        assert statistics.status_code == 200, statistics.text
        listed = next(row for row in statistics.json()["detail_records"] if row["id"] == record["id"])
        assert listed["material_uploaded_by_name"] == uploader_name
        assert listed["submitter_name"] == record["submitter_name"]

        with SessionLocal() as db:
            event = db.query(AuditLog).filter_by(
                entity_type="deduction", entity_id=str(record["id"]), action="补充扣分材料",
            ).order_by(AuditLog.created_at.desc()).first()
            event.after_json = None  # Older events may only have the operator snapshot.
            db.commit()
            member = monthly_member_score_rows(db, month, [record["employee_id"]])[0]
            deduction = next(row for row in member["details"]["all_records"] if row["record_type"] == "deduction")
            assert deduction["material_uploaded_by_name"] == uploader_name
            assert deduction["operator_name"] == record["submitter_name"]

        client.post("/api/logout")
        login(client, "TATEST01")
        entries = client.get("/api/my-entries", params={"record_type": "deduction", "month": month})
        assert entries.status_code == 200, entries.text
        listed = next(row for row in entries.json()["items"] if row["id"] == record["id"])
        assert listed["material_uploaded_by_name"] == uploader_name
        assert listed["submitter_name"] == record["submitter_name"]

        status = client.get(f"/api/deductions/{record['id']}/material-status")
        assert status.status_code == 200, status.text
        assert status.json()["record"]["material_uploaded_by_name"] == uploader_name
        with SessionLocal() as db:
            assert db.get(DeductionRecord, record["id"]).material_uploaded_by_name is None


def test_circle_hr_has_no_new_pending_material_queue_or_submission_permission() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        record = create_pending_material(client)
        client.post("/api/logout")
        restore_circle_hr_test_login()
        login(client, "HR-HEAT")
        assert client.get("/api/deductions/pending-materials", params={"scope": "all"}).status_code == 403
        blocked = client.post(
            f"/api/deductions/{record['id']}/material",
            files={"document": ("statement.pdf", make_pdf(), "application/pdf")},
        )
        assert blocked.status_code == 403


def test_circle_hr_month_close_action_opens_last_month_close_page() -> None:
    with TestClient(app) as client:
        restore_circle_hr_test_login()
        login(client, "HR-HEAT")
        items = client.get("/api/action-center").json()["items"]
        month_close = next(row for row in items if row["type"] == "month_close")
        assert month_close["title"] == "上月待月结景点圈"
        assert month_close["tab"] == "monthClose"
        assert date.today().replace(day=1).strftime("%Y-%m") not in month_close["description"]


def test_violation_sick_leave_without_statement_material_enters_the_same_collaboration_queue() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "attendance", "keyword": "CMTEST02"}).json()["items"]
            if row["employee_no"] == "CMTEST02"
        )
        submitted = client.post(
            "/api/sick-leaves",
            data={
                "employee_id": str(target["id"]),
                "leave_start_date": date.today().isoformat(),
                "leave_end_date": date.today().isoformat(),
                "leave_days": "1",
                "note": "违规病假待补声明材料测试",
                "is_violation": "true",
            },
            files={"proof": ("sick-proof.pdf", make_pdf(), "application/pdf")},
        )
        assert submitted.status_code == 200, submitted.text
        payload = submitted.json()
        assert payload["violation"]["status"] == "pending_material"
        assert payload["violation"]["material_status"] == "missing"

        client.post("/api/logout")
        login(client, "GSMTEST01")
        queue = client.get("/api/deductions/pending-materials", params={"scope": "all"})
        assert queue.status_code == 200, queue.text
        assert any(row["id"] == payload["violation"]["id"] for row in queue.json()["items"])
