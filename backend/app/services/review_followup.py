"""Ownership and escalation for the administrator's signing-card follow-up."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, load_only

from app.services.identity import base_roles_at, duties_at_bulk, role_permissions
from app.services.organization import active_group_leaders_bulk, group_leader_of_type
from app.v2_models import Employee, GroupMembership, RecognitionInboxItem, RecognitionRecord, UserAccount, WorkGroup

REVIEW_REMINDER_AGE = timedelta(hours=48)
REVIEW_ESCALATION_AGE = timedelta(days=7)


@dataclass
class PendingReview:
    row: RecognitionRecord
    group_name: str
    reviewer: Employee | None = None
    shared_queue: str = ""

    @property
    def unassigned(self) -> bool:
        return self.reviewer is None and not self.shared_queue

    def needs_followup(self, now: datetime) -> bool:
        return self.unassigned or self.row.submitted_at < now - REVIEW_ESCALATION_AGE


def supervisor_self_review(row: RecognitionRecord) -> bool:
    return row.source == "self" and row.employee_role_code_snapshot == "SUPERVISOR"


def shared_review_label(row: RecognitionRecord) -> str:
    return "GSM / AM 共同复核" if row.employee_acting_duty_code == "TA_GSM" else "GSM 共同复核"


def pending_reviews_for_management(db: Session, now: datetime) -> list[PendingReview]:
    """Resolve the live queues without changing the saved reviewer or review permissions."""
    rows = db.query(
        RecognitionRecord,
        select(RecognitionInboxItem.recognition_id).where(
            RecognitionInboxItem.recognition_id == RecognitionRecord.id
        ).exists(),
    ).options(load_only(
        RecognitionRecord.id, RecognitionRecord.employee_id, RecognitionRecord.employee_no,
        RecognitionRecord.employee_name, RecognitionRecord.home_attraction_name,
        RecognitionRecord.recognition_type_name, RecognitionRecord.recognition_date,
        RecognitionRecord.submitted_at, RecognitionRecord.source,
        RecognitionRecord.employee_role_code_snapshot, RecognitionRecord.employee_acting_duty_code,
    )).filter(RecognitionRecord.status == "pending").order_by(
        RecognitionRecord.submitted_at, RecognitionRecord.id
    ).all()
    if not rows:
        return []

    today = now.date().isoformat()
    member_groups: dict[int, tuple[int, str]] = {}
    for employee_id, group_id, name in db.query(GroupMembership.employee_id, WorkGroup.id, WorkGroup.name).join(
        WorkGroup, WorkGroup.id == GroupMembership.group_id
    ).filter(
        GroupMembership.employee_id.in_({row.employee_id for row, _ in rows}),
        GroupMembership.status == "active", GroupMembership.starts_on <= today,
        or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= today),
    ).order_by(GroupMembership.starts_on.desc(), GroupMembership.id.desc()):
        member_groups.setdefault(employee_id, (group_id, name))

    leaders = active_group_leaders_bulk(db, {group_id for group_id, _ in member_groups.values()}, today)
    individual_ids = {}
    for row, inbox in rows:
        if inbox or row.employee_id in individual_ids:
            continue
        group_id, _ = member_groups.get(row.employee_id, (None, "未分组"))
        leader = leaders.get(group_id)
        if leader and leader.leader_employee_id == row.employee_id:
            leader = group_leader_of_type(db, group_id, "formal", today)
        if leader and leader.leader_employee_id != row.employee_id:
            individual_ids[row.employee_id] = leader.leader_employee_id

    have_shared = any(supervisor_self_review(row) for row, _ in rows)
    accounts = db.query(Employee).join(UserAccount, UserAccount.employee_id == Employee.id).filter(
        Employee.is_active.is_(True), UserAccount.enabled.is_(True)
    )
    if not have_shared:
        accounts = accounts.filter(Employee.id.in_(set(individual_ids.values())))
    people = {employee.id: employee for employee in accounts}
    base_roles = base_roles_at(db, set(people), today)
    duties = duties_at_bulk(db, set(people), today)
    roles = {
        employee_id: [role, *duties.get(employee_id, [])]
        for employee_id, role in base_roles.items() if role
    }
    permissions = {role_id: role_permissions(db, role_id) for role_id in {
        role.id for current in roles.values() for role in current
    }}
    direct = {
        employee_id for employee_id, current in roles.items()
        if any("REVIEW_DIRECT" in permissions[role.id] for role in current)
    }
    shared = {"GSM": set(), "AM": set()}
    if have_shared:
        for employee_id, current in roles.items():
            if any("REVIEW_SUPERVISOR" in permissions[role.id] for role in current):
                for role in current:
                    if role.code in shared:
                        shared[role.code].add(employee_id)

    result = []
    for row, inbox in rows:
        group_name = member_groups.get(row.employee_id, (None, "未分组"))[1]
        entry = PendingReview(row, group_name)
        if supervisor_self_review(row):
            eligible = shared["GSM"] | shared["AM"] if row.employee_acting_duty_code == "TA_GSM" else shared["GSM"]
            if eligible - {row.employee_id}:
                entry.shared_queue = shared_review_label(row)
        if not inbox and not entry.shared_queue and (reviewer_id := individual_ids.get(row.employee_id)) in direct:
            entry.reviewer = people[reviewer_id]
        result.append(entry)
    return result
