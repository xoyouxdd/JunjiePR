from __future__ import annotations

import os
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
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import (  # noqa: E402
    DeductionRecord,
    Employee,
    EmployeeMonthOrganizationSnapshot,
    GovernanceCase,
    UserAccount,
)


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


def test_appeal_independent_review_and_in_app_notice() -> None:
    with TestClient(app) as client:
        deduction_id, cm_id = create_active_deduction(client, "2099-04")
        client.post("/api/logout")

        login(client, "TRTEST01")
        foreign = client.post(
            "/api/governance/appeals",
            json={"record_type": "deduction", "record_id": deduction_id, "reason": "这不是本人的记录，不能申诉"},
        )
        assert foreign.status_code == 404
        client.post("/api/logout")

        login(client, "CMTEST01")
        appealable = client.get("/api/governance/appealable-records")
        assert appealable.status_code == 200
        assert any(row["record_id"] == deduction_id for row in appealable.json()["items"])
        payload = {"record_type": "deduction", "record_id": deduction_id, "reason": "请复核本条扣分所依据的事实和材料。"}
        created = client.post("/api/governance/appeals", json=payload)
        assert created.status_code == 200, created.text
        case_id = created.json()["case"]["id"]
        assert client.post("/api/governance/appeals", json=payload).status_code == 409
        assert client.get("/api/governance/cases").json()["items"][0]["id"] == case_id
        client.post("/api/logout")

        # Give the highest administrator a conflicting source role in the isolated fixture.
        with SessionLocal() as db:
            record = db.get(DeductionRecord, deduction_id)
            admin = db.query(Employee).filter_by(employee_no="HR01").one()
            record.submitter_id = admin.id
            db.commit()

        login(client, "HR01", "HR123")
        conflict = client.post(
            f"/api/governance/cases/{case_id}/resolve",
            json={"decision": "uphold", "resolution": "原登记人不得参与本条治理复核。"},
        )
        assert conflict.status_code == 409
        client.post("/api/logout")

        activate_circle_hr()
        login(client, "HR-HEAT")
        cases = client.get("/api/governance/cases")
        assert cases.status_code == 200
        assert any(row["id"] == case_id and row["can_resolve"] for row in cases.json()["items"])
        resolved = client.post(
            f"/api/governance/cases/{case_id}/resolve",
            json={"decision": "correction_required", "resolution": "材料存在需要补充核对的部分，请通过受控更正流程处理。"},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["case"]["decision"] == "correction_required"

    with SessionLocal() as db:
        assert db.get(GovernanceCase, case_id).status == "resolved"


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
        assert set(operations.json()["governance"]) == {"open_cases", "overdue_cases", "overdue_recognition_reviews", "retention_review_files"}
        screenshot = client.post("/api/security/screenshot-event")
        assert screenshot.json()["message"] == "系统已记录截图按键事件；页面访问和导出均受审计，请勿分享敏感数据。"

    with SessionLocal() as db:
        assert db.query(EmployeeMonthOrganizationSnapshot).filter_by(employee_id=cm_id, score_month=month).count() == 1
        correction = db.query(GovernanceCase).filter_by(case_type="month_correction", score_month=month).one()
        assert correction.decision == "month_reopened"
