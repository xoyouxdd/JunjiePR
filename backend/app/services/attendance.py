"""Monthly attendance calculation and persistence, including LOA exclusion.

Single-employee updates and missing-row batches share score rules. The batch
path retains role/sick-leave prefetch and INSERT OR IGNORE concurrency behavior.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.role_constants import FRONTLINE_CODES, SCORE_UNIT, SUPERVISOR_SCORING_START_MONTH
from app.services.identity import base_role_at, base_roles_at, employee_active_on
from app.v2_models import (
    AttendanceMonthlyScore, AttendanceRule, Employee, EmployeeLOAPeriod, Role,
    SickLeaveRecord,
)


def attendance_scored_role(role: Role | None, month: str) -> bool:
    if not role:
        return False
    if role.code in FRONTLINE_CODES:
        return True
    return role.code == "SUPERVISOR" and month >= SUPERVISOR_SCORING_START_MONTH


HALF_DAY_DEDUCTION = Decimal("0.25")


def month_end(month: str) -> str:
    year, month_number = map(int, month.split("-"))
    return f"{year:04d}-{month_number:02d}-{monthrange(year, month_number)[1]:02d}"


def month_bounds(month: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month}-01")
    return start, start.replace(day=monthrange(start.year, start.month)[1])


def loa_periods_for_month(db: Session, employee_id: int, month: str) -> list[EmployeeLOAPeriod]:
    start, end = month_bounds(month)
    return (
        db.query(EmployeeLOAPeriod)
        .filter(
            EmployeeLOAPeriod.employee_id == employee_id,
            EmployeeLOAPeriod.status != "cancelled",
            EmployeeLOAPeriod.starts_on <= end.isoformat(),
            or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= start.isoformat()),
        )
        .order_by(EmployeeLOAPeriod.starts_on, EmployeeLOAPeriod.id)
        .all()
    )


def loa_excludes_month(db: Session, employee_id: int, month: str) -> bool:
    """An LOA touching any calendar day excludes that whole month from scoring."""
    return bool(loa_periods_for_month(db, employee_id, month))


def full_month_loa(db: Session, employee_id: int, month: str) -> bool:
    """Compatibility alias for callers not yet renamed to the current LOA rule."""
    return loa_excludes_month(db, employee_id, month)


def _sick_leave_date_values(rows: list[SickLeaveRecord], month: str) -> dict[date, Decimal]:
    month_start, month_finish = month_bounds(month)
    values: dict[date, Decimal] = {}
    for row in rows:
        current = max(date.fromisoformat(row.leave_start_date), month_start)
        finish = min(date.fromisoformat(row.leave_end_date), month_finish)
        remaining = Decimal(str(row.leave_days))
        while current <= finish and remaining > 0:
            value = min(Decimal("1.0"), remaining)
            values[current] = max(values.get(current, Decimal("0.0")), value)
            remaining -= value
            current += timedelta(days=1)
    return values


def attendance_day_totals(
    db: Session,
    employee_id: int,
    month: str,
    sick_rows: list[SickLeaveRecord] | None = None,
) -> tuple[Decimal, Decimal, bool]:
    if sick_rows is None:
        sick_rows = (
            db.query(SickLeaveRecord)
            .filter(
                SickLeaveRecord.employee_id == employee_id,
                SickLeaveRecord.attendance_month == month,
                SickLeaveRecord.status == "active",
            )
            .all()
        )
    day_values = _sick_leave_date_values(sick_rows, month)
    actual_days = sum(day_values.values(), Decimal("0.0"))
    # LOA is neither an inferred weekday absence nor a partial score deduction.
    # If it touches this month, source records remain auditable elsewhere but
    # attendance fields must not participate in any count or score.
    is_full_month_loa = loa_excludes_month(db, employee_id, month)
    if is_full_month_loa:
        return Decimal("0.0"), Decimal("0.0"), True
    charged_days = min(Decimal("22.0"), sum(day_values.values(), Decimal("0.0")))
    return actual_days, charged_days, is_full_month_loa


def sick_leave_score_deduction(charged_days: Decimal, daily_deduction: Decimal) -> Decimal:
    whole_days = int(charged_days)
    half_day = charged_days - Decimal(whole_days)
    deduction = daily_deduction * whole_days
    if half_day:
        deduction += HALF_DAY_DEDUCTION
    return deduction.quantize(SCORE_UNIT, rounding=ROUND_HALF_UP)


def calculate_attendance_score(
    eligible: bool,
    charged_days: Decimal,
    *,
    base: Decimal,
    bonus: Decimal,
    daily: Decimal,
    threshold: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return (final score, total deduction) without database access.

    Sick leave removes the perfect-attendance bonus; whole days use the rule's
    rate and a half-day uses the fixed 0.25 deduction. Ineligible months are zero.
    """
    if not eligible:
        return Decimal("0.00"), Decimal("0.00")
    if charged_days == 0:
        return base + bonus, Decimal("0.00")
    leave_deduction = sick_leave_score_deduction(charged_days, daily)
    total_deduction = bonus + leave_deduction
    final = max(Decimal("0.00"), base - leave_deduction)
    if final <= threshold:
        final = Decimal("0.00")
    return final, total_deduction


