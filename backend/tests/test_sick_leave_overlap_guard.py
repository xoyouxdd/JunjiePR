from __future__ import annotations

import os
import tempfile
import time
from datetime import date
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-sick-leave-overlap-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402


def login(client: TestClient) -> None:
    response = client.post("/api/login", json={"employee_no": "TATEST01", "password": "1234"})
    assert response.status_code == 200, response.text


def image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format="PNG")
    return output.getvalue()


def pdf_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (60, 80), "white").save(output, format="PDF")
    return output.getvalue()


def sick_leave_payload(employee_id: int, start: str, end: str, request_key: str) -> dict[str, str]:
    return {
        "employee_id": str(employee_id),
        "leave_start_date": start,
        "leave_end_date": end,
        "leave_days": str((date.fromisoformat(end) - date.fromisoformat(start)).days + 1),
        "rest_day_confirmed": "true",
        "idempotency_key": request_key,
    }


def test_active_sick_leave_date_overlap_is_visible_and_blocked_until_voided() -> None:
    with TestClient(app) as client:
        login(client)
        targets = client.get("/api/employee-targets", params={"usage": "attendance", "keyword": "CMTEST01"}).json()["items"]
        target = next(row for row in targets if row["employee_no"] == "CMTEST01")
        first = client.post(
            "/api/sick-leaves",
            data=sick_leave_payload(target["id"], "2099-08-10", "2099-08-11", "sick-overlap-first"),
            files={"proof": ("first.png", image_bytes(), "image/png")},
        )
        assert first.status_code == 200, first.text
        row = client.get("/api/sick-leaves", params={"month": "2099-08"}).json()[0]

        check = client.get(
            "/api/sick-leaves/overlap-check",
            params={"employee_id": target["id"], "leave_start_date": "2099-08-11", "leave_end_date": "2099-08-12"},
        )
        assert check.status_code == 200, check.text
        assert check.json()["conflict"] is True
        assert check.json()["detail"]["code"] == "SICK_LEAVE_DATE_OVERLAP"
        assert check.json()["detail"]["records"][0]["id"] == row["id"]

        blocked = client.post(
            "/api/sick-leaves",
            data=sick_leave_payload(target["id"], "2099-08-11", "2099-08-12", "sick-overlap-second"),
            files={"proof": ("second.png", image_bytes(), "image/png")},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "SICK_LEAVE_DATE_OVERLAP"

        voided = client.post(f"/api/sick-leaves/{row['id']}/void", json={"reason": "测试作废后允许重新登记"})
        assert voided.status_code == 200, voided.text
        allowed = client.post(
            "/api/sick-leaves",
            data=sick_leave_payload(target["id"], "2099-08-11", "2099-08-12", "sick-overlap-after-void"),
            files={"proof": ("after-void.png", image_bytes(), "image/png")},
        )
        assert allowed.status_code == 200, allowed.text


def test_mobile_form_contains_overlap_guard_and_chinese_message() -> None:
    script = (Path(__file__).parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "bindSickLeaveOverlapGuard(form)" in script
    assert "/api/sick-leaves/overlap-check" in script
    assert "已有缺勤登记" in script


def test_violation_sick_leave_creates_linked_pending_declaration_then_accepts_photo_material() -> None:
    with TestClient(app) as client:
        login(client)
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "attendance", "keyword": "CMTEST02"}).json()["items"]
            if row["employee_no"] == "CMTEST02"
        )
        payload = sick_leave_payload(target["id"], "2099-10-04", "2099-10-04", "violation-later-material")
        payload.update({"is_violation": "true", "note": "未按规定时间请病假"})
        created = client.post("/api/sick-leaves", data=payload, files={"proof": ("leave-proof.png", image_bytes(), "image/png")})
        assert created.status_code == 200, created.text
        violation = created.json()["violation"]
        assert violation["status"] == "pending_material"
        assert "关联缺勤：2099-10-04 至 2099-10-04" in violation["description"]

        supplemented = client.post(
            f"/api/deductions/{violation['id']}/material",
            files=[("document_images", ("statement.png", image_bytes(), "image/png"))],
        )
        assert supplemented.status_code == 200, supplemented.text
        for _ in range(50):
            current = client.get(f"/api/deductions/{violation['id']}/material-status").json()["record"]
            if current["material_status"] != "processing":
                break
            time.sleep(0.1)
        assert current["material_status"] == "ready"
        assert current["status"] == "active"


def test_second_violation_sick_leave_uses_existing_statement_upgrade_workorder() -> None:
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/options").json()
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        violation_type = next(row for row in options["deduction_types"] if row["code"] == "SICK_LEAVE_VIOLATION")
        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        first = client.post(
            "/api/deductions",
            data={
                "employee_id": str(target["id"]), "deduction_type_id": str(violation_type["id"]),
                "deduction_level_id": str(statement["id"]), "occurred_on": "2099-10-01", "description": "首次违规病假声明",
            },
            files={"document": ("first.pdf", pdf_bytes(), "application/pdf")},
        )
        assert first.status_code == 200, first.text
        preview = client.get(
            "/api/sick-leaves/violation-upgrade-preview",
            params={"employee_id": target["id"], "leave_start_date": "2099-10-05"},
        )
        assert preview.status_code == 200 and preview.json()["eligible"], preview.text
        reviewer = preview.json()["reviewers"][0]
        payload = sick_leave_payload(target["id"], "2099-10-05", "2099-10-05", "violation-upgrade-later")
        payload.update({"is_violation": "true", "violation_reviewer_id": str(reviewer["id"]), "note": "第二次违规病假"})
        second = client.post("/api/sick-leaves", data=payload, files={"proof": ("proof.png", image_bytes(), "image/png")})
        assert second.status_code == 200, second.text
        assert second.json()["violation_upgrade"] is True
        record = second.json()["violation"]
        assert record["status"] == "pending_material"

        supplemented = client.post(
            f"/api/deductions/{record['id']}/material",
            files={"document": ("second.pdf", pdf_bytes(), "application/pdf")},
        )
        assert supplemented.status_code == 200, supplemented.text
        for _ in range(50):
            current = client.get(f"/api/deductions/{record['id']}/material-status").json()["record"]
            if current["material_status"] != "processing":
                break
            time.sleep(0.1)
        assert current["status"] == "pending_upgrade"
        client.post("/api/logout")
        assert client.post("/api/login", json={"employee_no": reviewer["employee_no"], "password": "1234"}).status_code == 200
        pending = client.get("/api/deduction-upgrades/pending")
        assert pending.status_code == 200, pending.text
        assert any(item["second_record"]["id"] == record["id"] for item in pending.json()["items"])
