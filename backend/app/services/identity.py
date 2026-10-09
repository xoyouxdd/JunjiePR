"""Date-sensitive base identities, acting duties and recognizer eligibility.

The effective role adds acting permissions; the base role alone decides the
scoring category and group membership. These queries never commit a session.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.role_constants import (
    FRONTLINE_CODES, LEADER_CODES, RECOGNIZER_CODES, RECOGNIZER_CIRCLE_ORDER,
    RECOGNIZER_ELIGIBILITY_START, RECOGNIZER_ROLE_ORDER,
    SCORING_CATEGORY_BY_CODE, SCORE_UNIT, SENIOR_RECOGNIZER_CODES,
)
from app.score_policy import loa_month_exclusion
from app.v2_models import (
    Attraction, DeductionRecord, Employee, EmployeeActingDuty,
    EmployeeRoleAssignment, Permission, RecognitionRecord, RecognitionScoreRule,
    Role, RolePermission,
)


def _date_value(on_date: str | date | None) -> str:
    return on_date.isoformat() if isinstance(on_date, date) else (on_date or date.today().isoformat())


def scoring_category(role: Role | None) -> str | None:
    return SCORING_CATEGORY_BY_CODE.get(role.code) if role else None


def duties_at(db: Session, employee_id: int, on_date: str | date | None = None) -> list[Role]:
    value = _date_value(on_date)
    rows = (
        db.query(EmployeeActingDuty)
        .filter(
            EmployeeActingDuty.employee_id == employee_id,
            EmployeeActingDuty.status != "cancelled",
            EmployeeActingDuty.starts_on <= value,
            or_(EmployeeActingDuty.ends_on.is_(None), EmployeeActingDuty.ends_on >= value),
        )
        .all()
    )
    return sorted({row.role for row in rows}, key=lambda role: (-role.rank, role.code))


def duties_at_bulk(db: Session, employee_ids: list[int] | set[int], on_date: str | date | None = None) -> dict[int, list[Role]]:
    ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
    if not ids:
        return {}
    value = _date_value(on_date)
    rows = (
        db.query(EmployeeActingDuty)
        .filter(
            EmployeeActingDuty.employee_id.in_(ids),
            EmployeeActingDuty.status != "cancelled",
            EmployeeActingDuty.starts_on <= value,
            or_(EmployeeActingDuty.ends_on.is_(None), EmployeeActingDuty.ends_on >= value),
        )
        .all()
    )
    grouped: dict[int, set[Role]] = {employee_id: set() for employee_id in ids}
    for row in rows:
        grouped[row.employee_id].add(row.role)
    return {employee_id: sorted(roles, key=lambda role: (-role.rank, role.code)) for employee_id, roles in grouped.items()}


def acting_duty_periods(db: Session, employee_id: int, start: str, end: str) -> list[tuple[str, str, str, str]]:
    """(duty code, duty name, from, to) for duty days inside [start, end]."""
    rows = (
        db.query(EmployeeActingDuty)
        .filter(
            EmployeeActingDuty.employee_id == employee_id,
            EmployeeActingDuty.status != "cancelled",
            EmployeeActingDuty.starts_on <= end,
            or_(EmployeeActingDuty.ends_on.is_(None), EmployeeActingDuty.ends_on >= start),
        )
        .order_by(EmployeeActingDuty.starts_on, EmployeeActingDuty.id)
        .all()
    )
    return [(row.role.code, row.role.name, max(row.starts_on, start), min(row.ends_on or end, end)) for row in rows]


ACTING_NOTE_NAMES = {"TA_SUPERVISOR": "TA 主管", "TA_GSM": "TA GSM"}


def acting_period_notes(db: Session, employee_ids: list[int] | set[int], start: str, end: str) -> dict[int, str]:
    """e.g. "含 TA 主管期间（10-01 至 10-31）得分 3.50" for employees acting within [start, end].

    Recognition counts by recognition date (credited score after the monthly
    cap), deductions by event date; attendance is monthly and is not split.
    """
    ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
    if not ids:
        return {}
    duty_rows = (
        db.query(EmployeeActingDuty)
        .filter(
            EmployeeActingDuty.employee_id.in_(ids),
            EmployeeActingDuty.status != "cancelled",
            EmployeeActingDuty.starts_on <= end,
            or_(EmployeeActingDuty.ends_on.is_(None), EmployeeActingDuty.ends_on >= start),
        )
        .order_by(EmployeeActingDuty.employee_id, EmployeeActingDuty.starts_on, EmployeeActingDuty.id)
        .all()
    )
    notes: dict[int, list[str]] = {}
    for duty in duty_rows:
        period_start, period_end = max(duty.starts_on, start), min(duty.ends_on or end, end)
        credited = db.query(func.sum(case(
            (RecognitionRecord.recognition_date < "2026-09-01", RecognitionRecord.fraction),
            else_=RecognitionRecord.credited_fraction,
        ))).filter(
            RecognitionRecord.employee_id == duty.employee_id,
            RecognitionRecord.status == "confirmed",
            RecognitionRecord.recognition_date >= period_start,
            RecognitionRecord.recognition_date <= period_end,
            ~loa_month_exclusion(RecognitionRecord.employee_id, RecognitionRecord.recognition_month),
        ).scalar() or 0
        deducted = db.query(func.sum(DeductionRecord.points)).filter(
            DeductionRecord.employee_id == duty.employee_id,
            DeductionRecord.status == "active",
            or_(DeductionRecord.upgrade_role.is_(None), DeductionRecord.upgrade_role != "source_second"),
            DeductionRecord.occurred_on >= period_start,
            DeductionRecord.occurred_on <= period_end,
            ~loa_month_exclusion(DeductionRecord.employee_id, DeductionRecord.deduction_month),
        ).scalar() or 0
        score = (Decimal(str(credited)) - Decimal(str(deducted))).quantize(SCORE_UNIT)
        name = ACTING_NOTE_NAMES.get(duty.role.code, duty.role.name)
        notes.setdefault(duty.employee_id, []).append(f"含 {name}期间（{period_start[5:]} 至 {period_end[5:]}）得分 {score:.2f}")
    return {employee_id: "；".join(items) for employee_id, items in notes.items()}


def acting_duty_summary(db: Session, employee_id: int, month: str) -> tuple[str | None, int]:
    """The month's main duty code and its number of acting days."""
    year, month_number = map(int, month.split("-"))
    first = f"{month}-01"
    last = f"{year:04d}-{month_number:02d}-{monthrange(year, month_number)[1]:02d}"
    days_by_code: dict[str, int] = {}
    for code, _name, start, end in acting_duty_periods(db, employee_id, first, last):
        days_by_code[code] = days_by_code.get(code, 0) + (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if not days_by_code:
        return None, 0
    code = max(days_by_code, key=lambda key: days_by_code[key])
    return code, min(sum(days_by_code.values()), int(last[-2:]))


def duty_code_at(db: Session, employee_id: int, on_date: str | date | None = None) -> str | None:
    duties = duties_at(db, employee_id, on_date)
    return duties[0].code if duties else None


def role_at(db: Session, employee_id: int, on_date: str | date | None = None) -> Role | None:
    duties = duties_at(db, employee_id, on_date)
    if duties:
        return duties[0]
    return base_role_at(db, employee_id, on_date)


def roles_at(db: Session, employee_ids: list[int] | set[int], on_date: str | date | None = None) -> dict[int, Role | None]:
    """Resolve many employees' roles in use without one query per employee."""
    base = base_roles_at(db, employee_ids, on_date)
    duties = duties_at_bulk(db, list(base), on_date)
    return {employee_id: (duties.get(employee_id) or [role])[0] for employee_id, role in base.items()}


def can_lead_on(db: Session, employee_id: int, on_date: str | date | None = None) -> bool:
    """A base supervisor (also while acting as TA GSM) or an acting TA主管."""
    return any(role and role.code in LEADER_CODES for role in (role_at(db, employee_id, on_date), base_role_at(db, employee_id, on_date)))


def identity_labels(db: Session, employee_ids: list[int] | set[int], on_date: str | date | None = None) -> dict[int, str]:
    """Display names such as "CM · 代理TA主管" for many employees at once."""
    base = base_roles_at(db, employee_ids, on_date)
    duties = duties_at_bulk(db, list(base), on_date)
    labels: dict[int, str] = {}
    for employee_id, role in base.items():
        duty = (duties.get(employee_id) or [None])[0]
        if not role:
            labels[employee_id] = duty.name if duty else "未配置"
        elif duty and duty.code != role.code:
            labels[employee_id] = f"{role.name} · 代理{duty.name}"
        else:
            labels[employee_id] = role.name
    return labels


def identity_label(db: Session, employee_id: int, on_date: str | date | None = None) -> str:
    return identity_labels(db, [employee_id], on_date).get(employee_id, "未配置")


def base_role_at(db: Session, employee_id: int, on_date: str | date | None = None) -> Role | None:
    value = _date_value(on_date)
    assignment = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id == employee_id,
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= value,
            or_(EmployeeRoleAssignment.ends_on.is_(None), EmployeeRoleAssignment.ends_on >= value),
        )
        .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
        .first()
    )
    if assignment:
        return assignment.role
    latest = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id == employee_id,
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= value,
        )
        .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
        .first()
    )
    if latest and latest.ends_on and latest.ends_on < value and latest.return_role_id:
        return db.get(Role, latest.return_role_id)
    return None


