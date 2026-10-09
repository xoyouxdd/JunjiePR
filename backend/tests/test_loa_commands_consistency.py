"""Exercise both LOA write screens with isolated memory and SQLite race DBs."""
from __future__ import annotations

from datetime import date
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, Request
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.routers import employees as routes
from app.v2_auth import V2User
from app.v2_database import Base
from app.v2_models import (
    AttendanceMonthlyScore, AttendanceRule, Attraction, AuditLog, Employee,
    EmployeeActingDuty, EmployeeLOAPeriod, EmployeeMonthOrganizationSnapshot,
    EmployeeRoleAssignment, MonthClosure, Role, UserAccount, WorkGroup,
)
from app.v2_services import recalculate_attendance


TODAY = date(2026, 10, 9)


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(routes, "date", FixedDate)
    monkeypatch.setattr(routes, "loa_today", lambda: TODAY)
    monkeypatch.setattr(routes, "invalidate_data_caches", lambda: None)
    with session_factory() as db:
        circle = Attraction(name="测试景点圈", employee_circle=True)
        other_circle = Attraction(name="调入景点圈", employee_circle=True)
        cm = Role(code="CM", name="CM", rank=10, attendance_eligible=True)
        gsm = Role(code="GSM", name="GSM", rank=30)
        db.add_all([circle, other_circle, cm, gsm])
        db.flush()
        employee = Employee(employee_no="CM-LOA", name="LOA测试", attraction_id=circle.id, hired_on="2020-01-01")
        actor = Employee(employee_no="GSM-LOA", name="登记员", attraction_id=circle.id)
        db.add_all([employee, actor])
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=cm.id, starts_on="2020-01-01"))
        db.add(AttendanceRule(effective_date="2020-01-01"))
        db.commit()
        user = V2User(employee=actor, account=SimpleNamespace(), role=gsm, permissions={"SYSTEM_ADMIN", "HR_MANAGE", "LOA_REGISTER"})
        request = Request({"type": "http", "client": ("127.0.0.1", 12345), "headers": []})
        yield SimpleNamespace(db=db, employee=employee, actor=actor, user=user, request=request, other_circle=other_circle)
    engine.dispose()


def register(context, screen: str, starts_on: str):
    if screen == "dedicated":
        return routes.create_loa_period({"employee_id": context.employee.id, "starts_on": starts_on}, context.request, context.db, context.user)
    return routes.update_employee(context.employee.id, {"employment_status": "loa", "loa_start_date": starts_on}, context.request, context.db, context.user)


def seed_period(context, starts_on: str, ends_on: str | None = None, status: str = "active"):
    period = EmployeeLOAPeriod(
        employee_id=context.employee.id, starts_on=starts_on, ends_on=ends_on,
        status=status, created_by=context.actor.id, created_by_name=context.actor.name,
    )
    context.db.add(period)
    context.db.commit()
    return period


def seed_scores(context, *months: str):
    for month in months:
        recalculate_attendance(context.db, context.employee, month)
    context.db.commit()


def score(context, month: str):
    return context.db.query(AttendanceMonthlyScore).filter_by(employee_id=context.employee.id, attendance_month=month).one()


def close_month(context, month: str, attraction_id: int | None = None):
    context.db.add(MonthClosure(closure_month=month, attraction_id=attraction_id or context.employee.attraction_id, status="closed"))
    context.db.commit()


@pytest.mark.parametrize("screen", ["dedicated", "hr"])
@pytest.mark.parametrize("existing", [("2026-10-01", "2026-10-15"), ("2026-10-20", "2026-10-25")])
def test_open_loa_rejects_completed_overlap_including_future_period(context, screen, existing):
    seed_period(context, *existing, status="ended")
    with pytest.raises(HTTPException) as rejected:
        register(context, screen, "2026-10-10")
    assert rejected.value.status_code == 409
    assert context.db.query(EmployeeLOAPeriod).count() == 1
    assert context.db.query(AuditLog).count() == 0


@pytest.mark.parametrize("screen", ["dedicated", "hr"])
def test_open_loa_recalculates_every_materialized_month(context, screen):
    seed_scores(context, "2026-09", "2026-10", "2026-11")
    assert all(score(context, month).final_score == Decimal("12.00") for month in ("2026-09", "2026-10", "2026-11"))
    register(context, screen, "2026-09-10")
    assert all(not score(context, month).eligible and score(context, month).final_score == 0 for month in ("2026-09", "2026-10", "2026-11"))


