from __future__ import annotations

import os
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-supervisor-entry-records-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def login(client: TestClient, account: str) -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": "1234"})
    assert response.status_code == 200, response.text


def pdf_bytes() -> bytes:
    image = Image.new("RGB", (300, 400), "white")
    result = BytesIO()
    image.save(result, "PDF")
    image.close()
    return result.getvalue()


def create_deduction(client: TestClient, target_no: str, description: str) -> int:
    options = client.get("/api/options").json()
    target = next(
        row
        for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": target_no}).json()["items"]
        if row["employee_no"] == target_no
    )
    statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
    response = client.post(
        "/api/deductions",
        data={
            "employee_id": str(target["id"]),
            "deduction_type_id": str(options["deduction_types"][0]["id"]),
            "deduction_level_id": str(statement["id"]),
            "occurred_on": date.today().isoformat(),
            "description": description,
        },
        files={"document": ("statement.pdf", pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["record"]["id"]


def test_supervisor_can_switch_from_own_to_all_supervisor_deduction_records() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        own_id = create_deduction(client, "CMTEST01", "TA主管登记记录范围测试")

        client.post("/api/logout")
        login(client, "SUPTEST01")
        other_id = create_deduction(client, "CMTEST02", "主管登记记录范围测试")

        client.post("/api/logout")
        login(client, "TATEST01")
        own = client.get("/api/my-entries", params={"record_type": "deduction", "scope": "mine"})
        assert own.status_code == 200, own.text
        own_ids = {row["id"] for row in own.json()["items"]}
        assert own_id in own_ids
        assert other_id not in own_ids

        all_supervisors = client.get("/api/my-entries", params={"record_type": "deduction", "scope": "supervisors"})
        assert all_supervisors.status_code == 200, all_supervisors.text
        all_payload = all_supervisors.json()
        assert all_payload["scope"] == "supervisors"
        assert {own_id, other_id}.issubset({row["id"] for row in all_payload["items"]})

        forbidden = client.get("/api/my-entries", params={"record_type": "recognition", "scope": "supervisors"})
        assert forbidden.status_code == 400, forbidden.text


def test_only_supervisors_can_request_all_supervisor_entry_scope() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        response = client.get("/api/my-entries", params={"record_type": "deduction", "scope": "supervisors"})
        assert response.status_code == 403, response.text


def test_supervisor_member_records_remain_available() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        response = client.get("/api/member-records", params={"record_type": "recognition"})
        assert response.status_code == 200, response.text