def base_roles_at(db: Session, employee_ids: list[int] | set[int], on_date: str | date | None = None) -> dict[int, Role | None]:
    """Resolve many employees' base identities without issuing one query per employee."""
    ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
    if not ids:
        return {}
    value = _date_value(on_date)
    assignments = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id.in_(ids),
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= value,
        )
        .order_by(
            EmployeeRoleAssignment.employee_id,
            EmployeeRoleAssignment.starts_on.desc(),
            EmployeeRoleAssignment.id.desc(),
        )
        .all()
    )
    grouped: dict[int, list[EmployeeRoleAssignment]] = {employee_id: [] for employee_id in ids}
    role_ids: set[int] = set()
    resolved_role_ids: dict[int, int | None] = {}
    for assignment in assignments:
        grouped[assignment.employee_id].append(assignment)
        role_ids.add(assignment.role_id)
        if assignment.return_role_id:
            role_ids.add(assignment.return_role_id)
    for employee_id in ids:
        rows = grouped[employee_id]
        active = next((row for row in rows if row.ends_on is None or row.ends_on >= value), None)
        if active:
            resolved_role_ids[employee_id] = active.role_id
        elif rows and rows[0].ends_on and rows[0].ends_on < value and rows[0].return_role_id:
            resolved_role_ids[employee_id] = rows[0].return_role_id
        else:
            resolved_role_ids[employee_id] = None
    roles = {role.id: role for role in db.query(Role).filter(Role.id.in_(role_ids)).all()} if role_ids else {}
    return {employee_id: roles.get(role_id) for employee_id, role_id in resolved_role_ids.items()}


