"""Sick-leave registration endpoints."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.v2_auth import V2User, require_permissions
from app.v2_database import get_db
from app.v2_models import DeductionLevel, DeductionUpgradeRequest, DeductionRecord, DeductionType, Employee, SickLeaveRecord, StoredFile, UserAccount
from app.v2_services import current_group_for_employee, ensure_month_attendance, recalculate_attendance, remove_upload_file, role_at, save_upload, write_audit
from app.deduction_materials import PDF_HIGH_QUALITY_OPTIMIZATION_THRESHOLD, create_pdf_placeholder, queue_photo_material_job, stage_pdf_material, stage_photo_materials
from app.routers._shared import (
    UPGRADE_REVIEWER_CODES,
    client_ip,
    deduction_payload,
    ensure_deduction_type_allowed,
    ensure_enabled_frontline_target,
    ensure_month_open,
    ensure_operational_target_scope,
    ensure_scoped_hr_employee,
    existing_submission,
    invalidate_data_caches,
    normalize_request_key,
    parse_iso_date,
    pending_material_conflict,
    raise_pending_material_conflict,
    remember_submission,
    sick_leave_payloads,
    statement_upgrade_candidate,
    statement_upgrade_reviewer_options,
    submission_payload_digest,
    void_operator_snapshot,
)

router = APIRouter()


def sick_leave_overlap_payload(rows: list[SickLeaveRecord]) -> list[dict]:
    return [
        {
            "id": row.id,
            "leave_start_date": row.leave_start_date,
            "leave_end_date": row.leave_end_date,
            "leave_days": float(row.leave_days),
            "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for row in rows
    ]


def active_sick_leave_overlaps(db: Session, employee_id: int, start_date: str, end_date: str) -> list[SickLeaveRecord]:
    """Return any active absence that intersects an inclusive date range."""
    return (
        db.query(SickLeaveRecord)
        .filter(
            SickLeaveRecord.employee_id == employee_id,
            SickLeaveRecord.status == "active",
            SickLeaveRecord.leave_start_date <= end_date,
            SickLeaveRecord.leave_end_date >= start_date,
        )
        .order_by(SickLeaveRecord.leave_start_date, SickLeaveRecord.id)
        .all()
    )


def sick_leave_overlap_detail(rows: list[SickLeaveRecord]) -> dict:
    records = sick_leave_overlap_payload(rows)
    first = records[0]
    return {
        "code": "SICK_LEAVE_DATE_OVERLAP",
        "message": f"该员工已存在 {first['leave_start_date']} 至 {first['leave_end_date']} 的缺勤登记；日期有交集，不能重复提交。请先作废或更正原记录后再登记。",
        "records": records,
    }


@router.get("/sick-leaves/overlap-check")
def check_sick_leave_overlap(
    employee_id: int,
    leave_start_date: str,
    leave_end_date: str,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("SICK_REGISTER")),
):
    start = parse_iso_date(leave_start_date, "病假开始日期")
    end = parse_iso_date(leave_end_date, "病假结束日期")
    if end < start or start.strftime("%Y-%m") != end.strftime("%Y-%m"):
        raise HTTPException(400, "病假日期无效，跨月请分开登记")
    target, _ = ensure_enabled_frontline_target(db, employee_id, "病假")
    ensure_scoped_hr_employee(db, user, target)
    ensure_operational_target_scope(user, target)
    rows = active_sick_leave_overlaps(db, target.id, leave_start_date, leave_end_date)
    return {"conflict": bool(rows), "detail": sick_leave_overlap_detail(rows) if rows else None}


@router.get("/sick-leaves/violation-upgrade-preview")
def sick_leave_violation_upgrade_preview(
    employee_id: int,
    leave_start_date: str,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("SICK_REGISTER")),
):
    """Preview the statement-upgrade path used by the absence-page shortcut."""
    if not ({"DEDUCTION_DIRECT", "DEDUCTION_ALL"} & user.permissions):
        raise HTTPException(403, "当前账号没有登记违规病假声明的权限")
    parse_iso_date(leave_start_date, "病假开始日期")
    target, _ = ensure_enabled_frontline_target(db, employee_id, "违规病假")
    ensure_scoped_hr_employee(db, user, target)
    ensure_operational_target_scope(user, target)
    deduction_type = db.query(DeductionType).filter_by(code="SICK_LEAVE_VIOLATION", active=True).first()
    if not deduction_type:
        raise HTTPException(409, "违规病假声明配置缺失，请联系管理员")
    first = statement_upgrade_candidate(db, target.id, deduction_type.id, leave_start_date)
    return {
        "eligible": bool(first),
        "first_record": deduction_payload(first) if first else None,
        "reviewers": statement_upgrade_reviewer_options(db) if first else [],
    }


@router.post("/sick-leaves")
async def create_sick_leave(
    request: Request,
    employee_id: int = Form(...),
    leave_start_date: str = Form(...),
    leave_end_date: str = Form(...),
    leave_days: str | None = Form(None),
    rest_day_confirmed: bool = Form(False),
    note: str | None = Form(None),
    is_violation: bool = Form(False),
    violation_reviewer_id: int | None = Form(None),
    idempotency_key: str | None = Form(None),
    proof: UploadFile = File(...),
    violation_document: UploadFile | None = File(None),
    violation_document_images: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("SICK_REGISTER")),
):
    if not str(employee_id or "").strip():
        raise HTTPException(400, "请先通过搜索结果选择缺勤员工")
    request_key = normalize_request_key(idempotency_key)
    payload_digest = submission_payload_digest(
        {
            "employee_id": int(employee_id),
            "leave_start_date": str(leave_start_date or "").strip(),
            "leave_end_date": str(leave_end_date or "").strip(),
            "leave_days": str(leave_days or "").strip(),
            "rest_day_confirmed": bool(rest_day_confirmed),
            "note": str(note or "").strip(),
            "is_violation": bool(is_violation),
        }
    )
    duplicate_row = existing_submission(db, user.id, "sick_leave", request_key, SickLeaveRecord, payload_digest)
    if duplicate_row:
        attendance = ensure_month_attendance(db, duplicate_row.attendance_month, {duplicate_row.employee_id})[duplicate_row.employee_id]
        return {"ok": True, "attendance_score": float(attendance.final_score), "duplicate": True}
    start = parse_iso_date(leave_start_date, "病假开始日期")
    end = parse_iso_date(leave_end_date, "病假结束日期")
    if end < start or start.strftime("%Y-%m") != end.strftime("%Y-%m"):
        raise HTTPException(400, "病假日期无效，跨月请分开登记")
    if end > start and not rest_day_confirmed:
        raise HTTPException(
            409,
            detail={
                "code": "SICK_LEAVE_REST_DAY_CONFIRMATION_REQUIRED",
                "message": "请确认您所提交的病假日期中不含演职人员本休。",
            },
        )
    calendar_days = Decimal((end - start).days + 1)
    try:
        days = Decimal(leave_days) if leave_days not in (None, "") else calendar_days
    except InvalidOperation as exc:
        raise HTTPException(400, "病假天数必须是数字") from exc
    if days <= 0 or days * 2 != (days * 2).to_integral_value():
        raise HTTPException(400, "病假天数必须按0.5天递增")
    if days > calendar_days:
        raise HTTPException(400, "病假天数不能超过日期范围")
    target, target_role = ensure_enabled_frontline_target(db, employee_id, "病假")
    ensure_scoped_hr_employee(db, user, target)
    ensure_operational_target_scope(user, target)
    ensure_month_open(db, start.strftime("%Y-%m"), target.attraction_id, "新增病假")
    overlaps = active_sick_leave_overlaps(db, target.id, leave_start_date, leave_end_date)
    if overlaps:
        raise HTTPException(409, detail=sick_leave_overlap_detail(overlaps))
    if not proof.filename:
        raise HTTPException(400, "缺勤证明未上传，请重新选择图片或PDF文件")
    violation_type = statement = first_statement = reviewer = None
    violation_photo_mode = bool([item for item in (violation_document_images or []) if item and item.filename])
    violation_pdf_mode = bool(violation_document and violation_document.filename)
    if violation_pdf_mode and violation_photo_mode:
        raise HTTPException(400, "违规病假声明请在上传PDF和照片材料中选择一种方式")
    if is_violation:
        if not ({"DEDUCTION_DIRECT", "DEDUCTION_ALL"} & user.permissions):
            raise HTTPException(403, "当前账号没有登记违规病假声明的权限")
        violation_type = db.query(DeductionType).filter_by(code="SICK_LEAVE_VIOLATION", active=True).first()
        statement = db.query(DeductionLevel).filter_by(code="STATEMENT", active=True).first()
        if not violation_type or not statement:
            raise HTTPException(409, "违规病假声明配置缺失，请联系管理员")
        ensure_deduction_type_allowed(user, violation_type)
        existing_pending_material = pending_material_conflict(
            db,
            employee_id=target.id,
            deduction_type_id=violation_type.id,
            deduction_level_id=statement.id,
            occurred_on=leave_start_date,
        )
        if existing_pending_material:
            raise_pending_material_conflict(existing_pending_material)
        first_statement = statement_upgrade_candidate(db, target.id, violation_type.id, leave_start_date)
        if first_statement:
            reviewer = db.get(Employee, violation_reviewer_id) if violation_reviewer_id else None
            reviewer_role = role_at(db, reviewer.id) if reviewer else None
            reviewer_account = db.query(UserAccount).filter_by(employee_id=reviewer.id, enabled=True).first() if reviewer else None
            if not reviewer or not reviewer_role or reviewer_role.code not in UPGRADE_REVIEWER_CODES or not reviewer.is_active or not reviewer_account:
                raise HTTPException(400, "检测到3个月内可升级的违规病假声明，请选择在职且账号启用的GSM或TA GSM审核")
    file_row = violation_file_row = None
    violation_source_rows: list[StoredFile] = []
    try:
        file_row = await save_upload(db, proof, user.id, allowed_extensions={".jpg", ".jpeg", ".png", ".heic", ".webp", ".pdf"})
        row = SickLeaveRecord(
            employee_id=target.id,
            employee_no_snapshot=target.employee_no,
            employee_name_snapshot=target.name,
            employee_role_snapshot=target_role.name,
            attraction_id_snapshot=target.attraction_id,
            attendance_month=start.strftime("%Y-%m"),
            leave_start_date=leave_start_date,
            leave_end_date=leave_end_date,
            leave_days=days,
            charged_days=days,
            proof_file_id=file_row.id,
            note=(note or "").strip() or None,
            status="active",
            submitted_by=user.id,
            submitted_by_name=user.name,
            is_violation=bool(is_violation),
        )
        db.add(row)
        db.flush()
        if is_violation:
            violation_needs_processing = False
            if violation_photo_mode:
                violation_source_rows = await stage_photo_materials(db, list(violation_document_images or []), user.id)
                violation_file_row = create_pdf_placeholder(db, user.id)
            elif violation_pdf_mode:
                violation_file_row = await stage_pdf_material(db, violation_document, user.id)
                violation_needs_processing = violation_file_row.file_size > PDF_HIGH_QUALITY_OPTIMIZATION_THRESHOLD
                if violation_needs_processing:
                    violation_file_row.status = "processing_source"
                    violation_source_rows = [violation_file_row]
                    violation_file_row = create_pdf_placeholder(db, user.id)
                else:
                    violation_file_row.status = "active"
            else:
                violation_file_row = create_pdf_placeholder(db, user.id)
                violation_file_row.original_filename = "待补充违规病假声明材料.pdf"
                violation_file_row.status = "pending_material"
            group = current_group_for_employee(db, target.id)
            material_processing = violation_photo_mode or violation_needs_processing
            awaiting_material = not violation_photo_mode and not violation_pdf_mode
            violation = DeductionRecord(
                employee_id=target.id, employee_no=target.employee_no, employee_name=target.name,
                employee_role_snapshot=target_role.name, employee_group_id_snapshot=group.id if group else None,
                attraction_id_snapshot=target.attraction_id, deduction_type_id=violation_type.id,
                deduction_type_name=violation_type.name, deduction_level_id=statement.id,
                deduction_level_name=statement.name, points=Decimal("0") if first_statement else statement.points, occurred_on=leave_start_date,
                deduction_month=leave_start_date[:7], description=f"违规病假（关联缺勤：{leave_start_date} 至 {leave_end_date}）：" + ((note or "").strip() or "待补充处理说明"),
                document_file_id=violation_file_row.id, submitter_id=user.id, submitter_name=user.name,
                submitter_role_snapshot=user.role.name, permission_scope_snapshot="缺勤登记违规病假声明",
                status=("material_processing" if material_processing else ("pending_upgrade" if first_statement and not awaiting_material else ("active" if violation_pdf_mode else "pending_material"))),
                material_status="processing" if material_processing else ("ready" if violation_pdf_mode else "missing"),
                material_source_type="photos" if violation_photo_mode else ("pdf" if violation_pdf_mode else "later"),
                upgrade_role="source_second" if first_statement else None,
                upgrade_state=("material_processing" if material_processing else ("awaiting_material" if first_statement and awaiting_material else ("pending" if first_statement else None))),
            )
            db.add(violation)
            db.flush()
            row.violation_deduction_id = violation.id
            if first_statement:
                claimed = db.execute(
                    update(DeductionRecord)
                    .where(
                        DeductionRecord.id == first_statement.id,
                        DeductionRecord.status == "active",
                        or_(DeductionRecord.upgrade_state.is_(None), DeductionRecord.upgrade_state == "eligible"),
                    )
                    .values(
                        upgrade_role="source_first",
                        upgrade_state="awaiting_material" if awaiting_material else ("material_processing" if material_processing else "pending"),
                    )
                    .execution_options(synchronize_session=False)
                )
                if claimed.rowcount != 1:
                    raise HTTPException(409, "该历史违规病假声明已被其他升级工单使用，请刷新后重试")
                upgrade = DeductionUpgradeRequest(
                    employee_id=target.id, deduction_type_id=violation_type.id,
                    first_deduction_id=first_statement.id, second_deduction_id=violation.id,
                    reviewer_id=reviewer.id, reviewer_name=reviewer.name,
                    submitted_by=user.id, submitted_by_name=user.name,
                    status="awaiting_material" if (awaiting_material or material_processing) else "pending",
                )
                db.add(upgrade)
                db.flush()
                first_statement.upgrade_request_id = upgrade.id
                violation.upgrade_request_id = upgrade.id
                if material_processing:
                    queue_photo_material_job(
                        db, violation, violation_file_row, violation_source_rows,
                        mode="upgrade_pdf_compress" if violation_needs_processing else "upgrade",
                        reviewer_id=reviewer.id, first_deduction_id=first_statement.id,
                    )
            elif material_processing:
                queue_photo_material_job(
                    db, violation, violation_file_row, violation_source_rows,
                    mode="pdf_compress" if violation_needs_processing else "deduction",
                )
        remember_submission(db, user.id, "sick_leave", request_key, row.id, payload_digest)
        attendance = recalculate_attendance(db, target, row.attendance_month)
        write_audit(db, user.employee, "登记病假", "sick_leave", row.id, after={"employee": target.employee_no, "days": float(days), "score": float(attendance.final_score), "is_violation": bool(is_violation), "violation_deduction_id": row.violation_deduction_id}, ip_address=client_ip(request))
        db.commit()
    except IntegrityError:
        db.rollback()
        remove_upload_file(file_row)
        remove_upload_file(violation_file_row)
        for source_row in violation_source_rows:
            remove_upload_file(source_row)
        duplicate_row = existing_submission(db, user.id, "sick_leave", request_key, SickLeaveRecord, payload_digest)
        if duplicate_row:
            attendance = ensure_month_attendance(db, duplicate_row.attendance_month, {duplicate_row.employee_id})[duplicate_row.employee_id]
            return {"ok": True, "attendance_score": float(attendance.final_score), "duplicate": True}
        raise
    except Exception:
        db.rollback()
        remove_upload_file(file_row)
        remove_upload_file(violation_file_row)
        for source_row in violation_source_rows:
            remove_upload_file(source_row)
        raise
    invalidate_data_caches()
    return {
        "ok": True,
        "attendance_score": float(attendance.final_score),
        "violation": deduction_payload(violation) if is_violation else None,
        "violation_upgrade": bool(first_statement),
    }


@router.get("/sick-leaves")
def list_sick_leaves(month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SICK_REGISTER"))):
    query = db.query(SickLeaveRecord).filter(SickLeaveRecord.submitted_by == user.id)
    if month:
        query = query.filter(SickLeaveRecord.attendance_month == month)
    rows = query.order_by(SickLeaveRecord.submitted_at.desc()).limit(500).all()
    return sick_leave_payloads(db, rows)


@router.post("/sick-leaves/{record_id}/void")
def void_sick_leave(record_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SICK_REGISTER"))):
    row = db.get(SickLeaveRecord, record_id)
    if not row or row.submitted_by != user.id:
        raise HTTPException(404, "只能作废自己登记的病假")
    if row.status != "active":
        raise HTTPException(400, "病假已经作废")
    employee = db.get(Employee, row.employee_id)
    ensure_month_open(db, row.attendance_month, row.attraction_id_snapshot or (employee.attraction_id if employee else None), "作废病假")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "作废原因必填")
    role_code, role_name, permission_scope = void_operator_snapshot(db, user)
    row.voided_from_status = row.status
    row.status = "void"
    row.voided_by = user.id
    row.voided_by_name = user.name
    row.voided_by_role_code = role_code
    row.voided_by_role_name = role_name
    row.void_permission_scope_snapshot = permission_scope
    row.voided_at = datetime.now()
    row.void_reason = reason
    db.flush()
    attendance = recalculate_attendance(db, db.get(Employee, row.employee_id), row.attendance_month)
    write_audit(db, user.employee, "作废病假", "sick_leave", row.id, after={"score": float(attendance.final_score)}, reason=reason, ip_address=client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "attendance_score": float(attendance.final_score)}
