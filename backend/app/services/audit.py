"""Audit snapshots and entity-circle routing; callers own the transaction."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.v2_models import (
    AuditLog, Employee, EmployeeActingDuty, EmployeeLOAPeriod,
    RecognitionRecord, SickLeaveRecord,
)


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
    if entity_type == "rotation_account":
        from app.rotation.models import RotationAccount

        row = db.get(RotationAccount, key)
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