@pytest.mark.parametrize("screen", ["dedicated", "hr"])
def test_open_loa_blocks_on_materialized_later_closed_month_without_partial_write(context, screen):
    seed_scores(context, "2026-10", "2026-11")
    close_month(context, "2026-11")
    with pytest.raises(HTTPException) as rejected:
        register(context, screen, "2026-10-10")
    assert rejected.value.status_code == 423
    assert rejected.value.detail["month"] == "2026-11"
    assert context.db.query(EmployeeLOAPeriod).count() == 0
    assert context.db.query(AuditLog).count() == 0
    assert score(context, "2026-11").final_score == Decimal("12.00")


@pytest.mark.parametrize("screen", ["dedicated", "hr"])
def test_ending_loa_allows_unchanged_closed_month_and_restores_later_scores(context, screen):
    seed_scores(context, "2026-09", "2026-10", "2026-11")
    register(context, "dedicated", "2026-09-01")
    close_month(context, "2026-09")
    close_month(context, "2026-10")
    if screen == "dedicated":
        result = routes.create_loa_period({"employee_id": context.employee.id, "starts_on": "2026-09-01", "ends_on": "2026-10-08"}, context.request, context.db, context.user)
        assert result["completed"] is True
        assert result["excluded_months"] == ["2026-09", "2026-10"]
    else:
        # This matches hrEditEmployee's complete body, including unchanged
        # identity/organization/account values and its string circle ID.
        body = {
            "role_code": "CM", "attraction_id": str(context.employee.attraction_id),
            "employment_status": "active", "loa_start_date": "2026-09-01",
            "account_enabled": False, "group_id": None, "reason": "HR员工管理编辑",
        }
        assert routes.update_employee(context.employee.id, body, context.request, context.db, context.user)["ok"] is True
    assert score(context, "2026-09").eligible is False
    assert score(context, "2026-10").eligible is False
    assert score(context, "2026-11").final_score == Decimal("12.00")
    period = context.db.query(EmployeeLOAPeriod).one()
    assert period.ends_on == "2026-10-08"
    assert period.ended_by == context.actor.id


@pytest.mark.parametrize("screen", ["dedicated", "hr"])
def test_ending_loa_blocks_closed_restored_month(context, screen):
    seed_scores(context, "2026-10", "2026-11")
    register(context, "dedicated", "2026-10-01")
    close_month(context, "2026-11")
    audits_before = context.db.query(AuditLog).count()
    with pytest.raises(HTTPException) as rejected:
        if screen == "dedicated":
            routes.create_loa_period({"employee_id": context.employee.id, "starts_on": "2026-10-01", "ends_on": "2026-10-08"}, context.request, context.db, context.user)
        else:
            routes.update_employee(context.employee.id, {"employment_status": "active", "name": "不应保存"}, context.request, context.db, context.user)
    assert rejected.value.status_code == 423
    assert rejected.value.detail["month"] == "2026-11"
    assert context.db.query(EmployeeLOAPeriod).one().ends_on is None
    assert context.db.query(AuditLog).count() == audits_before
    assert context.db.get(Employee, context.employee.id).name == "LOA测试"
    assert score(context, "2026-11").eligible is False


def test_hr_ending_a_future_loa_recalculates_its_entire_horizon(context):
    seed_scores(context, "2026-10", "2026-11")
    register(context, "hr", "2026-10-10")
    assert score(context, "2026-11").eligible is False
    routes.update_employee(context.employee.id, {"employment_status": "active"}, context.request, context.db, context.user)
    period = context.db.query(EmployeeLOAPeriod).one()
    assert period.status == "cancelled"
    assert period.ends_on == "2026-10-10"
    assert all(score(context, month).final_score == Decimal("12.00") for month in ("2026-10", "2026-11"))


def test_cancel_only_gates_months_that_lose_combined_exclusion(context):
    first = seed_period(context, "2026-10-01", "2026-10-05")
    seed_period(context, "2026-10-20", "2026-10-25", status="ended")
    seed_scores(context, "2026-10")
    close_month(context, "2026-10")
    assert routes.cancel_loa_period(first.id, {"reason": "登记更正"}, context.request, context.db, context.user)["ok"] is True
    assert score(context, "2026-10").eligible is False
    remaining = context.db.query(EmployeeLOAPeriod).filter(EmployeeLOAPeriod.status != "cancelled").one()
    with pytest.raises(HTTPException) as rejected:
        routes.cancel_loa_period(remaining.id, {"reason": "登记更正"}, context.request, context.db, context.user)
    assert rejected.value.status_code == 423
    assert context.db.get(EmployeeLOAPeriod, remaining.id).status == "ended"


