from __future__ import annotations

import os
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-attendance-search-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402


def login(client: TestClient, account: str) -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": "1234"})
    assert response.status_code == 200, response.text


def test_supervisor_attendance_search_is_global_and_requires_keyword() -> None:
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        empty = client.get("/api/employee-targets", params={"usage": "attendance"})
        assert empty.status_code == 200, empty.text
        body = empty.json()
        assert body["items"] == []
        assert "全部景点圈" in body["search_scope"]
        found = client.get("/api/employee-targets", params={"usage": "attendance", "keyword": "CMTEST01"})
        assert found.status_code == 200, found.text
        assert any(row["employee_no"] == "CMTEST01" for row in found.json()["items"])


def test_ta_supervisor_attendance_search_stays_in_home_circle() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        options = client.get("/api/options").json()
        dwarf_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "矮人迷宫")
        result = client.get(
            "/api/employee-targets",
            params={"usage": "attendance", "keyword": "CMTEST01", "attraction_id": dwarf_id},
        )
        assert result.status_code == 200, result.text
        assert result.json()["search_scope"] == "本景点圈在职CM/TR"
        assert any(row["employee_no"] == "CMTEST01" for row in result.json()["items"])