def recalculate_attendance(db: Session, employee: Employee, month: str) -> AttendanceMonthlyScore:
    end_date = month_end(month)
    end_role = base_role_at(db, employee.id, end_date)
    eligible = bool(attendance_scored_role(end_role, month) and employee_active_on(employee, end_date))
    rule = (
        db.query(AttendanceRule)
        .filter(AttendanceRule.active.is_(True), AttendanceRule.effective_date <= end_date)
        .order_by(AttendanceRule.effective_date.desc(), AttendanceRule.id.desc())
        .first()
    ) or db.query(AttendanceRule).filter(AttendanceRule.active.is_(True)).first()
    if not rule:
        raise HTTPException(500, "全勤规则未配置")
    sick_rows = (
        db.query(SickLeaveRecord)
        .filter(
            SickLeaveRecord.employee_id == employee.id,
            SickLeaveRecord.attendance_month == month,
            SickLeaveRecord.status == "active",
        )
        .all()
    )
    actual_days, charged_days, is_full_month_loa = attendance_day_totals(db, employee.id, month, sick_rows)
    eligible = eligible and not is_full_month_loa
    base = Decimal(str(rule.base_score)).quantize(SCORE_UNIT)
    bonus = Decimal(str(rule.perfect_bonus)).quantize(SCORE_UNIT)
    daily = Decimal(str(rule.sick_day_deduction)).quantize(SCORE_UNIT)
    threshold = Decimal(str(rule.zero_threshold)).quantize(SCORE_UNIT)
    final, total_deduction = calculate_attendance_score(
        eligible, charged_days, base=base, bonus=bonus, daily=daily, threshold=threshold,
    )
    row = (
        db.query(AttendanceMonthlyScore)
        .filter(AttendanceMonthlyScore.employee_id == employee.id, AttendanceMonthlyScore.attendance_month == month)
        .first()
    )
    if not row:
        row = AttendanceMonthlyScore(employee_id=employee.id, attendance_month=month)
        db.add(row)
    next_values = {
        "month_end_role_id": end_role.id if end_role else None,
        "eligible": eligible,
        "actual_sick_days": actual_days,
        "charged_sick_days": charged_days,
        "base_score": base if eligible else Decimal("0.00"),
        "perfect_bonus": bonus if eligible else Decimal("0.00"),
        "sick_deduction": total_deduction.quantize(SCORE_UNIT),
        "final_score": final.quantize(SCORE_UNIT, rounding=ROUND_HALF_UP),
        "rule_id": rule.id,
        "calculation_version": 2,
    }
    # Skip the write (and flush) when nothing changed so read paths such as
    # the dashboard do not turn repeated refreshes into database writes.
    if row.id is None or any(getattr(row, name) != value for name, value in next_values.items()):
        for name, value in next_values.items():
            setattr(row, name, value)
        row.calculated_at = datetime.now()
        db.flush()
    return row