def role_permissions(db: Session, role_id: int) -> set[str]:
    return {
        code
        for (code,) in (
            db.query(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .filter(RolePermission.role_id == role_id)
            .all()
        )
    }


def active_frontline_employees(db: Session) -> list[Employee]:
    rows = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.name.asc()).all()
    return [employee for employee in rows if (role := base_role_at(db, employee.id)) and role.code in FRONTLINE_CODES]


def recognizer_role_for_date(db: Session, employee_id: int, on_date: str | date | None = None) -> Role | None:
    """Apply the recognition policy start to the employee's current eligible role."""
    eligibility_date = on_date.isoformat() if isinstance(on_date, date) else str(on_date or date.today().isoformat())
    if eligibility_date < RECOGNIZER_ELIGIBILITY_START:
        return None
    # The score follows the recognizer's role on the recognition date (today's
    # role for a future date, whose role is not known yet).
    role_date = min(eligibility_date, date.today().isoformat())
    role_on_date = role_at(db, employee_id, role_date) or earliest_roles(db, [employee_id]).get(employee_id)
    return role_on_date if role_on_date and role_on_date.code in RECOGNIZER_CODES else None


def earliest_roles(db: Session, employee_ids: list[int] | set[int]) -> dict[int, Role]:
    """First recorded role, used for dates before an imported role history begins."""
    ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
    if not ids:
        return {}
    first: dict[int, EmployeeRoleAssignment] = {}
    for row in (
        db.query(EmployeeRoleAssignment)
        .filter(EmployeeRoleAssignment.employee_id.in_(ids), EmployeeRoleAssignment.status != "cancelled")
        .order_by(EmployeeRoleAssignment.employee_id, EmployeeRoleAssignment.starts_on, EmployeeRoleAssignment.id)
        .all()
    ):
        first.setdefault(row.employee_id, row)
    return {employee_id: row.role for employee_id, row in first.items()}


