from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import tempfile


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-loa-month-recalc-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import employees as employees_router  # noqa: E402
from app.v2_database import ROLE_PERMISSION_CODES, SessionLocal  # noqa: E402
from app.v2_models import AttendanceMonthlyScore, Employee, EmployeeLOAPeriod, MonthClosure  # noqa: E402
from app.v2_services import recalculate_attendance  # noqa: E402

# AM now holds this permission by default; avoid adding a duplicate role-permission row.
if "LOA_REGISTER" not in ROLE_PERMISSION_CODES["AM"]:
    ROLE_PERMISSION_CODES["AM"] = (*ROLE_PERMISSION_CODES["AM"], "LOA_REGISTER")


def login(client: TestClient, employee_no: str) -> None:
    response = client.post("/api/login", json={"employee_no": employee_no, "password": "1234"})
    assert response.status_code == 200, response.text


def employee_by_no(employee_no: str) -> Employee:
    with SessionLocal() as db:
        employee = db.query(Employee).filter(Employee.employee_no == employee_no).one()
        db.expunge(employee)
        return employee


def seed_attendance(employee_no: str, months: list[str]) -> None:
    with SessionLocal() as db:
        employee = db.query(Employee).filter(Employee.employee_no == employee_no).one()
        for month in months:
            recalculate_attendance(db, employee, month)
        db.commit()


def attendance(employee_id: int, month: str) -> AttendanceMonthlyScore | None:
    with SessionLocal() as db:
        row = (
            db.query(AttendanceMonthlyScore)
            .filter(AttendanceMonthlyScore.employee_id == employee_id, AttendanceMonthlyScore.attendance_month == month)
            .first()
        )
        if row:
            db.expunge(row)
        return row


def assert_scored(employee_id: int, month: str) -> None:
    row = attendance(employee_id, month)
    assert row is not None, month
    assert row.eligible is True, month
    assert row.final_score > 0, month


def assert_excluded(employee_id: int, month: str) -> None:
    row = attendance(employee_id, month)
    assert row is not None, month
    assert row.eligible is False, month
    assert row.final_score == 0, month


def close_month(month: str, attraction_id: int | None) -> int:
    with SessionLocal() as db:
        closure = MonthClosure(closure_month=month, attraction_id=attraction_id, status="closed", closed_by_name="x")
        db.add(closure)
        db.commit()
        return closure.id


def reopen_month(closure_id: int) -> None:
    with SessionLocal() as db:
        db.get(MonthClosure, closure_id).status = "open"
        db.commit()