def test_hr_failure_after_loa_flush_rolls_back_employee_period_audit_and_scores(context, monkeypatch):
    seed_scores(context, "2026-09", "2026-10", "2026-11")
    original_audit = routes.write_audit

    def fail_final_audit(db, actor, action, *args, **kwargs):
        if action == "修改员工":
            raise RuntimeError("injected audit failure")
        return original_audit(db, actor, action, *args, **kwargs)

    monkeypatch.setattr(routes, "write_audit", fail_final_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        routes.update_employee(context.employee.id, {"employment_status": "loa", "loa_start_date": "2026-09-10", "name": "不应保存"}, context.request, context.db, context.user)
    # Continuing and committing the same session must not persist a failed edit.
    context.db.commit()
    assert context.db.get(Employee, context.employee.id).name == "LOA测试"
    assert context.db.query(EmployeeLOAPeriod).count() == 0
    assert context.db.query(AuditLog).count() == 0
    assert all(score(context, month).final_score == Decimal("12.00") for month in ("2026-09", "2026-10", "2026-11"))


def test_hr_loa_and_circle_move_gate_original_circle_for_historical_months(context):
    close_month(context, "2026-09")
    with pytest.raises(HTTPException) as rejected:
        routes.update_employee(context.employee.id, {"employment_status": "loa", "loa_start_date": "2026-09-10", "attraction_id": context.other_circle.id}, context.request, context.db, context.user)
    assert rejected.value.status_code == 423
    assert rejected.value.detail["month"] == "2026-09"
    assert context.db.get(Employee, context.employee.id).attraction_id != context.other_circle.id
    assert context.db.query(EmployeeLOAPeriod).count() == 0


@pytest.mark.parametrize("screen", ["dedicated", "hr"])
@pytest.mark.parametrize("action", ["create", "close", "cancel"])
def test_loa_mutations_gate_historical_snapshot_circle(context, screen, action):
    original_circle = context.employee.attraction_id
    seed_scores(context, "2026-09", "2026-10")
    period = None
    if action == "close":
        # Closing before September restores the historical month in circle A.
        period = seed_period(context, "2026-08-01")
        seed_scores(context, "2026-09", "2026-10")
    elif action == "cancel":
        period = seed_period(context, "2026-09-01", "2026-09-10")
        seed_scores(context, "2026-09")
    context.db.add(EmployeeMonthOrganizationSnapshot(employee_id=context.employee.id, score_month="2026-09", attraction_id=original_circle))
    context.employee.attraction_id = context.other_circle.id
    context.db.commit()
    close_month(context, "2026-09", attraction_id=original_circle)
    before_score = score(context, "2026-09").final_score
    with pytest.raises(HTTPException) as rejected:
        if action == "create":
            register(context, screen, "2026-09-10")
        elif action == "close" and screen == "dedicated":
            routes.create_loa_period({"employee_id": context.employee.id, "starts_on": "2026-08-01", "ends_on": "2026-08-31"}, context.request, context.db, context.user)
        elif action == "close":
            # HR's yesterday is moved to August so its close restores September.
            old_date = routes.date
            try:
                class AugustEndDate(FixedDate):
                    @classmethod
                    def today(cls):
                        return cls(2026, 9, 1)
                routes.date = AugustEndDate
                routes.update_employee(context.employee.id, {"employment_status": "active"}, context.request, context.db, context.user)
            finally:
                routes.date = old_date
        else:
            routes.cancel_loa_period(period.id, {"reason": "登记错误"}, context.request, context.db, context.user)
    assert rejected.value.status_code == 423
    assert rejected.value.detail["month"] == "2026-09"
    assert rejected.value.detail["attraction_id"] == original_circle
    assert score(context, "2026-09").final_score == before_score
    if action == "create":
        assert context.db.query(EmployeeLOAPeriod).count() == 0
    elif action == "close":
        assert context.db.get(EmployeeLOAPeriod, period.id).ends_on is None
    else:
        assert context.db.get(EmployeeLOAPeriod, period.id).status != "cancelled"


@pytest.mark.parametrize("change", ["role", "circle", "group", "account", "activity", "duty_end"])
def test_hr_real_identity_organization_account_and_activity_changes_still_gate_current_month(context, change):
    body = {"role_code": "CM", "attraction_id": str(context.employee.attraction_id), "employment_status": "active", "account_enabled": True, "group_id": None}
    account = UserAccount(employee_id=context.employee.id, login_account=context.employee.employee_no, password_hash="unused-test-hash", enabled=True)
    context.db.add(account)
    if change == "role":
        context.db.add(Role(code="TR", name="TR", rank=10, attendance_eligible=True))
        body["role_code"] = "TR"
    elif change == "circle":
        body["attraction_id"] = str(context.other_circle.id)
    elif change == "group":
        group = WorkGroup(name="目标小组", attraction_id=context.employee.attraction_id)
        context.db.add(group)
        context.db.flush()
        body["group_id"] = str(group.id)
    elif change == "account":
        body["account_enabled"] = False
    elif change == "activity":
        body["employment_status"] = "terminated"
    elif change == "duty_end":
        duty_role = Role(code="TA_SUPERVISOR", name="TA主管", rank=20)
        context.db.add(duty_role)
        context.db.flush()
        context.db.add(EmployeeActingDuty(employee_id=context.employee.id, role_id=duty_role.id, starts_on="2026-10-01", ends_on="2026-10-15", created_by=context.actor.id))
        body["role_code"] = "TA_SUPERVISOR"
        body["role_ends_on"] = "2026-10-20"
    context.db.commit()
    close_month(context, "2026-10")
    with pytest.raises(HTTPException) as rejected:
        routes.update_employee(context.employee.id, body, context.request, context.db, context.user)
    assert rejected.value.status_code == 423
    assert rejected.value.detail["month"] == "2026-10"
    assert context.db.get(UserAccount, account.id).enabled is True
    assert context.db.get(Employee, context.employee.id).is_active is True


@pytest.mark.parametrize("first_screen,second_screen", [("dedicated", "dedicated"), ("dedicated", "hr"), ("hr", "dedicated"), ("hr", "hr")])
def test_concurrent_loa_routes_reserve_writer_before_conflict_reads(context, monkeypatch, race_db_path, first_screen, second_screen):
    # Distinct real SQLite connections are required to exercise busy waiting.
    engine = create_engine(f"sqlite:///{race_db_path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 5})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        circle = Attraction(name="并发圈", employee_circle=True)
        cm = Role(code="CM", name="CM", rank=10, attendance_eligible=True)
        gsm = Role(code="GSM", name="GSM", rank=30)
        db.add_all([circle, cm, gsm])
        db.flush()
        employee = Employee(employee_no="CM-RACE", name="并发员工", attraction_id=circle.id, hired_on="2020-01-01")
        actor = Employee(employee_no="GSM-RACE", name="并发登记员", attraction_id=circle.id)
        db.add_all([employee, actor])
        db.flush()
        employee_id, actor_id, gsm_id = employee.id, actor.id, gsm.id
        db.add(EmployeeRoleAssignment(employee_id=employee_id, role_id=cm.id, starts_on="2020-01-01"))
        db.add(AttendanceRule(effective_date="2020-01-01"))
        db.commit()
    first_checked, release_first, second_writer_attempt = Event(), Event(), Event()
    original_check = routes.loa_commands._ensure_no_overlap

    def pause_first_check(*args, **kwargs):
        original_check(*args, **kwargs)
        if not first_checked.is_set():
            first_checked.set()
            assert release_first.wait(5), "first writer was not released"

    monkeypatch.setattr(routes.loa_commands, "_ensure_no_overlap", pause_first_check)

    @event.listens_for(engine, "before_cursor_execute")
    def writer_attempt(connection, cursor, statement, parameters, execution_context, executemany):
        if statement == "BEGIN IMMEDIATE" and first_checked.is_set():
            second_writer_attempt.set()

    def run(screen, first):
        with sessions() as db:
            actor = db.get(Employee, actor_id)
            user = V2User(employee=actor, account=SimpleNamespace(), role=db.get(Role, gsm_id), permissions={"SYSTEM_ADMIN", "HR_MANAGE", "LOA_REGISTER"})
            starts_on = "2026-10-01" if first else "2026-10-10"
            try:
                if screen == "dedicated":
                    payload = {"employee_id": employee_id, "starts_on": starts_on}
                    if first:
                        payload["ends_on"] = "2026-10-20"
                    return routes.create_loa_period(payload, context.request, db, user)
                return routes.update_employee(employee_id, {"employment_status": "loa", "loa_start_date": starts_on}, context.request, db, user)
            except HTTPException as exc:
                return exc.status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run, first_screen, True)
            assert first_checked.wait(5)
            second = executor.submit(run, second_screen, False)
            assert second_writer_attempt.wait(5)
            # The second thread has reached BEGIN IMMEDIATE but cannot read
            # stale LOA state while the first has the write reservation.
            assert not second.done()
            release_first.set()
            assert first.result(timeout=5)["ok"] is True
            result = second.result(timeout=5)
            if first_screen == "hr" and second_screen == "hr":
                assert result["ok"] is True  # An already-open HR LOA is unchanged.
            else:
                assert result == 409
        with sessions() as db:
            assert db.query(EmployeeLOAPeriod).count() == 1
    finally:
        release_first.set()
        engine.dispose()


@pytest.fixture
def race_db_path():
    # Keep task-owned files in the project and avoid a shared system TEMP root.
    base = Path(__file__).resolve().parents[2] / ".handover_review"
    base.mkdir(exist_ok=True)
    path = base / f"loa-race-{uuid4().hex}.db"
    try:
        yield path
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            path.with_name(f"{path.name}{suffix}").unlink(missing_ok=True)
