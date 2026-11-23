"""Single-record upgrade rules and matching for declaration intake."""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.deduction_materials import duplicate_deduction_material_id
from app.deduction_policy import DECISION_DATED_LEVEL_CODES, GSM_CORRECTION_LEVEL_CODES, deduction_level_label
from app.idempotency import submission_payload_digest
from app.month_closure import ensure_month_open
from app.services.attendance import loa_excludes_month
from app.services.identity import primary_role_assignment_at
from app.services.scoring_policies import stored_deduction_points
from app.v2_auth import V2User
from app.v2_models import (
    DeductionLevel, DeductionOriginalUpgrade, DeductionRecord,
    DeductionUpgradeRequest, Employee, EmployeeMonthOrganizationSnapshot, StoredFile,
)


def can_upgrade_original(db: Session, user: V2User) -> bool:
    if (user.role.code != "GSM" or user.base_role.code != "GSM" or "DEDUCTION_ALL" not in user.permissions
            or user.rotation_temporary or user.rotation_identity_temporary):
        return False
    assignment = primary_role_assignment_at(db, user.id)
    return assignment is None or assignment.assignment_type == "permanent"


def related_pair_work(db: Session, record_id: int) -> bool:
    return db.query(DeductionUpgradeRequest.id).filter(or_(
        DeductionUpgradeRequest.first_deduction_id == record_id,
        DeductionUpgradeRequest.second_deduction_id == record_id,
        DeductionUpgradeRequest.result_deduction_id == record_id,
    )).first() is not None


def require_gsm_standalone_correction(db: Session, row: DeductionRecord, user: V2User) -> None:
    if row.employee_id == user.id:
        raise HTTPException(403, "正式GSM不能更正、作废或还原本人的扣分记录")
    level = db.get(DeductionLevel, row.deduction_level_id)
    if not level or level.code not in GSM_CORRECTION_LEVEL_CODES:
        raise HTTPException(403, "正式GSM仅可更正声明、备忘录或一级警告")
    if db.get(DeductionOriginalUpgrade, row.id):
        raise HTTPException(409, "该记录已原单升级，不能通过普通更正操作")


def restore_event_check(db: Session, row: DeductionRecord) -> dict:
    if row.voided_at is None:
        raise HTTPException(409, "缺少可核实的作废时间，不能直接还原")
    incident = row.incident_on or row.occurred_on
    material = db.get(StoredFile, row.document_file_id)
    duplicate_id = duplicate_deduction_material_id(db, row, material)
    if duplicate_id is not None:
        raise HTTPException(409, f"同一事件已有记录 #{duplicate_id}，不能还原这条作废记录")
    matches = db.query(DeductionRecord).filter(
        DeductionRecord.id != row.id, DeductionRecord.employee_id == row.employee_id,
        DeductionRecord.status != "void",
        func.coalesce(DeductionRecord.incident_on, DeductionRecord.occurred_on) == incident,
    ).order_by(DeductionRecord.id).all()
    records = [event_record_summary(candidate) for candidate in matches]
    return {"records": records, "confirmation": submission_payload_digest({"id": row.id, "records": records})}


def event_record_summary(row: DeductionRecord) -> dict:
    return {
        "id": row.id, "incident_on": row.incident_on or row.occurred_on, "month": row.deduction_month,
        "type_name": row.deduction_type_name, "level_name": row.deduction_level_name,
        "status": row.status, "description": row.description,
    }


def checked_original_reference(db: Session, employee_id: int, original_statement_id: int) -> DeductionRecord:
    row = db.get(DeductionRecord, original_statement_id)
    if row is None or row.employee_id != employee_id:
        raise HTTPException(400, "原声明编号与选中的员工不匹配，请核对编号和员工。")
    return row


def reject_original_reference(db: Session, employee_id: int, original_statement_id: int | None) -> None:
    if original_statement_id is not None:
        row = checked_original_reference(db, employee_id, original_statement_id)
        raise HTTPException(409, {
            "code": "SAME_DEDUCTION_EVENT",
            "message": "这是已登记的事件，请打开原单处理。",
            "record": event_record_summary(row),
        })


