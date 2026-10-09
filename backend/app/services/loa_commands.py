"""Shared LOA mutations; the caller owns permissions, audit and transaction.

Both registration screens use the same date conflict and score-month policy.
Month gates are supplied by the HTTP layer so this service does not import a
router.  No command commits: employee edits and LOA changes remain atomic.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.v2_models import AttendanceMonthlyScore, Employee, EmployeeLOAPeriod, EmployeeMonthOrganizationSnapshot
from app.services.attendance import recalculate_attendance


MonthGate = Callable[[Session, str, int | None, str], None]


@dataclass(frozen=True)
class LOAChange:
    period: EmployeeLOAPeriod
    excluded_months: tuple[str, ...]
    changed_months: tuple[str, ...]


def excluded_months(starts_on: date, ends_on: date | None, horizon: date) -> set[str]:
    finish = ends_on or horizon
    year, month = starts_on.year, starts_on.month
    result = set()
    while (year, month) <= (finish.year, finish.month):
        result.add(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def open_horizon(db: Session, employee_id: int, starts_on: date, today: date, *extra_dates: date) -> date:
    """Include today, the requested interval and every stored attendance month.

    The LOA remains open beyond this finite recalculation horizon; score reads
    still exclude later months directly from the period's unbounded end date.
    """
    latest_month = (
        db.query(func.max(AttendanceMonthlyScore.attendance_month))
        .filter(AttendanceMonthlyScore.employee_id == employee_id)
        .scalar()
    )
    candidates = [starts_on, today, *extra_dates]
    if latest_month:
        candidates.append(date.fromisoformat(f"{latest_month}-01"))
    return max(candidates).replace(day=1)


def current_open_period(db: Session, employee_id: int) -> EmployeeLOAPeriod | None:
    return (
        db.query(EmployeeLOAPeriod)
        .filter(
            EmployeeLOAPeriod.employee_id == employee_id,
            EmployeeLOAPeriod.status == "active",
            EmployeeLOAPeriod.ends_on.is_(None),
        )
        .order_by(EmployeeLOAPeriod.starts_on.desc(), EmployeeLOAPeriod.id.desc())
        .first()
    )


def _ensure_no_overlap(
    db: Session, employee_id: int, starts_on: date, ends_on: date | None, ignore_id: int | None = None,
) -> None:
    query = db.query(EmployeeLOAPeriod).filter(
        EmployeeLOAPeriod.employee_id == employee_id,
        EmployeeLOAPeriod.status != "cancelled",
        or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= starts_on.isoformat()),
    )
    # None is an unbounded future, not the start date.  This also catches a
    # previously completed period that starts after the new open LOA.
    if ends_on is not None:
        query = query.filter(EmployeeLOAPeriod.starts_on <= ends_on.isoformat())
    if ignore_id is not None:
        query = query.filter(EmployeeLOAPeriod.id != ignore_id)
    if query.first():
        raise HTTPException(409, "该员工在所选日期内已有LOA记录，请先修改或撤销原记录")


def _changed_months(
    db: Session, employee_id: int, horizon: date, before: set[str], after: set[str], ignore_id: int | None = None,
) -> tuple[str, ...]:
    # Two disjoint date ranges can still exclude the same score month.  Only
    # a change to the employee's combined exclusion requires reopening it.
    query = db.query(EmployeeLOAPeriod).filter(
        EmployeeLOAPeriod.employee_id == employee_id,
        EmployeeLOAPeriod.status != "cancelled",
    )
    if ignore_id is not None:
        query = query.filter(EmployeeLOAPeriod.id != ignore_id)
    others: set[str] = set()
    for period in query.all():
        others |= excluded_months(
            date.fromisoformat(period.starts_on),
            date.fromisoformat(period.ends_on) if period.ends_on else None,
            horizon,
        )
    return tuple(sorted((before | others) ^ (after | others)))


def _check_months(db: Session, employee: Employee, months: tuple[str, ...], operation: str, gate: MonthGate) -> None:
    snapshot_scopes = {
        row.score_month: row.attraction_id
        for row in db.query(EmployeeMonthOrganizationSnapshot).filter(
            EmployeeMonthOrganizationSnapshot.employee_id == employee.id,
            EmployeeMonthOrganizationSnapshot.score_month.in_(months),
        ).all()
    } if months else {}
    for month in months:
        gate(db, month, employee.attraction_id, operation)
        if month in snapshot_scopes and snapshot_scopes[month] != employee.attraction_id:
            gate(db, month, snapshot_scopes[month], operation)


def create_period(
    db: Session, employee: Employee, starts_on: date, ends_on: date | None, *,
    actor_id: int, actor_name: str, note: str | None, today: date, gate: MonthGate, operation: str = "登记LOA",
) -> LOAChange:
    if ends_on is not None and ends_on < starts_on:
        raise HTTPException(400, "LOA结束日期不能早于进入日期")
    _ensure_no_overlap(db, employee.id, starts_on, ends_on)
    horizon = open_horizon(db, employee.id, starts_on, today, ends_on or starts_on)
    excluded = excluded_months(starts_on, ends_on, horizon)
    changed = _changed_months(db, employee.id, horizon, set(), excluded)
    _check_months(db, employee, changed, operation, gate)
    period = EmployeeLOAPeriod(
        employee_id=employee.id, starts_on=starts_on.isoformat(), ends_on=ends_on.isoformat() if ends_on else None,
        status="active", note=note, created_by=actor_id, created_by_name=actor_name,
    )
    db.add(period)
    return LOAChange(period, tuple(sorted(excluded)), changed)


def close_period(
    db: Session, employee: Employee, period: EmployeeLOAPeriod, ends_on: date, *,
    actor_id: int, actor_name: str, today: date, gate: MonthGate, operation: str = "登记LOA结束",
    note: str | None = None, status: str = "active", cancel_if_before_start: bool = False,
) -> LOAChange:
    starts_on = date.fromisoformat(period.starts_on)
    cancelled = ends_on < starts_on and cancel_if_before_start
    if ends_on < starts_on and not cancelled:
        raise HTTPException(400, "LOA结束日期不能早于进入日期")
    if not cancelled:
        _ensure_no_overlap(db, employee.id, starts_on, ends_on, period.id)
    horizon = open_horizon(db, employee.id, starts_on, today, ends_on)
    before = excluded_months(starts_on, date.fromisoformat(period.ends_on) if period.ends_on else None, horizon)
    after = set() if cancelled else excluded_months(starts_on, ends_on, horizon)
    changed = _changed_months(db, employee.id, horizon, before, after, period.id)
    _check_months(db, employee, changed, operation, gate)
    period.status = "cancelled" if cancelled else status
    period.ends_on = starts_on.isoformat() if cancelled else ends_on.isoformat()
    period.ended_by = actor_id
    period.ended_by_name = actor_name
    period.ended_at = datetime.now()
    if note:
        period.note = note
    return LOAChange(period, tuple(sorted(after)), changed)


def cancel_period(
    db: Session, employee: Employee, period: EmployeeLOAPeriod, *,
    today: date, gate: MonthGate, reason: str, operation: str = "撤销LOA",
) -> LOAChange:
    starts_on = date.fromisoformat(period.starts_on)
    ends_on = date.fromisoformat(period.ends_on) if period.ends_on else None
    horizon = open_horizon(db, employee.id, starts_on, today, ends_on or starts_on)
    before = excluded_months(starts_on, ends_on, horizon)
    changed = _changed_months(db, employee.id, horizon, before, set(), period.id)
    _check_months(db, employee, changed, operation, gate)
    period.status = "cancelled"
    period.note = f"{period.note or ''}\n撤销原因：{reason}".strip()
    return LOAChange(period, (), changed)


def recalculate_changed_months(db: Session, employee: Employee, months: tuple[str, ...] | set[str]) -> None:
    # SessionLocal has autoflush disabled; score queries must see the mutation.
    db.flush()
    for month in sorted(months):
        recalculate_attendance(db, employee, month)
