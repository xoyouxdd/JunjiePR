from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import tempfile
from io import BytesIO

from PIL import Image

from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-governance-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import ROLE_PERMISSION_CODES, SessionLocal, backfill_audit_scope_and_remove_appeals  # noqa: E402
from app.v2_models import (  # noqa: E402
    Employee,
    EmployeeMonthOrganizationSnapshot,
    GovernanceCase,
    UserAccount,
)

# Exercise the retained legacy sick-leave API without re-enabling it for production roles.
ROLE_PERMISSION_CODES["TA_SUPERVISOR"] = (*ROLE_PERMISSION_CODES["TA_SUPERVISOR"], "SICK_REGISTER")


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def activate_circle_hr() -> None:
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no="HR-HEAT").one()
        account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        db.commit()


def valid_pdf() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PDF")
    return stream.getvalue()


def create_active_deduction(client: TestClient, month: str) -> tuple[int, int]:
    login(client, "TATEST01")
    options = client.get("/api/options").json()
    target = next(
        row for row in client.get("/api/employee-targets", params={"usage": "attendance", "keyword": "CMTEST01"}).json()["items"]
        if row["employee_no"] == "CMTEST01"
    )
    deduction_type = next(row for row in options["deduction_types"] if row["code"] == "SAFETY")
    statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
    response = client.post(
        "/api/deductions",
        data={
            "employee_id": str(target["id"]),
            "deduction_type_id": str(deduction_type["id"]),
            "deduction_level_id": str(statement["id"]),
            "occurred_on": f"{month}-12",
            "description": "治理流程隔离测试扣分",
            "idempotency_key": f"governance-deduction-{month}",
        },
        files={"document": ("statement.pdf", valid_pdf(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["record"]["id"], target["id"]


def test_online_appeals_are_removed_and_old_appeal_rows_deleted() -> None:
    with TestClient(app) as client:
        login(client, "CMTEST01")
        assert client.get("/api/governance/appealable-records").status_code == 404
        assert client.post("/api/governance/appeals", json={"record_type": "deduction", "record_id": 1, "reason": "已改为线下申诉"}).status_code == 404
        assert client.get("/api/governance/cases").status_code == 404
        client.post("/api/logout")

    with SessionLocal() as db:
        cm = db.query(Employee).filter_by(employee_no="CMTEST01").one()
        for case_type in ("appeal", "month_correction"):
            db.add(GovernanceCase(case_type=case_type, record_type="deduction", record_id=1, submitted_by=cm.id, submitted_by_name=cm.name, reason="旧数据", due_at=datetime.now()))
        db.commit()
        backfill_audit_scope_and_remove_appeals(db)
        assert db.query(GovernanceCase).filter_by(case_type="appeal").count() == 0
        assert db.query(GovernanceCase).filter_by(case_type="month_correction").count() >= 1


def test_circle_hr_sees_only_own_circle_audit_summaries() -> None:
    with TestClient(app) as client:
        deduction_id, _cm_id = create_active_deduction(client, "2099-04")
        client.post("/api/logout")
        with SessionLocal() as db:
            for employee_no in ("HR-HEAT", "HR-DWARF"):
                employee = db.query(Employee).filter_by(employee_no=employee_no).one()
                account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
                account.password_hash = hash_password("1234")
                account.enabled = True
                account.must_change_password = False
            db.commit()
        entity = f"deduction:{deduction_id}"

        login(client, "HR-HEAT")
        heat_logs = client.get("/api/admin/logs").json()
        assert any(row["entity"] == entity for row in heat_logs)
        assert all(set(row) == {"id", "time", "operator", "action", "entity", "reason"} for row in heat_logs)
        client.post("/api/logout")

        login(client, "HR-DWARF")
        assert not any(row["entity"] == entity for row in client.get("/api/admin/logs").json())
        client.post("/api/logout")

        login(client, "HR01", "HR123")
        assert any(row["entity"] == entity for row in client.get("/api/admin/logs").json())
        client.post("/api/logout")

        login(client, "CMTEST01")
        assert client.get("/api/admin/logs").status_code == 403


def test_month_snapshot_correction_ledger_and_operations_aggregate() -> None:
    month = "2099-05"
    with TestClient(app) as client:
        _deduction_id, cm_id = create_active_deduction(client, month)
        client.post("/api/logout")
        activate_circle_hr()
        login(client, "HR-HEAT")
        options = client.get("/api/options").json()
        heat_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
        closed = client.post(f"/api/month-closes/{month}/close", json={"attraction_id": heat_id, "reason": "治理测试月结冻结"})
        assert closed.status_code == 200, closed.text
        statistics = client.get("/api/statistics", params={"month": month, "attraction_id": heat_id})
        assert statistics.status_code == 200, statistics.text
        assert statistics.json()["organization_basis"] == "月结封存归属"
        client.post("/api/logout")

        login(client, "HR01", "HR123")
        reopened = client.post(f"/api/month-closes/{month}/reopen", json={"attraction_id": heat_id, "reason": "治理测试需要复查月结"})
        assert reopened.status_code == 200, reopened.text
        operations = client.get("/api/admin/operations-health")
        assert operations.status_code == 200
        assert set(operations.json()["governance"]) == {"overdue_recognition_reviews", "retention_review_files"}
        screenshot = client.post("/api/security/screenshot-event")
        assert screenshot.json()["message"] == "系统已记录截图按键事件；页面访问和导出均受审计，请勿分享敏感数据。"

    with SessionLocal() as db:
        assert db.query(EmployeeMonthOrganizationSnapshot).filter_by(employee_id=cm_id, score_month=month).count() == 1
        correction = db.query(GovernanceCase).filter_by(case_type="month_correction", score_month=month).one()
        assert correction.decision == "month_reopened"