def deduction_event_check(db: Session, employee_id: int, incident_on: str) -> dict:
    rows = db.query(DeductionRecord).filter(
        DeductionRecord.employee_id == employee_id,
        func.coalesce(DeductionRecord.incident_on, DeductionRecord.occurred_on) == incident_on,
        DeductionRecord.status != "void",
    ).order_by(DeductionRecord.id.desc()).all()
    records = [event_record_summary(row) for row in rows]
    return {
        "records": records,
        "confirmation": submission_payload_digest({"employee_id": employee_id, "incident_on": incident_on, "records": records}),
    }


def verify_deduction_event_check(
    db: Session, employee_id: int, incident_on: str, confirmation: str | None, reason: str,
) -> None:
    if confirmation is None:
        return
    checked = deduction_event_check(db, employee_id, incident_on)
    if confirmation != checked["confirmation"] or (checked["records"] and not reason):
        raise HTTPException(409, "原单核对结果已变化，请再次提交，核对现有记录。")


def check_original_upgrade(db: Session, user: V2User, record_id: int, effective_day: date) -> DeductionRecord:
    if not can_upgrade_original(db, user):
        raise HTTPException(403, "仅正式GSM可以办理原单升级")
    row = db.get(DeductionRecord, record_id, populate_existing=True)
    if row is None:
        raise HTTPException(404, "声明记录不存在")
    if db.get(DeductionOriginalUpgrade, record_id):
        raise HTTPException(409, "这条声明已经原单升级，请刷新查看结果")
    level = db.get(DeductionLevel, row.deduction_level_id)
    material = db.get(StoredFile, row.document_file_id)
    if (row.status != "active" or not level or level.code != "STATEMENT"
            or row.material_status != "ready" or not material or material.status != "active"):
        raise HTTPException(409, "仅已生效且材料就绪的普通声明可以原单升级")
    if row.upgrade_request_id or row.upgrade_role or related_pair_work(db, record_id):
        raise HTTPException(409, "该记录已关联声明升级工单，请通过原工单处理")
    effective_on = effective_day.isoformat()
    if effective_on < (row.incident_on or row.occurred_on):
        raise HTTPException(400, "生效日不得早于原事件日期")
    ensure_deduction_months_open(db, row, (row.deduction_month, effective_on[:7]), "原单升级")
    return row


def ensure_deduction_months_open(db: Session, row: DeductionRecord, months: tuple[str, ...], operation: str) -> None:
    employee = db.get(Employee, row.employee_id)
    current_circle = employee.attraction_id if employee else None
    record_circle = row.attraction_id_snapshot or current_circle
    months = tuple(dict.fromkeys(months))
    score_circles = dict(db.query(EmployeeMonthOrganizationSnapshot.score_month, EmployeeMonthOrganizationSnapshot.attraction_id)
                         .filter_by(employee_id=row.employee_id)
                         .filter(EmployeeMonthOrganizationSnapshot.score_month.in_(months)).all())
    for month in months:
        ensure_month_open(db, month, record_circle, operation)
        score_circle = score_circles.get(month, current_circle)
        if score_circle is not None and score_circle != record_circle:
            ensure_month_open(db, month, score_circle, operation)


def original_upgrade_preview(db: Session, row: DeductionRecord, effective_day: date) -> dict:
    effective_on = effective_day.isoformat()
    effective_month = effective_on[:7]
    excluded = loa_excludes_month(db, row.employee_id, effective_month)
    levels = db.query(DeductionLevel).filter(
        DeductionLevel.code.in_(DECISION_DATED_LEVEL_CODES), DeductionLevel.active.is_(True),
    ).order_by(DeductionLevel.points, DeductionLevel.id).all()
    options = []
    for level in levels:
        points = float(stored_deduction_points(db, row, level))
        options.append({"id": level.id, "name": deduction_level_label(level, row.employee_type_snapshot),
                        "points": points, "actual_points": 0 if excluded else points})
    return {
        "record": event_record_summary(row),
        "original_points": 0 if loa_excludes_month(db, row.employee_id, row.deduction_month) else float(row.points),
        "effective_on": effective_on, "effective_month": effective_month, "loa_excluded": excluded, "levels": options,
    }
