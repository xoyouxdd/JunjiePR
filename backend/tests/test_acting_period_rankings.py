from __future__ import annotations

import os
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-acting-rankings-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Employee  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    client.post("/api/logout")
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def employee_id(employee_no: str) -> int:
    with SessionLocal() as db:
        return db.query(Employee).filter_by(employee_no=employee_no).one().id


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return stream.getvalue()


def recognize(client: TestClient, *, recognizer_no: str, key: str, target_no: str | None = None):
    options = client.get("/api/options").json()
    venue_id = next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")
    circle_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
    type_id = next(row["id"] for row in options["recognition_types"] if row["code"] == "COURTESY")
    today = date.today().isoformat()
    recognizers = client.get("/api/recognizers", params={"attraction_id": circle_id, "recognition_date": today}).json()
    recognizer = next(row for row in recognizers if row.get("employee_no") == recognizer_no)
    data = {
        "recognition_date": today,
        "occurred_attraction_id": str(venue_id),
        "recognition_type_id": str(type_id),
        "recognizer_employee_id": str(recognizer["id"]),
        "content": "排行口径测试",
        "idempotency_key": key,
        "same_day_duplicate_confirmed": "true",
    }
    files = None
    if target_no:
        data["employee_id"] = str(employee_id(target_no))
    else:
        files = {"image": ("proof.png", png_bytes(), "image/png")}
    response = client.post("/api/recognitions", data=data, files=files)
    assert response.status_code == 200, response.text
    return response.json()["record"]


def issuing_counts(client: TestClient) -> dict[str, int]:
    start = date.today().replace(day=1).isoformat()
    data = client.get("/api/pr-rankings", params={"start_date": start, "end_date": date.today().isoformat(), "category": "leader", "page_size": 100}).json()
    return {row["employee_no"]: row["count"] for row in data["rows"]}


def test_issuing_ranking_counts_role_used_and_keeps_former_ta_records() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        recognize(client, recognizer_no="TATEST01", key="ta-issue", target_no="CMTEST01")
        own = recognize(client, recognizer_no="SUPTEST01", key="ta-own")
        login(client, "SUPTEST01")
        assert client.post(f"/api/reviews/{own['id']}", json={"action": "confirm"}).status_code == 200

        login(client, "GSMTEST01")
        counts = issuing_counts(client)
        # The TA's own submission is not something they issued.
        assert counts["TATEST01"] == 1
        assert counts["SUPTEST01"] == 1

        login(client, "HR01", "HR123")
        ended = client.put(f"/api/hr/employees/{employee_id('TATEST01')}", json={"role_code": "CM", "reason": "结束代理"})
        assert ended.status_code == 400  # still leads a group with members
        login(client, "GSMTEST01")
        assert issuing_counts(client)["TATEST01"] == 1


def test_monthly_statistics_note_the_acting_period_score() -> None:
    month = date.today().strftime("%Y-%m")
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        recognize(client, recognizer_no="GSMTEST01", key="gsm-credit-ta", target_no="TATEST01")
        data = client.get("/api/statistics", params={"month": month}).json()
        node = next(node for node in data["hierarchy"] if node.get("employee_no") == "TATEST01")
        assert node["role_name"] == "CM · 代理TA主管"
        assert node["acting_note"].startswith("含 TA 主管期间（")
        assert "得分" in node["acting_note"]
        start = date.today().replace(day=1).isoformat()
        overall = client.get("/api/pr-rankings", params={"start_date": start, "end_date": date.today().isoformat()}).json()
        row = next(row for row in overall["rows"] if row["employee_no"] == "TATEST01")
        # The ranking range ends today while the monthly note covers the month.
        assert row["acting_note"].split("得分")[1] == node["acting_note"].split("得分")[1]
        assert row["acting_note"].endswith(f"{date.today().isoformat()[5:]}）得分 1.50")
