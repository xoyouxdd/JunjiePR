from __future__ import annotations

import os
import tempfile
from calendar import monthrange
from datetime import date
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-supervisor-statistics-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Employee  # noqa: E402
from app.v2_services import SUPERVISOR_SCORING_START_MONTH, acting_duty_summary  # noqa: E402


def login(client: TestClient, account: str) -> None:
    client.post("/api/logout")
    response = client.post("/api/login", json={"employee_no": account, "password": "1234"})
    assert response.status_code == 200, response.text


def test_supervisor_attendance_starts_with_the_supervisor_scoring_month() -> None:
    month = date.today().strftime("%Y-%m")
    assert month >= SUPERVISOR_SCORING_START_MONTH
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        current = client.get("/api/dashboard", params={"month": month}).json()
        assert current["attendance_score"] == 12.0
        earlier = client.get("/api/dashboard", params={"month": "2026-09"}).json()
        assert earlier["attendance_score"] == 0


def test_statistics_split_frontline_and_supervisor_pages() -> None:
    month = date.today().strftime("%Y-%m")
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        frontline = client.get("/api/statistics", params={"month": month}).json()
        frontline_nos = {node.get("employee_no") for node in frontline["hierarchy"] if node.get("node_type") == "employee"}
        assert {"CMTEST01", "TATEST01"}.issubset(frontline_nos)
        assert not {"SUPTEST01", "TAGSMTEST01"} & frontline_nos
        assert frontline["summary"]["employee_count"] == len(frontline_nos)

        supervisors = client.get("/api/statistics", params={"month": month, "title": "SUPERVISOR"}).json()
        supervisor_nodes = [node for node in supervisors["hierarchy"] if node.get("node_type") == "employee"]
        assert {node["employee_no"] for node in supervisor_nodes} == {"SUPTEST01", "TAGSMTEST01"}
        assert any(node["role_name"] == "主管 · 代理TA GSM" for node in supervisor_nodes)
        trend = client.get("/api/statistics/trend", params={"month": month, "title": "SUPERVISOR", "months": 2})
        assert trend.status_code == 200, trend.text


def test_pr_ranking_has_a_supervisor_population() -> None:
    start = date.today().replace(day=1).isoformat()
    end = date.today().isoformat()
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        supervisors = client.get("/api/pr-rankings", params={"start_date": start, "end_date": end, "population": "supervisor"}).json()
        assert supervisors["population"] == "supervisor"
        assert {row["employee_no"] for row in supervisors["rows"]} == {"SUPTEST01", "TAGSMTEST01"}
        frontline = client.get("/api/pr-rankings", params={"start_date": start, "end_date": end}).json()
        assert "TATEST01" in {row["employee_no"] for row in frontline["rows"]}
        assert not {"SUPTEST01", "TAGSMTEST01"} & {row["employee_no"] for row in frontline["rows"]}


def test_acting_duty_summary_counts_days_inside_the_month() -> None:
    today = date.today()
    month = today.strftime("%Y-%m")
    with TestClient(app):
        with SessionLocal() as db:
            ta = db.query(Employee).filter_by(employee_no="TATEST01").one()
            code, days = acting_duty_summary(db, ta.id, month)
    assert code == "TA_SUPERVISOR"
    assert days == monthrange(today.year, today.month)[1] - today.day + 1
