from __future__ import annotations

import os
from datetime import date
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-poc-idempotency-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def test_poc_replay_returns_same_record_and_conflict_on_different_payload() -> None:
    today = date.today().isoformat()
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "poc", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        payload = {
            "recognition_date": today,
            "employee_id": str(target["id"]),
            "points": "2",
            "poc_period_type": "month",
            "poc_reason": "幂等测试特别贡献",
            "idempotency_key": "poc-idempotency-001",
        }
        first = client.post("/api/recognitions/poc", data=payload)
        assert first.status_code == 200, first.text
        first_id = first.json()["record"]["id"]
        replay = client.post("/api/recognitions/poc", data=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["record"]["id"] == first_id
        assert replay.json().get("duplicate") is True
        conflict = client.post("/api/recognitions/poc", data={**payload, "poc_reason": "另一项内容"})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