def recognizer_options(db: Session, attraction_id: int, on_date: str | date | None = None) -> list[dict]:
    eligibility_date = on_date.isoformat() if isinstance(on_date, date) else (str(on_date or date.today().isoformat()))
    if eligibility_date < RECOGNIZER_ELIGIBILITY_START:
        return []
    # Anyone employed on the recognition date may be chosen, so a back-dated
    # entry can still name a recognizer who has since left.
    role_date = min(eligibility_date, date.today().isoformat())
    employees = [
        employee
        for employee in db.query(Employee).order_by(Employee.name.asc()).all()
        if employed_on(employee, role_date)
    ]
    # Batch role/circle/score resolution keeps this endpoint at a handful of
    # queries regardless of roster size instead of several per employee.
    roles = roles_at(db, [employee.id for employee in employees], role_date)
    missing = [employee_id for employee_id, role in roles.items() if role is None]
    roles.update(earliest_roles(db, missing))
    circles = {
        circle.id: circle
        for circle in db.query(Attraction).filter(Attraction.employee_circle.is_(True), Attraction.active.is_(True)).all()
    }
    score_rules: dict[int, list[RecognitionScoreRule]] = {}
    for rule in (
        db.query(RecognitionScoreRule)
        .filter(RecognitionScoreRule.active.is_(True), RecognitionScoreRule.effective_date <= eligibility_date)
        .order_by(RecognitionScoreRule.role_id, RecognitionScoreRule.effective_date.desc(), RecognitionScoreRule.id.desc())
        .all()
    ):
        score_rules.setdefault(rule.role_id, []).append(rule)
    fallback_rules: dict[int, RecognitionScoreRule] = {}
    for rule in (
        db.query(RecognitionScoreRule)
        .filter(RecognitionScoreRule.active.is_(True))
        .order_by(RecognitionScoreRule.role_id, RecognitionScoreRule.effective_date.asc())
        .all()
    ):
        fallback_rules.setdefault(rule.role_id, rule)

    def score_for(role_id: int) -> Decimal:
        rule = score_rules.get(role_id, [None])[0] or fallback_rules.get(role_id)
        if not rule:
            raise HTTPException(400, "该认可人角色尚未配置分值")
        return Decimal(str(rule.score)).quantize(SCORE_UNIT)

    result = []
    for employee in employees:
        role = roles.get(employee.id)
        if not role or role.code not in RECOGNIZER_CODES:
            continue
        if role.code in LEADER_CODES:
            circle = circles.get(employee.attraction_id) if employee.attraction_id else None
            if not circle:
                continue
            group_key = f"circle:{circle.id}"
            group_label = circle.name
            group_order = RECOGNIZER_CIRCLE_ORDER.get(circle.name, len(RECOGNIZER_CIRCLE_ORDER))
        elif role.code in SENIOR_RECOGNIZER_CODES:
            group_key = "tagsm_plus"
            group_label = "TAGSM及以上"
            group_order = len(RECOGNIZER_CIRCLE_ORDER)
        else:
            continue
        result.append(
            {
                "id": employee.id,
                "employee_no": employee.employee_no,
                "name": employee.name,
                "role_code": role.code,
                "role_name": role.name,
                "score": float(score_for(role.id)),
                "group_key": group_key,
                "group_label": group_label,
                "group_order": group_order,
                "role_order": RECOGNIZER_ROLE_ORDER[role.code],
            }
        )
    return sorted(result, key=lambda row: (row["group_order"], row["role_order"], row["name"], row["id"]))


def recognition_score_for_role(db: Session, role_id: int, on_date: str) -> Decimal:
    rule = (
        db.query(RecognitionScoreRule)
        .filter(
            RecognitionScoreRule.role_id == role_id,
            RecognitionScoreRule.active.is_(True),
            RecognitionScoreRule.effective_date <= on_date,
        )
        .order_by(RecognitionScoreRule.effective_date.desc(), RecognitionScoreRule.id.desc())
        .first()
    )
    if not rule:
        rule = (
            db.query(RecognitionScoreRule)
            .filter(RecognitionScoreRule.role_id == role_id, RecognitionScoreRule.active.is_(True))
            .order_by(RecognitionScoreRule.effective_date.asc())
            .first()
        )
    if not rule:
        raise HTTPException(400, "该认可人角色尚未配置分值")
    return Decimal(str(rule.score)).quantize(SCORE_UNIT)


def employee_active_on(employee: Employee, on_date: str) -> bool:
    if employee.hired_on and employee.hired_on > on_date:
        return False
    if employee.terminated_on and employee.terminated_on <= on_date:
        return False
    return employee.is_active


def employed_on(employee: Employee, on_date: str) -> bool:
    """Whether the employee had not yet left on a (possibly past) date."""
    if employee.terminated_on:
        return employee.terminated_on > on_date
    return employee.is_active
