"""Group membership, leadership and attraction management scopes.

An acting leader runs a group while assigned, otherwise its formal leader does.
New formal appointments require a base supervisor; historical higher-rank
appointments stay valid until HR replaces them. Commands flush, never commit.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app.role_constants import FRONTLINE_CODES, GSM_CODES, LEADER_CODES
from app.services.audit import write_audit
from app.services.identity import base_role_at, duty_code_at, role_at, roles_at
from app.v2_models import (
    Employee, GroupLeaderAssignment, GroupMembership, ManagementScope,
    RecognitionRecord, Role, WorkGroup,
)


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


ACTING_LEADER_FIRST = case((GroupLeaderAssignment.leader_type == "acting", 0), else_=1)


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
