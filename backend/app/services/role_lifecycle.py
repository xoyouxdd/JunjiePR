"""Role expiry orchestration, reviewer handoff and deduplicated alerts.

Expiry processing preserves its existing flush/commit boundary and the
existing database management-scope synchronization strategy.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.role_constants import DUTY_ROLE_CODES
from app.services.identity import base_role_at, can_lead_on, role_at
from app.services.organization import active_group_leader, formal_leader_eligible
from app.services.management_scopes import synchronize_gsm_management_scope
from app.v2_models import (
    EmployeeActingDuty, EmployeeRoleAssignment, GroupLeaderAssignment,
    RecognitionRecord, SystemAlert,
)


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
