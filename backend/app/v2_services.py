from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import case, or_, text
from sqlalchemy.orm import Session

from app.v2_database import FILE_DIR, synchronize_gsm_management_scope
from app.v2_models import (
    Attraction,
    AttendanceMonthlyScore,
    AttendanceRule,
    AuditLog,
    Employee,
    EmployeeActingDuty,
    EmployeeLOAPeriod,
    EmployeeRoleAssignment,
    GroupLeaderAssignment,
    GroupMembership,
    ManagementScope,
    RecognitionRecord,
    RecognitionScoreRule,
    Role,
    RolePermission,
    SickLeaveRecord,
    StoredFile,
    SystemAlert,
    WorkGroup,
    Permission,
)


FRONTLINE_CODES = {"CM", "TR"}
LEADER_CODES = {"TA_SUPERVISOR", "SUPERVISOR"}
GSM_CODES = {"TA_GSM", "GSM"}
RECOGNIZER_CODES = {"TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "AM", "OM"}
SENIOR_RECOGNIZER_CODES = {"TA_GSM", "GSM", "AM", "OM"}
RECOGNIZER_CIRCLE_ORDER = {"热力追踪": 0, "矮人迷宫": 1, "小熊罐子": 2}
RECOGNIZER_ROLE_ORDER = {
    "TA_SUPERVISOR": 0,
    "SUPERVISOR": 1,
    "TA_GSM": 0,
    "GSM": 1,
    "AM": 2,
    "OM": 3,
}
RECOGNIZER_ELIGIBILITY_START = "2026-08-01"
SCORE_UNIT = Decimal("0.01")
HALF_DAY_DEDUCTION = Decimal("0.25")


# Acting duties layer on a base identity and never replace it.  role_at returns
# the role actually in use (the highest-ranked active duty, else the base
# identity); base_role_at returns the base identity, which alone decides the
# scoring category and group membership.
DUTY_ROLE_CODES = {"TA_SUPERVISOR", "TA_GSM"}
DUTY_BASE_CODES = {"TA_SUPERVISOR": FRONTLINE_CODES, "TA_GSM": {"SUPERVISOR"}}
SCORING_CATEGORY_BY_CODE = {"CM": "frontline", "TR": "frontline", "SUPERVISOR": "supervisor"}
SCORED_BASE_CODES = set(SCORING_CATEGORY_BY_CODE)
# Supervisor performance (own scores and attendance) starts with this month;
# earlier months, including closed ones, keep their original totals.
SUPERVISOR_SCORING_START_MONTH = "2026-10"


def attendance_scored_role(role: Role | None, month: str) -> bool:
    if not role:
        return False
    if role.code in FRONTLINE_CODES:
        return True
    return role.code == "SUPERVISOR" and month >= SUPERVISOR_SCORING_START_MONTH


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
        credited = db.execute(
            text(
                "SELECT COALESCE(SUM(CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END), 0) "
                "FROM recognition_records WHERE employee_id=:employee_id AND status='confirmed' "
                "AND recognition_date>=:start AND recognition_date<=:end"
            ),
            {"employee_id": duty.employee_id, "start": period_start, "end": period_end},
        ).scalar() or 0
        deducted = db.execute(
            text(
                "SELECT COALESCE(SUM(points), 0) FROM deduction_records WHERE employee_id=:employee_id AND status='active' "
                "AND (upgrade_role IS NULL OR upgrade_role!='source_second') AND occurred_on>=:start AND occurred_on<=:end"
            ),
            {"employee_id": duty.employee_id, "start": period_start, "end": period_end},
        ).scalar() or 0
        score = (Decimal(str(credited)) - Decimal(str(deducted))).quantize(SCORE_UNIT)
        name = ACTING_NOTE_NAMES.get(duty.role.code, duty.role.name)
        notes.setdefault(duty.employee_id, []).append(f"含 {name}期间（{period_start[5:]} 至 {period_end[5:]}）得分 {score:.2f}")
    return {employee_id: "；".join(items) for employee_id, items in notes.items()}


def acting_duty_summary(db: Session, employee_id: int, month: str) -> tuple[str | None, int]:
    """The month's main duty code and its number of acting days."""
    first, last = f"{month}-01", month_end(month)
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


def gsm_candidates_for_attractions_bulk(
    db: Session,
    attraction_ids: list[int] | set[int],
    on_date: str | None = None,
) -> dict[int, list[tuple[Employee, Role]]]:
    """Resolve GSM/TA GSM candidates for many attractions with batched queries."""
    ids = list(dict.fromkeys(int(attraction_id) for attraction_id in attraction_ids))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    scopes = (
        db.query(ManagementScope)
        .filter(
            ManagementScope.attraction_id.in_(ids),
            ManagementScope.starts_on <= value,
            or_(ManagementScope.ends_on.is_(None), ManagementScope.ends_on >= value),
        )
        .all()
    )
    employee_ids = {scope.employee_id for scope in scopes}
    employees = (
        {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
        if employee_ids
        else {}
    )
    roles = roles_at(db, employee_ids, value)
    grouped: dict[int, list[tuple[Employee, Role]]] = {attraction_id: [] for attraction_id in ids}
    for scope in scopes:
        employee = employees.get(scope.employee_id)
        role = roles.get(scope.employee_id)
        if employee and employee.is_active and role and role.code in GSM_CODES:
            grouped[scope.attraction_id].append((employee, role))
    for attraction_id in ids:
        grouped[attraction_id].sort(key=lambda item: (item[1].code != "GSM", item[0].employee_no))
    return grouped


# The acting leader (代理组长) runs a group while assigned; otherwise the
# formal leader (原组长) does.  Use this ordering wherever one row is picked.
ACTING_LEADER_FIRST = case((GroupLeaderAssignment.leader_type == "acting", 0), else_=1)
# New 主管 appointments need a base 主管.  Assignments made before 2026-10
# may still hold a GSM/AM/OM; they stay valid until HR replaces them.
GROUP_SUPERVISOR_BASE_CODES = {"SUPERVISOR"}
FORMAL_LEADER_BASE_CODES = {"SUPERVISOR", "GSM", "AM", "OM"}


def group_code_for_index(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA, 27 → AB …"""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def group_code_index(code: str | None) -> int:
    value = 0
    for char in str(code or "").upper():
        if not "A" <= char <= "Z":
            return -1
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def group_code_sort_key(code: str | None) -> tuple[int, str]:
    return (len(code or "") or 99, code or "")


def group_display_name(circle_name: str, code: str) -> str:
    return f"{circle_name}{code}组"


def next_group_code(db: Session, attraction_id: int) -> str:
    """The earliest letter no open group of the circle uses (gaps first)."""
    used = {
        group_code_index(code)
        for (code,) in db.query(WorkGroup.code).filter(
            WorkGroup.attraction_id == attraction_id,
            WorkGroup.status != "closed",
            WorkGroup.code.isnot(None),
        )
    }
    index = 0
    while index in used:
        index += 1
    return group_code_for_index(index)


def resequence_group_codes(db: Session, attraction_id: int, operator: Employee | None, reason: str) -> list[tuple[str, str]]:
    """Re-letter the circle's open groups A, B, C… in their current order.

    Groups still named "<circle><old letter>组" get the matching new name; a
    name HR changed by hand is kept.  Closed groups give up their letter (a
    closed group sharing a new name is marked "（已关闭）").  Records refer to
    groups by id, so every page follows the new names.  Returns the renames.
    """
    from app.v2_models import Attraction

    circle = db.get(Attraction, attraction_id)
    circle_name = circle.name if circle else ""
    groups = db.query(WorkGroup).filter(WorkGroup.attraction_id == attraction_id).all()
    open_groups = sorted(
        (group for group in groups if group.status != "closed"),
        key=lambda group: (group.code is None, group_code_index(group.code), group.id),
    )
    changes: list[tuple[str, str]] = []
    new_names = set()
    for index, group in enumerate(open_groups):
        code = group_code_for_index(index)
        old_name = group.name
        default_name = not group.code or old_name == group_display_name(circle_name, group.code)
        name = group_display_name(circle_name, code) if default_name else old_name
        new_names.add(name)
        if (group.code, group.name) == (code, name):
            continue
        group.code, group.name = code, name
        group.revision += 1
        db.flush()
        changes.append((old_name, name))
        write_audit(db, operator, "小组重新排列字母", "work_group", group.id, before={"name": old_name}, after={"name": name, "code": code}, reason=reason)
    for group in groups:
        if group.status == "closed" and group.code:
            group.code = None
            if group.name in new_names:
                group.name = f"{group.name}（已关闭）"
    db.flush()
    return changes


def group_leader_label(formal_name: str, acting_name: str) -> str:
    """'主管 X · 代理主管 Y', or just the one that is set; empty when neither."""
    parts = [f"主管 {formal_name}" if formal_name else "", f"代理主管 {acting_name}" if acting_name else ""]
    return " · ".join(part for part in parts if part)


def group_supervisor_eligible(db: Session, employee_id: int, on_date: str | date | None = None) -> bool:
    base = base_role_at(db, employee_id, on_date)
    return bool(base and base.code in GROUP_SUPERVISOR_BASE_CODES)


def group_acting_eligible(db: Session, employee_id: int, on_date: str | date | None = None) -> bool:
    return duty_code_at(db, employee_id, on_date) == "TA_SUPERVISOR"


def _active_leader_query(db: Session, group_id: int, on_date: str | None = None):
    value = on_date or date.today().isoformat()
    return db.query(GroupLeaderAssignment).filter(
        GroupLeaderAssignment.group_id == group_id,
        GroupLeaderAssignment.status == "active",
        GroupLeaderAssignment.starts_on <= value,
        or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= value),
    )


def active_group_leader(db: Session, group_id: int, on_date: str | None = None) -> GroupLeaderAssignment | None:
    """The leader running the group: the acting leader if any, else the formal one."""
    return (
        _active_leader_query(db, group_id, on_date)
        .order_by(ACTING_LEADER_FIRST, GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc())
        .first()
    )


def group_leader_of_type(db: Session, group_id: int, leader_type: str, on_date: str | None = None) -> GroupLeaderAssignment | None:
    return (
        _active_leader_query(db, group_id, on_date)
        .filter(GroupLeaderAssignment.leader_type == leader_type)
        .order_by(GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc())
        .first()
    )


def formal_leader_eligible(db: Session, employee_id: int, on_date: str | date | None = None) -> bool:
    """Whether an existing 主管 assignment stays valid: base 主管 or above.
    New appointments use group_supervisor_eligible (base 主管 only)."""
    base = base_role_at(db, employee_id, on_date)
    return bool(base and base.code in FORMAL_LEADER_BASE_CODES)


def leader_type_for(db: Session, employee_id: int, on_date: str | date | None = None) -> str:
    return "formal" if formal_leader_eligible(db, employee_id, on_date) else "acting"


def groups_assigned_to(db: Session, leader_id: int) -> list[WorkGroup]:
    """Every group the employee is an active formal or acting leader of."""
    today = date.today().isoformat()
    return (
        db.query(WorkGroup)
        .join(GroupLeaderAssignment, GroupLeaderAssignment.group_id == WorkGroup.id)
        .filter(
            GroupLeaderAssignment.leader_employee_id == leader_id,
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= today,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= today),
        )
        .order_by(WorkGroup.name.asc())
        .distinct()
        .all()
    )


def groups_formally_led_by(db: Session, leader_id: int) -> list[WorkGroup]:
    today = date.today().isoformat()
    return (
        db.query(WorkGroup)
        .join(GroupLeaderAssignment, GroupLeaderAssignment.group_id == WorkGroup.id)
        .filter(
            GroupLeaderAssignment.leader_employee_id == leader_id,
            GroupLeaderAssignment.leader_type == "formal",
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= today,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= today),
        )
        .order_by(WorkGroup.name.asc())
        .all()
    )


def active_group_memberships(db: Session, group_id: int, on_date: str | None = None) -> list[GroupMembership]:
    value = on_date or date.today().isoformat()
    return (
        db.query(GroupMembership)
        .filter(
            GroupMembership.group_id == group_id,
            GroupMembership.status == "active",
            GroupMembership.starts_on <= value,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= value),
        )
        .all()
    )


def groups_led_by(db: Session, leader_id: int) -> list[WorkGroup]:
    """Groups this employee currently runs (acting leader, or formal leader of a
    group without an acting leader)."""
    return groups_led_by_bulk(db, [leader_id]).get(leader_id, [])


def groups_led_by_bulk(db: Session, leader_ids: list[int] | set[int]) -> dict[int, list[WorkGroup]]:
    """Same window semantics as groups_led_by, resolved for many leaders at once."""
    ids = list(dict.fromkeys(int(leader_id) for leader_id in leader_ids))
    if not ids:
        return {}
    today = date.today().isoformat()
    rows = (
        db.query(WorkGroup, GroupLeaderAssignment.leader_employee_id)
        .join(GroupLeaderAssignment, GroupLeaderAssignment.group_id == WorkGroup.id)
        .filter(
            GroupLeaderAssignment.leader_employee_id.in_(ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= today,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= today),
        )
        .order_by(WorkGroup.name.asc())
        .all()
    )
    operating = active_group_leaders_bulk(db, {group.id for group, _leader_id in rows}, today)
    grouped: dict[int, list[WorkGroup]] = {leader_id: [] for leader_id in ids}
    for group, leader_id in rows:
        running = operating.get(group.id)
        if running and running.leader_employee_id == leader_id and group not in grouped.setdefault(leader_id, []):
            grouped[leader_id].append(group)
    return grouped


def active_group_leaders_bulk(db: Session, group_ids: list[int] | set[int], on_date: str | None = None) -> dict[int, GroupLeaderAssignment | None]:
    """Same pick as active_group_leader (latest start, then id) for many groups."""
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    rows = (
        db.query(GroupLeaderAssignment)
        .filter(
            GroupLeaderAssignment.group_id.in_(ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= value,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= value),
        )
        .order_by(
            GroupLeaderAssignment.group_id,
            ACTING_LEADER_FIRST,
            GroupLeaderAssignment.starts_on.desc(),
            GroupLeaderAssignment.id.desc(),
        )
        .all()
    )
    resolved: dict[int, GroupLeaderAssignment | None] = {group_id: None for group_id in ids}
    for row in rows:
        if resolved.get(row.group_id) is None:
            resolved[row.group_id] = row
    return resolved


def active_group_memberships_bulk(db: Session, group_ids: list[int] | set[int], on_date: str | None = None) -> dict[int, list[GroupMembership]]:
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    rows = (
        db.query(GroupMembership)
        .filter(
            GroupMembership.group_id.in_(ids),
            GroupMembership.status == "active",
            GroupMembership.starts_on <= value,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= value),
        )
        .order_by(GroupMembership.id.asc())
        .all()
    )
    grouped: dict[int, list[GroupMembership]] = {group_id: [] for group_id in ids}
    for row in rows:
        grouped[row.group_id].append(row)
    return grouped


def direct_member_ids(db: Session, leader_id: int, *, include_overseen: bool = False) -> set[int]:
    """Employees whose records this leader reviews; with include_overseen, also
    the members a formal leader may view in groups run by an acting leader."""
    leader = db.get(Employee, leader_id)
    leader_role = role_at(db, leader_id) if leader else None
    if leader and leader_role and leader_role.code == "HR_CIRCLE":
        return {
            employee.id
            for employee in db.query(Employee).filter(
                Employee.attraction_id == leader.attraction_id,
                Employee.is_active.is_(True),
            ).all()
            if (role := role_at(db, employee.id)) and role.code in (FRONTLINE_CODES | LEADER_CODES | GSM_CODES)
        }
    today = date.today().isoformat()

    def members_of(group_ids: list[int]) -> set[int]:
        if not group_ids:
            return set()
        return {
            employee_id
            for (employee_id,) in db.query(GroupMembership.employee_id)
            .filter(
                GroupMembership.group_id.in_(group_ids),
                GroupMembership.status == "active",
                GroupMembership.starts_on <= today,
                or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= today),
            )
            .all()
        }

    # Members of the groups this leader runs; never the leader themselves.
    result = members_of([group.id for group in groups_led_by(db, leader_id)]) - {leader_id}
    formal_groups = groups_formally_led_by(db, leader_id)
    if formal_groups:
        # An acting leader inside the group they run is reviewed by its formal leader.
        formal_members = members_of([group.id for group in formal_groups])
        for group in formal_groups:
            acting = group_leader_of_type(db, group.id, "acting", today)
            if acting and acting.leader_employee_id in formal_members:
                result.add(acting.leader_employee_id)
        if include_overseen:
            # 原组长 may view (not review) the members of groups run by an acting leader.
            result |= formal_members - {leader_id}
    return result


def current_group_for_employee(db: Session, employee_id: int) -> WorkGroup | None:
    today = date.today().isoformat()
    membership = (
        db.query(GroupMembership)
        .filter(
            GroupMembership.employee_id == employee_id,
            GroupMembership.status == "active",
            GroupMembership.starts_on <= today,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= today),
        )
        .order_by(GroupMembership.starts_on.desc(), GroupMembership.id.desc())
        .first()
    )
    return membership.group if membership else None


def current_leader_for_employee(db: Session, employee_id: int) -> Employee | None:
    """Who reviews this employee: the leader running their group, or, for the
    acting leader of their own group, that group's formal leader."""
    group = current_group_for_employee(db, employee_id)
    assignment = active_group_leader(db, group.id) if group else None
    if assignment and assignment.leader_employee_id == employee_id:
        formal = group_leader_of_type(db, group.id, "formal")
        return formal.leader if formal and formal.leader_employee_id != employee_id else None
    return assignment.leader if assignment else None


def sync_pending_reviewers(db: Session, group_id: int) -> None:
    """Point the group's pending self-submitted records at their current reviewer."""
    for member in active_group_memberships(db, group_id):
        reviewer = current_leader_for_employee(db, member.employee_id)
        db.query(RecognitionRecord).filter(
            RecognitionRecord.employee_id == member.employee_id,
            RecognitionRecord.status == "pending",
            RecognitionRecord.source == "self",
        ).update({RecognitionRecord.assigned_reviewer_id: reviewer.id if reviewer else None}, synchronize_session=False)


def group_leader_names(db: Session, group_id: int) -> tuple[str, str]:
    """(原组长 name, 代理组长 name) of a group; empty strings when unset."""
    formal = group_leader_of_type(db, group_id, "formal")
    acting = group_leader_of_type(db, group_id, "acting")
    return (formal.leader.name if formal else "", acting.leader.name if acting else "")


def active_frontline_employees(db: Session) -> list[Employee]:
    rows = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.name.asc()).all()
    return [employee for employee in rows if (role := base_role_at(db, employee.id)) and role.code in FRONTLINE_CODES]


def managed_attraction_ids(db: Session, employee_id: int, on_date: str | None = None) -> set[int]:
    value = on_date or date.today().isoformat()
    employee = db.get(Employee, employee_id)
    employee_role = role_at(db, employee_id, value) if employee else None
    if employee and employee_role and employee_role.code == "HR_CIRCLE":
        return {employee.attraction_id} if employee.attraction_id else set()
    return {
        attraction_id
        for (attraction_id,) in db.query(ManagementScope.attraction_id)
        .filter(
            ManagementScope.employee_id == employee_id,
            ManagementScope.starts_on <= value,
            or_(ManagementScope.ends_on.is_(None), ManagementScope.ends_on >= value),
        )
        .all()
    }


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


# Audit entity → employee circle, so circle HR sees only their own circle.
# Entities not listed here (exports, settings, backups) stay global-only.
AUDIT_EMPLOYEE_ENTITY_TYPES = {"employee", "employee_group", "employee_loa", "employee_login_archive", "employee_number_change"}


def audit_attraction_id(db: Session, entity_type: str, entity_id) -> int | None:
    from app.v2_models import (
        CircleTransferRequest,
        DeductionFollowUp,
        DeductionRecord,
        DeductionUpgradeRequest,
        MonthClosure,
        UserAccount,
        WorkGroup,
    )

    try:
        key = int(entity_id)
    except (TypeError, ValueError):
        return None

    def employee_circle(employee_id: int | None) -> int | None:
        employee = db.get(Employee, employee_id) if employee_id else None
        return employee.attraction_id if employee else None

    if entity_type == "recognition":
        row = db.get(RecognitionRecord, key)
        return row.home_attraction_id if row else None
    if entity_type == "deduction":
        row = db.get(DeductionRecord, key)
        return row.attraction_id_snapshot if row else None
    if entity_type == "sick_leave":
        row = db.get(SickLeaveRecord, key)
        return row.attraction_id_snapshot if row else None
    if entity_type in AUDIT_EMPLOYEE_ENTITY_TYPES:
        return employee_circle(key)
    if entity_type == "user_account":
        account = db.get(UserAccount, key)
        return employee_circle(account.employee_id) if account else None
    if entity_type == "employee_loa_period":
        row = db.get(EmployeeLOAPeriod, key)
        return employee_circle(row.employee_id) if row else None
    if entity_type == "acting_duty":
        row = db.get(EmployeeActingDuty, key)
        return employee_circle(row.employee_id) if row else None
    if entity_type == "deduction_upgrade":
        row = db.get(DeductionUpgradeRequest, key)
        return employee_circle(row.employee_id) if row else None
    if entity_type == "deduction_follow_up":
        row = db.get(DeductionFollowUp, key)
        return employee_circle(row.employee_id) if row else None
    if entity_type == "work_group":
        row = db.get(WorkGroup, key)
        return row.attraction_id if row else None
    if entity_type == "month_close":
        row = db.get(MonthClosure, key)
        return row.attraction_id if row else None
    if entity_type == "circle_transfer":
        # The source circle; the target circle's HR is matched by the log query.
        row = db.get(CircleTransferRequest, key)
        return row.source_attraction_id if row else None
    return None


def write_audit(
    db: Session,
    operator: Employee | None,
    action: str,
    entity_type: str,
    entity_id=None,
    *,
    before=None,
    after=None,
    reason=None,
    ip_address=None,
) -> None:
    db.add(
        AuditLog(
            operator_id=operator.id if operator else None,
            operator_name=operator.name if operator else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before_json=json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            after_json=json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            reason=reason,
            ip_address=ip_address,
            attraction_id=audit_attraction_id(db, entity_type, entity_id),
        )
    )


BUSINESS_ATTACHMENT_EFFECTIVE_DATE = "2026-09-01"
BUSINESS_ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _stream_upload_to_path(upload: UploadFile, path: Path, *, max_bytes: int, empty_message: str, label: str) -> tuple[int, str]:
    """Persist a bounded upload without retaining a 100MB request in RAM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("wb") as target:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(400, f"{label}不能超过{max_bytes // 1024 // 1024}MB")
                digest.update(chunk)
                target.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if not total:
        path.unlink(missing_ok=True)
        raise HTTPException(400, empty_message)
    return total, digest.hexdigest()


async def save_upload(db: Session, upload: UploadFile, uploader_id: int, *, allowed_extensions: set[str], max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES) -> StoredFile:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise HTTPException(400, f"文件格式不支持，仅允许：{', '.join(sorted(allowed_extensions))}")
    storage_key = f"{uuid4().hex}{extension}"
    path = FILE_DIR / storage_key
    file_size, digest = await _stream_upload_to_path(upload, path, max_bytes=max_bytes, empty_message="文件不能为空", label="文件")
    record = StoredFile(
        storage_key=storage_key,
        original_filename=upload.filename or storage_key,
        extension=extension,
        mime_type=upload.content_type,
        file_size=file_size,
        sha256=digest,
        uploaded_by=uploader_id,
        status="active",
    )
    db.add(record)
    try:
        db.flush()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return record


def detect_image_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


async def save_image_upload(db: Session, upload: UploadFile, uploader_id: int, *, max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES) -> StoredFile:
    staging_key = f"{uuid4().hex}.upload"
    staging_path = FILE_DIR / staging_key
    file_size, digest = await _stream_upload_to_path(upload, staging_path, max_bytes=max_bytes, empty_message="认可图片不能为空", label="认可图片")
    with staging_path.open("rb") as source:
        detected = detect_image_type(source.read(16))
    if not detected:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(400, "认可图片格式无效，仅支持JPG、PNG、WebP")
    extension, mime_type = detected
    storage_key = f"{uuid4().hex}{extension}"
    path = FILE_DIR / storage_key
    staging_path.replace(path)
    record = StoredFile(
        storage_key=storage_key,
        original_filename=upload.filename or storage_key,
        extension=extension,
        mime_type=mime_type,
        file_size=file_size,
        sha256=digest,
        uploaded_by=uploader_id,
        status="active",
    )
    db.add(record)
    try:
        db.flush()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return record


def remove_upload_file(file_row: StoredFile | None) -> None:
    if not file_row:
        return
    path = (FILE_DIR / file_row.storage_key).resolve()
    if FILE_DIR.resolve() in path.parents:
        path.unlink(missing_ok=True)


def month_end(month: str) -> str:
    year, month_number = map(int, month.split("-"))
    return f"{year:04d}-{month_number:02d}-{monthrange(year, month_number)[1]:02d}"


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
    if not eligible:
        final = Decimal("0.00")
        total_deduction = Decimal("0.00")
    elif charged_days == 0:
        final = base + bonus
        total_deduction = Decimal("0.00")
    else:
        leave_deduction = sick_leave_score_deduction(charged_days, daily)
        total_deduction = bonus + leave_deduction
        final = max(Decimal("0.00"), base - leave_deduction)
        if final <= threshold:
            final = Decimal("0.00")
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
    actual_days: dict[int, Decimal] = {employee_id: Decimal("0.0") for employee_id in missing_ids}
    charged_days: dict[int, Decimal] = {employee_id: Decimal("0.0") for employee_id in missing_ids}
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
        actual_days[employee.id] = employee_actual_days
        charged_days[employee.id] = employee_charged_days
        eligible = employee_active_on(employee, end_date) and not is_full_month_loa
        if not eligible:
            final = Decimal("0.00")
            total_deduction = Decimal("0.00")
        elif employee_charged_days == 0:
            final = base + bonus
            total_deduction = Decimal("0.00")
        else:
            leave_deduction = sick_leave_score_deduction(employee_charged_days, daily)
            total_deduction = bonus + leave_deduction
            final = max(Decimal("0.00"), base - leave_deduction)
            if final <= threshold:
                final = Decimal("0.00")
        pending_rows.append(
            {
                "employee_id": employee.id,
                "attendance_month": month,
                "month_end_role_id": end_role.id,
                "eligible": eligible,
                "actual_sick_days": float(actual_days[employee.id]),
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


def create_alert(db: Session, alert_type: str, dedupe_key: str, message: str, *, employee_id=None, group_id=None, due_date=None):
    if db.query(SystemAlert).filter_by(alert_type=alert_type, dedupe_key=dedupe_key).first():
        return
    db.add(
        SystemAlert(
            alert_type=alert_type,
            dedupe_key=dedupe_key,
            employee_id=employee_id,
            group_id=group_id,
            due_date=due_date,
            message=message,
            status="open",
        )
    )


def process_role_expirations(db: Session) -> None:
    today = date.today()
    today_text = today.isoformat()
    temporary_rows = db.query(EmployeeRoleAssignment).filter(EmployeeRoleAssignment.assignment_type == "temporary").all()
    for assignment in temporary_rows:
        if assignment.ends_on:
            end = date.fromisoformat(assignment.ends_on)
            days = (end - today).days
            if days in (30, 7, 1):
                create_alert(
                    db,
                    "temporary_role_expiring",
                    f"{assignment.id}:{days}",
                    f"{assignment.employee.name}的{assignment.role.name}任期将在{days}天后结束",
                    employee_id=assignment.employee_id,
                    due_date=assignment.ends_on,
                )
            if end < today and assignment.status != "expired":
                assignment.status = "expired"
                return_start = (end + timedelta(days=1)).isoformat()
                covering = (
                    db.query(EmployeeRoleAssignment)
                    .filter(
                        EmployeeRoleAssignment.employee_id == assignment.employee_id,
                        EmployeeRoleAssignment.starts_on <= today_text,
                        or_(EmployeeRoleAssignment.ends_on.is_(None), EmployeeRoleAssignment.ends_on >= today_text),
                        EmployeeRoleAssignment.status != "cancelled",
                    )
                    .first()
                )
                if not covering and assignment.return_role_id:
                    db.add(
                        EmployeeRoleAssignment(
                            employee_id=assignment.employee_id,
                            role_id=assignment.return_role_id,
                            starts_on=return_start,
                            assignment_type="permanent",
                            status="active",
                            reason="临时角色到期自动恢复",
                        )
                    )
    for duty in db.query(EmployeeActingDuty).filter(EmployeeActingDuty.status == "active", EmployeeActingDuty.ends_on.isnot(None)).all():
        end = date.fromisoformat(duty.ends_on)
        days = (end - today).days
        if days in (30, 7, 1):
            create_alert(
                db,
                "acting_duty_expiring",
                f"{duty.id}:{days}",
                f"{duty.employee.name}的代理{duty.role.name}将在{days}天后结束",
                employee_id=duty.employee_id,
                due_date=duty.ends_on,
            )
        if end < today:
            duty.status = "ended"
            db.flush()
            current = role_at(db, duty.employee_id, today_text)
            synchronize_gsm_management_scope(db, duty.employee, current.code if current else None, today_text)
    db.flush()
    for leader_assignment in db.query(GroupLeaderAssignment).filter(GroupLeaderAssignment.status == "active").all():
        if leader_assignment.ends_on and leader_assignment.ends_on < today_text:
            still_valid = False
        elif leader_assignment.leader_type == "formal":
            # A 主管 acting as TA GSM keeps their group.
            still_valid = formal_leader_eligible(db, leader_assignment.leader_employee_id, today_text)
        else:
            still_valid = can_lead_on(db, leader_assignment.leader_employee_id, today_text)
        if still_valid:
            continue
        leader_assignment.status = "ended"
        leader_assignment.ends_on = min(leader_assignment.ends_on or today_text, today_text)
        group = leader_assignment.group
        db.query(RecognitionRecord).filter(
            RecognitionRecord.assigned_reviewer_id == leader_assignment.leader_employee_id,
            RecognitionRecord.status == "pending",
        ).update({RecognitionRecord.assigned_reviewer_id: None}, synchronize_session=False)
        db.flush()
        if active_group_leader(db, group.id, today_text):
            # The other leader (formal or acting) keeps the group running.
            continue
        group.status = "pending_takeover"
        create_alert(
            db,
            "group_pending_takeover",
            str(group.id),
            f"{group.name}的负责人已失去带组资格，请在小组管理中设置负责人",
            employee_id=leader_assignment.leader_employee_id,
            group_id=group.id,
        )
    resolve_acting_duty_migration_alerts(db)
    db.commit()


def resolve_acting_duty_migration_alerts(db: Session, employee_id: int | None = None) -> None:
    """Close "无法确定本职" alerts once HR has confirmed the person's base identity."""
    query = db.query(SystemAlert).filter(SystemAlert.alert_type == "acting_duty_migration", SystemAlert.status == "open")
    if employee_id is not None:
        query = query.filter(SystemAlert.employee_id == employee_id)
    for alert in query.all():
        base = base_role_at(db, alert.employee_id) if alert.employee_id else None
        if base and base.code not in DUTY_ROLE_CODES:
            alert.status = "handled"
            alert.handled_at = datetime.now()