def test_open_loa_recalculates_later_months_and_completion_restores_them() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        employee = employee_by_no("CMTEST01")
        months = ["2091-08", "2091-09", "2091-10"]
        seed_attendance("CMTEST01", months)
        for month in months:
            assert_scored(employee.id, month)

        # Registering an open LOA must also recalculate materialized months after the start month.
        entered = client.post("/api/loa-periods", json={"employee_id": employee.id, "starts_on": "2091-08-10"})
        assert entered.status_code == 200, entered.text
        assert entered.json()["completed"] is False
        assert entered.json()["excluded_months"] == months
        for month in months:
            assert_excluded(employee.id, month)
        period_id = entered.json()["record"]["id"]

        # The closed start month is unaffected by the completion, so it must not block it.
        closure_id = close_month("2091-08", employee.attraction_id)
        completed = client.post(
            "/api/loa-periods",
            json={"employee_id": employee.id, "starts_on": "2091-08-10", "ends_on": "2091-08-31"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["completed"] is True
        assert completed.json()["excluded_months"] == ["2091-08"]
        assert_excluded(employee.id, "2091-08")
        assert_scored(employee.id, "2091-09")
        assert_scored(employee.id, "2091-10")

        with SessionLocal() as db:
            gsm_id = db.query(Employee.id).filter(Employee.employee_no == "GSMTEST01").scalar()
            period = db.get(EmployeeLOAPeriod, period_id)
            assert period.ended_by == gsm_id
            ended_by_name = period.ended_by_name
            ended_at = period.ended_at
        assert ended_by_name

        # Cancelling touches 2091-08, which is still closed.
        blocked = client.request("DELETE", f"/api/loa-periods/{period_id}", json={"reason": "登记错误"})
        assert blocked.status_code == 423, blocked.text
        reopen_month(closure_id)

        login(client, "TAGSMTEST01")
        cancelled = client.request("DELETE", f"/api/loa-periods/{period_id}", json={"reason": "登记错误"})
        assert cancelled.status_code == 200, cancelled.text
        with SessionLocal() as db:
            period = db.get(EmployeeLOAPeriod, period_id)
            assert period.status == "cancelled"
            # The original end registrar survives; the canceller lives in the audit log.
            assert period.ended_by == gsm_id
            assert period.ended_by_name == ended_by_name
            assert period.ended_at == ended_at
            assert "撤销原因：登记错误" in (period.note or "")
        for month in months:
            assert_scored(employee.id, month)


def test_completion_rejected_when_restored_month_is_closed() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        employee = employee_by_no("CMTEST02")
        months = ["2092-08", "2092-09", "2092-10"]
        seed_attendance("CMTEST02", months)
        entered = client.post("/api/loa-periods", json={"employee_id": employee.id, "starts_on": "2092-08-10"})
        assert entered.status_code == 200, entered.text
        close_month("2092-10", employee.attraction_id)

        # Ending in August would restore September and October; October is closed.
        rejected = client.post(
            "/api/loa-periods",
            json={"employee_id": employee.id, "starts_on": "2092-08-10", "ends_on": "2092-08-31"},
        )
        assert rejected.status_code == 423, rejected.text
        assert rejected.json()["detail"]["month"] == "2092-10"
        with SessionLocal() as db:
            period = db.get(EmployeeLOAPeriod, entered.json()["record"]["id"])
            assert period.ends_on is None
            assert period.ended_by is None
        for month in months:
            assert_excluded(employee.id, month)

        # Ending inside the closed month changes no month's result, so it is allowed.
        completed = client.post(
            "/api/loa-periods",
            json={"employee_id": employee.id, "starts_on": "2092-08-10", "ends_on": "2092-10-05"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["excluded_months"] == months

        # The end date can still not precede the start date.
        second = client.post("/api/loa-periods", json={"employee_id": employee.id, "starts_on": "2092-12-01"})
        assert second.status_code == 200, second.text
        backwards = client.post(
            "/api/loa-periods",
            json={"employee_id": employee.id, "starts_on": "2092-12-01", "ends_on": "2092-11-30"},
        )
        assert backwards.status_code == 400, backwards.text


def test_open_loa_horizon_follows_today_and_closed_later_months(monkeypatch) -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        employee = employee_by_no("TRTEST01")
        monkeypatch.setattr(employees_router, "loa_today", lambda: date(2093, 12, 15))
        closure_id = close_month("2093-12", employee.attraction_id)
        rejected = client.post("/api/loa-periods", json={"employee_id": employee.id, "starts_on": "2093-10-01"})
        assert rejected.status_code == 423, rejected.text
        assert rejected.json()["detail"]["month"] == "2093-12"
        reopen_month(closure_id)

        entered = client.post("/api/loa-periods", json={"employee_id": employee.id, "starts_on": "2093-10-01"})
        assert entered.status_code == 200, entered.text
        assert entered.json()["excluded_months"] == ["2093-10", "2093-11", "2093-12"]
        for month in ("2093-10", "2093-11", "2093-12"):
            assert_excluded(employee.id, month)


def test_cancel_loa_requires_registrar_role() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        employee = employee_by_no("CMTEST01")
        entered = client.post(
            "/api/loa-periods",
            json={"employee_id": employee.id, "starts_on": "2094-03-01", "ends_on": "2094-03-20"},
        )
        assert entered.status_code == 200, entered.text
        period_id = entered.json()["record"]["id"]

        login(client, "CMTEST02")
        no_permission = client.request("DELETE", f"/api/loa-periods/{period_id}", json={"reason": "越权"})
        assert no_permission.status_code == 403, no_permission.text

        login(client, "AMTEST01")
        registrar = client.request("DELETE", f"/api/loa-periods/{period_id}", json={"reason": "登记更正"})
        assert registrar.status_code == 200, registrar.text

        with SessionLocal() as db:
            assert db.get(EmployeeLOAPeriod, period_id).status == "cancelled"