def ensure_month_attendance(
    db: Session,
    month: str,
    employee_ids: list[int] | set[int] | None = None,
) -> dict[int, AttendanceMonthlyScore]:
    """Create missing monthly attendance rows in batches and leave current rows untouched.

    Sick-leave and employee-change workflows recalculate their affected employee directly,
    so statistics reads do not need to rewrite every employee on every request.
    """
    employee_query = db.query(Employee)
    if employee_ids is not None:
        ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
        if not ids:
            return {}
        employee_query = employee_query.filter(Employee.id.in_(ids))
    employees = employee_query.all()
    if not employees:
        return {}

    ids = [employee.id for employee in employees]
    existing_rows = (
        db.query(AttendanceMonthlyScore)
        .filter(
            AttendanceMonthlyScore.attendance_month == month,
            AttendanceMonthlyScore.employee_id.in_(ids),
        )
        .all()
    )
    records = {row.employee_id: row for row in existing_rows}
    missing = [employee for employee in employees if employee.id not in records]
    if not missing:
        return records

    end_date = month_end(month)
    missing_ids = [employee.id for employee in missing]
    end_roles = base_roles_at(db, missing_ids, end_date)
    rule = (
        db.query(AttendanceRule)
        .filter(AttendanceRule.active.is_(True), AttendanceRule.effective_date <= end_date)
        .order_by(AttendanceRule.effective_date.desc(), AttendanceRule.id.desc())
        .first()
    ) or db.query(AttendanceRule).filter(AttendanceRule.active.is_(True)).first()
    if not rule:
        raise HTTPException(500, "全勤规则未配置")

    sick_rows = (
        db.query(SickLeaveRecord)
        .filter(
            SickLeaveRecord.employee_id.in_(missing_ids),
            SickLeaveRecord.attendance_month == month,
            SickLeaveRecord.status == "active",
        )
        .all()
    )
    base = Decimal(str(rule.base_score)).quantize(SCORE_UNIT)
    bonus = Decimal(str(rule.perfect_bonus)).quantize(SCORE_UNIT)
    daily = Decimal(str(rule.sick_day_deduction)).quantize(SCORE_UNIT)
    threshold = Decimal(str(rule.zero_threshold)).quantize(SCORE_UNIT)
    calculated_at = datetime.now()
    pending_rows: list[dict] = []
    for employee in missing:
        end_role = end_roles.get(employee.id)
        if not attendance_scored_role(end_role, month):
            continue
        employee_actual_days, employee_charged_days, is_full_month_loa = attendance_day_totals(
            db,
            employee.id,
            month,
            [row for row in sick_rows if row.employee_id == employee.id],
        )
        eligible = employee_active_on(employee, end_date) and not is_full_month_loa
        final, total_deduction = calculate_attendance_score(
            eligible, employee_charged_days,
            base=base, bonus=bonus, daily=daily, threshold=threshold,
        )
        pending_rows.append(
            {
                "employee_id": employee.id,
                "attendance_month": month,
                "month_end_role_id": end_role.id,
                "eligible": eligible,
                "actual_sick_days": float(employee_actual_days),
                "charged_sick_days": float(employee_charged_days),
                "base_score": float(base if eligible else Decimal("0.00")),
                "perfect_bonus": float(bonus if eligible else Decimal("0.00")),
                "sick_deduction": float(total_deduction.quantize(SCORE_UNIT)),
                "final_score": float(final.quantize(SCORE_UNIT, rounding=ROUND_HALF_UP)),
                "rule_id": rule.id,
                "calculation_version": 2,
                "calculated_at": calculated_at,
            }
        )
    if pending_rows:
        db.execute(
            text(
                """
                INSERT OR IGNORE INTO attendance_monthly_scores (
                    employee_id, attendance_month, month_end_role_id, eligible,
                    actual_sick_days, charged_sick_days, base_score, perfect_bonus,
                    sick_deduction, final_score, rule_id, calculation_version, calculated_at
                ) VALUES (
                    :employee_id, :attendance_month, :month_end_role_id, :eligible,
                    :actual_sick_days, :charged_sick_days, :base_score, :perfect_bonus,
                    :sick_deduction, :final_score, :rule_id, :calculation_version, :calculated_at
                )
                """
            ),
            pending_rows,
        )
        created_rows = (
            db.query(AttendanceMonthlyScore)
            .filter(
                AttendanceMonthlyScore.attendance_month == month,
                AttendanceMonthlyScore.employee_id.in_([row["employee_id"] for row in pending_rows]),
            )
            .all()
        )
        records.update({row.employee_id: row for row in created_rows})
    return records
