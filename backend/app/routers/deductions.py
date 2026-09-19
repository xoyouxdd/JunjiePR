"""Deduction entry, upgrade and target endpoints."""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, DeductionLevel, DeductionFollowUp, DeductionMaterialJob, DeductionUpgradeRequest, DeductionUpgradeTransfer, DeductionRecord, DeductionType, Employee, StoredFile, UserAccount
from app.v2_services import LEADER_CODES, current_group_for_employee, remove_upload_file, role_at, write_audit
from app.deduction_materials import PDF_HIGH_QUALITY_OPTIMIZATION_THRESHOLD, create_pdf_placeholder, queue_photo_material_job, stage_pdf_material, stage_photo_materials
from app.routers._shared import (
    DEDUCTION_LEVEL_ORDER,
    MATERIAL_COLLABORATOR_CODES,
    NEXT_DEDUCTION_LEVEL,
    REPEAT_CONTROLLED_DEDUCTION_CODES,
    UPGRADE_REVIEWER_CODES,
    client_ip,
    deduction_follow_up_payload,
    deduction_payload,
    direct_only_deduction_user,
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
    scoped_hr_attraction_ids,
    search_employee_targets,
    statement_upgrade_candidate,
    statement_upgrade_reviewer_options,
    submission_payload_digest,
    subtract_calendar_months,
    void_operator_snapshot,
)

router = APIRouter()


def ensure_repeat_follow_up(
    db: Session,
    user: V2User,
    employee: Employee,
    deduction_type: DeductionType,
    occurred_on: str,
    repeat_context: dict,
) -> DeductionFollowUp | None:
    if not repeat_context.get("has_repeat") or user.role.code not in LEADER_CODES:
        return None
    row = db.query(DeductionFollowUp).filter_by(
        supervisor_id=user.id,
        employee_id=employee.id,
        deduction_type_id=deduction_type.id,
        occurred_on=occurred_on,
    ).first()
    if row:
        return row
    row = DeductionFollowUp(
        supervisor_id=user.id,
        supervisor_name=user.name,
        employee_id=employee.id,
        employee_no=employee.employee_no,
        employee_name=employee.name,
        deduction_type_id=deduction_type.id,
        deduction_type_name=deduction_type.name,
        occurred_on=occurred_on,
        previous_record_ids=json.dumps([item["id"] for item in repeat_context["previous_records"]]),
        status="pending",
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        user.employee,
        "生成重复处分跟进",
        "deduction_follow_up",
        row.id,
        after=deduction_follow_up_payload(row),
    )
    return row


def attendance_repeat_context(db: Session, employee_id: int, deduction_type: DeductionType, occurred_on: str, user: V2User) -> dict:
    if deduction_type.code not in REPEAT_CONTROLLED_DEDUCTION_CODES:
        return {"has_repeat": False, "blocked": False, "requires_confirmation": False, "previous_records": []}
    event_date = parse_iso_date(occurred_on, "事件日期")
    window_start = subtract_calendar_months(event_date, 3).isoformat()
    rows = (
        db.query(DeductionRecord)
        .filter(
            DeductionRecord.employee_id == employee_id,
            DeductionRecord.deduction_type_id == deduction_type.id,
            DeductionRecord.status == "active",
            DeductionRecord.legacy_upgrade_excluded.is_(False),
            DeductionRecord.occurred_on >= window_start,
            DeductionRecord.occurred_on <= occurred_on,
        )
        .order_by(DeductionRecord.occurred_on.desc(), DeductionRecord.id.desc())
        .all()
    )
    if not rows:
        return {
            "has_repeat": False,
            "blocked": False,
            "requires_confirmation": False,
            "window_start": window_start,
            "previous_records": [],
        }
    highest = max(rows, key=lambda row: DEDUCTION_LEVEL_ORDER.get(db.get(DeductionLevel, row.deduction_level_id).code, 0))
    highest_level = db.get(DeductionLevel, highest.deduction_level_id)
    minimum_code = NEXT_DEDUCTION_LEVEL.get(highest_level.code, "MEMO")
    minimum_level = db.query(DeductionLevel).filter(DeductionLevel.code == minimum_code, DeductionLevel.active.is_(True)).one()
    direct_only = direct_only_deduction_user(user)
    history = [
        {
            "id": row.id,
            "occurred_on": row.occurred_on,
            "deduction_type": row.deduction_type_name,
            "deduction_level": row.deduction_level_name,
            "points": float(row.points),
        }
        for row in rows
    ]
    category_name = "违规病假" if deduction_type.code == "SICK_LEAVE_VIOLATION" else "考勤"
    message = (
        f"此员工在3个月内已有同类{category_name}登记，本次需要升级为备忘录或警告，"
        + ("TA主管/主管不能继续登记，请由GSM登记。" if direct_only else f"本次最低处分等级为{minimum_level.name}，请确认历史记录后继续登记。")
    )
    return {
        "has_repeat": True,
        "blocked": direct_only,
        "requires_confirmation": not direct_only,
        "window_start": window_start,
        "message": message,
        "minimum_level_id": minimum_level.id,
        "minimum_level_code": minimum_level.code,
        "minimum_level_name": minimum_level.name,
        "previous_records": history,
    }


def deduction_upgrade_payload(db: Session, row: DeductionUpgradeRequest, *, include_details: bool = True) -> dict:
    first = db.get(DeductionRecord, row.first_deduction_id)
    second = db.get(DeductionRecord, row.second_deduction_id)
    result = db.get(DeductionRecord, row.result_deduction_id) if row.result_deduction_id else None
    transfer_rows = db.query(DeductionUpgradeTransfer).filter_by(request_id=row.id).order_by(DeductionUpgradeTransfer.created_at.asc()).all()
    status_names = {"pending": "待审核", "approved": "已升级", "rejected": "审核不通过"}
    payload = {
        "id": row.id,
        "record_type": "deduction_upgrade",
        "employee_id": row.employee_id,
        "employee_name": first.employee_name if first else "",
        "employee_no": first.employee_no if first else "",
        "deduction_type": first.deduction_type_name if first else "",
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "reviewer_id": row.reviewer_id,
        "reviewer_name": row.reviewer_name,
        "submitted_by": row.submitted_by_name,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "handling_note": row.handling_note or "",
        "result_level": result.deduction_level_name if result else "",
        "result_points": float(result.points) if result else 0,
        "transfers": [{"from_name": item.from_reviewer_name, "to_name": item.to_reviewer_name, "reason": item.reason, "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S")} for item in transfer_rows],
    }
    if include_details:
        payload["first_record"] = deduction_payload(first) if first else None
        payload["second_record"] = deduction_payload(second) if second else None
    return payload


@router.get("/deduction-targets")
def deduction_targets(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if not ({"DEDUCTION_ALL", "DEDUCTION_DIRECT"} & user.permissions):
        raise HTTPException(403, "没有扣分权限")
    allowed = scoped_hr_attraction_ids(db, user)
    attraction_id = next(iter(allowed)) if allowed else None
    return search_employee_targets(db, attraction_id=attraction_id, limit=500)["items"]


@router.get("/deductions/attendance-repeat-check")
def check_attendance_repeat(
    employee_id: int,
    deduction_type_id: int,
    occurred_on: str,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    parse_iso_date(occurred_on, "事件日期")
    target, target_role = ensure_enabled_frontline_target(db, employee_id, "扣分")
    ensure_scoped_hr_employee(db, user, target)
    ensure_operational_target_scope(user, target)
    target_circle = db.get(Attraction, target.attraction_id) if target.attraction_id else None
    if not target_circle or not target_circle.active or not target_circle.employee_circle:
        raise HTTPException(400, "被扣分员工未配置有效景点圈")
    ensure_month_open(db, occurred_on[:7], target_circle.id, "生成重复处分跟进")
    if "DEDUCTION_ALL" in user.permissions:
        pass
    elif "DEDUCTION_DIRECT" in user.permissions:
        pass
    else:
        raise HTTPException(403, "没有扣分权限")
    deduction_type = db.get(DeductionType, deduction_type_id)
    if not deduction_type or not deduction_type.active:
        raise HTTPException(400, "扣分类型无效")
    ensure_deduction_type_allowed(user, deduction_type)
    result = attendance_repeat_context(db, employee_id, deduction_type, occurred_on, user)
    if result["has_repeat"] and user.role.code in LEADER_CODES:
        follow_up = ensure_repeat_follow_up(db, user, target, deduction_type, occurred_on, result)
        db.commit()
        result["follow_up_id"] = follow_up.id if follow_up else None
    result.update({"employee_id": target.id, "employee_name": target.name, "deduction_type_id": deduction_type.id, "deduction_type_name": deduction_type.name})
    return result


@router.post("/deductions")
async def create_deduction(
    request: Request,
    employee_id: int = Form(...),
    deduction_type_id: int = Form(...),
    deduction_level_id: int = Form(...),
    occurred_on: str = Form(...),
    description: str = Form(...),
    repeat_confirmed: bool = Form(False),
    idempotency_key: str | None = Form(None),
    document: UploadFile | None = File(None),
    document_images: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    request_key = normalize_request_key(idempotency_key)
    payload_digest = submission_payload_digest(
        {
            "employee_id": int(employee_id),
            "deduction_type_id": int(deduction_type_id),
            "deduction_level_id": int(deduction_level_id),
            "occurred_on": str(occurred_on or "").strip(),
            "description": str(description or "").strip(),
            "repeat_confirmed": bool(repeat_confirmed),
        }
    )
    duplicate_row = existing_submission(db, user.id, "deduction", request_key, DeductionRecord, payload_digest)
    if duplicate_row:
        return {"ok": True, "record": deduction_payload(duplicate_row), "duplicate": True}
    parse_iso_date(occurred_on, "事件日期")
    target, target_role = ensure_enabled_frontline_target(db, employee_id, "扣分")
    ensure_scoped_hr_employee(db, user, target)
    ensure_operational_target_scope(user, target)
    target_circle = db.get(Attraction, target.attraction_id) if target.attraction_id else None
    if not target_circle or not target_circle.active or not target_circle.employee_circle:
        raise HTTPException(400, "被扣分员工未配置有效景点圈")
    ensure_month_open(db, occurred_on[:7], target_circle.id, "新增扣分")
    if "DEDUCTION_ALL" in user.permissions:
        scope = "所有CM/TR"
    elif "DEDUCTION_DIRECT" in user.permissions:
        scope = "所有CM/TR（仅声明）"
    else:
        raise HTTPException(403, "没有扣分权限")
    level = db.get(DeductionLevel, deduction_level_id)
    deduction_type = db.get(DeductionType, deduction_type_id)
    if not level or not level.active or not deduction_type or not deduction_type.active:
        raise HTTPException(400, "扣分类型或等级无效")
    ensure_deduction_type_allowed(user, deduction_type)
    if "DEDUCTION_DIRECT" in user.permissions and "DEDUCTION_ALL" not in user.permissions and level.code != "STATEMENT":
        raise HTTPException(403, "TA主管、主管只能登记声明1分")
    existing_pending_material = pending_material_conflict(
        db,
        employee_id=target.id,
        deduction_type_id=deduction_type.id,
        deduction_level_id=level.id,
        occurred_on=occurred_on,
    )
    if existing_pending_material:
        raise_pending_material_conflict(existing_pending_material)
    photo_mode = bool([item for item in (document_images or []) if item and item.filename])
    pdf_mode = bool(document and document.filename)
    if direct_only_deduction_user(user) and level.code == "STATEMENT" and deduction_type.code in REPEAT_CONTROLLED_DEDUCTION_CODES and (photo_mode or pdf_mode):
        if statement_upgrade_candidate(db, employee_id, deduction_type.id, occurred_on):
            raise HTTPException(409, "三个月内已有可升级同类声明，请通过声明升级工单提交")
        repeat_context = {"has_repeat": False, "blocked": False, "requires_confirmation": False}
    elif photo_mode or pdf_mode:
        repeat_context = attendance_repeat_context(db, employee_id, deduction_type, occurred_on, user)
    else:
        repeat_context = {"has_repeat": False, "blocked": False, "requires_confirmation": False, "previous_records": []}
    if repeat_context["has_repeat"]:
        if repeat_context["blocked"]:
            raise HTTPException(409, detail={"code": "ATTENDANCE_REPEAT_BLOCKED", **repeat_context})
        if not repeat_confirmed:
            raise HTTPException(409, detail={"code": "ATTENDANCE_REPEAT_CONFIRMATION_REQUIRED", **repeat_context})
        minimum_rank = DEDUCTION_LEVEL_ORDER.get(repeat_context["minimum_level_code"], 0)
        if DEDUCTION_LEVEL_ORDER.get(level.code, 0) < minimum_rank:
            raise HTTPException(400, f"3个月内已有同类型处分，本次最低必须选择{repeat_context['minimum_level_name']}")
    description = description.strip()
    if not description:
        raise HTTPException(400, "事件说明必填")
    file_row = None
    source_rows: list[StoredFile] = []
    needs_pdf_processing = False
    if pdf_mode and photo_mode:
        raise HTTPException(400, "请在上传PDF和照片材料中选择一种方式")
    # A record without materials is a collaboration draft: it is visible in
    # supervisor to-dos but deliberately has no score and no repeat/escalation
    # effect until the material is supplied successfully.
    if not pdf_mode and not photo_mode:
        repeat_context = {"has_repeat": False, "blocked": False, "requires_confirmation": False, "previous_records": []}
    try:
        if photo_mode:
            source_rows = await stage_photo_materials(db, list(document_images or []), user.id)
            file_row = create_pdf_placeholder(db, user.id)
        elif pdf_mode:
            file_row = await stage_pdf_material(db, document, user.id)
            needs_pdf_processing = file_row.file_size > PDF_HIGH_QUALITY_OPTIMIZATION_THRESHOLD
            if needs_pdf_processing:
                file_row.status = "processing_source"
                source_rows = [file_row]
                file_row = create_pdf_placeholder(db, user.id)
            else:
                # A small, validated PDF is immediately usable.  Keep its
                # source row active instead of leaving an inaccessible
                # processing-source file behind.
                file_row.status = "active"
        else:
            file_row = create_pdf_placeholder(db, user.id)
            file_row.original_filename = "待补充声明材料.pdf"
            file_row.status = "pending_material"
        group = current_group_for_employee(db, target.id)
        row = DeductionRecord(
            employee_id=target.id,
            employee_no=target.employee_no,
            employee_name=target.name,
            employee_role_snapshot=target_role.name,
            employee_group_id_snapshot=group.id if group else None,
            attraction_id_snapshot=target.attraction_id,
            deduction_type_id=deduction_type.id,
            deduction_type_name=deduction_type.name,
            deduction_level_id=level.id,
            deduction_level_name=level.name,
            points=level.points,
            occurred_on=occurred_on,
            deduction_month=occurred_on[:7],
            description=description,
            document_file_id=file_row.id,
            submitter_id=user.id,
            submitter_name=user.name,
            submitter_role_snapshot=user.role.name,
            permission_scope_snapshot=scope,
            status="material_processing" if (photo_mode or needs_pdf_processing) else ("active" if pdf_mode else "pending_material"),
            material_status="processing" if (photo_mode or needs_pdf_processing) else ("ready" if pdf_mode else "missing"),
            material_source_type="photos" if photo_mode else ("pdf" if pdf_mode else "later"),
        )
        db.add(row)
        db.flush()
        if photo_mode or needs_pdf_processing:
            queue_photo_material_job(db, row, file_row, source_rows, mode="pdf_compress" if needs_pdf_processing else "deduction")
        elif level.code != "STATEMENT" and row.status == "active":
            follow_ups = db.query(DeductionFollowUp).filter_by(
                employee_id=target.id,
                deduction_type_id=deduction_type.id,
                occurred_on=occurred_on,
                status="pending",
            ).all()
            for follow_up in follow_ups:
                follow_up.status = "issued"
                follow_up.issued_deduction_id = row.id
                follow_up.issued_by = user.id
                follow_up.issued_by_name = user.name
                follow_up.issued_at = datetime.now()
        remember_submission(db, user.id, "deduction", request_key, row.id, payload_digest)
        audit_after = deduction_payload(row)
        if repeat_context["has_repeat"]:
            audit_after["repeat_warning_acknowledged"] = True
            audit_after["repeat_reference_ids"] = [item["id"] for item in repeat_context["previous_records"]]
            audit_after["minimum_level_name"] = repeat_context["minimum_level_name"]
        write_audit(db, user.employee, "登记扣分", "deduction", row.id, after=audit_after, ip_address=client_ip(request))
        db.commit()
    except IntegrityError:
        db.rollback()
        remove_upload_file(file_row)
        for source_row in source_rows:
            remove_upload_file(source_row)
        duplicate_row = existing_submission(db, user.id, "deduction", request_key, DeductionRecord, payload_digest)
        if duplicate_row:
            return {"ok": True, "record": deduction_payload(duplicate_row), "duplicate": True}
        raise
    except Exception:
        db.rollback()
        remove_upload_file(file_row)
        for source_row in source_rows:
            remove_upload_file(source_row)
        raise
    invalidate_data_caches()
    return {"ok": True, "record": deduction_payload(row)}


@router.get("/deduction-upgrades/preview")
def deduction_upgrade_preview(employee_id: int, deduction_type_id: int, occurred_on: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if not ({"DEDUCTION_DIRECT", "DEDUCTION_ALL"} & user.permissions):
        raise HTTPException(403, "没有声明升级登记权限")
    parse_iso_date(occurred_on, "事件日期")
    target, _role = ensure_enabled_frontline_target(db, employee_id, "扣分")
    deduction_type = db.get(DeductionType, deduction_type_id)
    statement = db.query(DeductionLevel).filter_by(code="STATEMENT", active=True).first()
    if deduction_type and statement:
        existing_pending_material = pending_material_conflict(
            db,
            employee_id=target.id,
            deduction_type_id=deduction_type.id,
            deduction_level_id=statement.id,
            occurred_on=occurred_on,
        )
        if existing_pending_material:
            raise_pending_material_conflict(existing_pending_material)
    if not deduction_type or not deduction_type.active or deduction_type.code not in REPEAT_CONTROLLED_DEDUCTION_CODES:
        return {"eligible": False}
    first = statement_upgrade_candidate(db, target.id, deduction_type.id, occurred_on)
    if not first:
        return {"eligible": False}
    return {"eligible": True, "first_record": deduction_payload(first), "reviewers": statement_upgrade_reviewer_options(db)}


@router.post("/deduction-upgrades")
async def create_deduction_upgrade(
    request: Request,
    employee_id: int = Form(...),
    deduction_type_id: int = Form(...),
    occurred_on: str = Form(...),
    description: str = Form(...),
    reviewer_id: int = Form(...),
    document: UploadFile | None = File(None),
    document_images: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("DEDUCTION_DIRECT")),
):
    parse_iso_date(occurred_on, "事件日期")
    description = description.strip()
    if not description:
        raise HTTPException(400, "事件说明必填")
    target, target_role = ensure_enabled_frontline_target(db, employee_id, "扣分")
    ensure_scoped_hr_employee(db, user, target)
    target_circle = db.get(Attraction, target.attraction_id) if target.attraction_id else None
    if not target_circle or not target_circle.active or not target_circle.employee_circle:
        raise HTTPException(400, "被扣分员工未配置有效景点圈")
    ensure_month_open(db, occurred_on[:7], target_circle.id, "提交声明升级工单")
    deduction_type = db.get(DeductionType, deduction_type_id)
    statement = db.query(DeductionLevel).filter_by(code="STATEMENT", active=True).first()
    reviewer = db.get(Employee, reviewer_id)
    reviewer_role = role_at(db, reviewer.id) if reviewer else None
    if not deduction_type or deduction_type.code not in REPEAT_CONTROLLED_DEDUCTION_CODES or not statement:
        raise HTTPException(400, "该扣分类型不支持声明升级")
    existing_pending_material = pending_material_conflict(
        db,
        employee_id=target.id,
        deduction_type_id=deduction_type.id,
        deduction_level_id=statement.id,
        occurred_on=occurred_on,
    )
    if existing_pending_material:
        raise_pending_material_conflict(existing_pending_material)
    if not reviewer or not reviewer.is_active or not reviewer_role or reviewer_role.code not in UPGRADE_REVIEWER_CODES:
        raise HTTPException(400, "请选择在职的GSM或TA GSM审核")
    reviewer_account = db.query(UserAccount).filter_by(employee_id=reviewer.id, enabled=True).first()
    if not reviewer_account:
        raise HTTPException(400, "所选审核人账号未启用")
    photo_mode = bool([item for item in (document_images or []) if item and item.filename])
    pdf_mode = bool(document and document.filename)
    if pdf_mode and photo_mode:
        raise HTTPException(400, "请在上传PDF和照片材料中选择一种方式")
    if not pdf_mode and not photo_mode:
        raise HTTPException(400, "请上传PDF，或拍照/从相册选择声明材料")
    first = statement_upgrade_candidate(db, target.id, deduction_type.id, occurred_on)
    if not first:
        raise HTTPException(409, "三个月内不存在可升级的同类声明，请按普通声明登记")
    claimed = db.execute(
        update(DeductionRecord)
        .where(DeductionRecord.id == first.id, DeductionRecord.status == "active", or_(DeductionRecord.upgrade_state.is_(None), DeductionRecord.upgrade_state == "eligible"))
        .values(upgrade_state="material_processing" if photo_mode else "pending", upgrade_role="source_first")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "该历史声明已被其他工单使用，请刷新后重试")
    file_row = None
    source_rows: list[StoredFile] = []
    needs_pdf_processing = False
    try:
        if photo_mode:
            source_rows = await stage_photo_materials(db, list(document_images or []), user.id)
            file_row = create_pdf_placeholder(db, user.id)
        else:
            file_row = await stage_pdf_material(db, document, user.id)
            needs_pdf_processing = file_row.file_size > PDF_HIGH_QUALITY_OPTIMIZATION_THRESHOLD
            if needs_pdf_processing:
                file_row.status = "processing_source"
                source_rows = [file_row]
                file_row = create_pdf_placeholder(db, user.id)
            else:
                file_row.status = "active"
        group = current_group_for_employee(db, target.id)
        second = DeductionRecord(
        employee_id=target.id, employee_no=target.employee_no, employee_name=target.name,
        employee_role_snapshot=target_role.name, employee_group_id_snapshot=group.id if group else None,
        attraction_id_snapshot=target.attraction_id, deduction_type_id=deduction_type.id,
        deduction_type_name=deduction_type.name, deduction_level_id=statement.id,
        deduction_level_name=statement.name, points=Decimal("0"), occurred_on=occurred_on,
        deduction_month=occurred_on[:7], description=description, document_file_id=file_row.id,
        submitter_id=user.id, submitter_name=user.name, submitter_role_snapshot=user.role.name,
        permission_scope_snapshot="所有CM/TR（声明升级待审核）", status="material_processing" if (photo_mode or needs_pdf_processing) else "pending_upgrade",
        material_status="processing" if (photo_mode or needs_pdf_processing) else "ready", material_source_type="photos" if photo_mode else "pdf",
        upgrade_role="source_second", upgrade_state="material_processing" if (photo_mode or needs_pdf_processing) else "pending",
        )
        db.add(second)
        db.flush()
        if photo_mode or needs_pdf_processing:
            queue_photo_material_job(db, second, file_row, source_rows, mode="upgrade_pdf_compress" if needs_pdf_processing else "upgrade", reviewer_id=reviewer.id, first_deduction_id=first.id)
            write_audit(db, user.employee, "提交声明升级材料", "deduction", second.id, after={"first_deduction_id": first.id, "reviewer": reviewer.name, "material_status": "processing"}, ip_address=client_ip(request))
            db.commit()
            invalidate_data_caches()
            return {"ok": True, "processing": True, "record": deduction_payload(second)}
        row = DeductionUpgradeRequest(
            employee_id=target.id, deduction_type_id=deduction_type.id, first_deduction_id=first.id,
            second_deduction_id=second.id, reviewer_id=reviewer.id, reviewer_name=reviewer.name,
            submitted_by=user.id, submitted_by_name=user.name, status="pending",
        )
        db.add(row)
        db.flush()
        first.upgrade_request_id = row.id
        second.upgrade_request_id = row.id
        write_audit(db, user.employee, "提交声明升级工单", "deduction_upgrade", row.id, after={"first_deduction_id": first.id, "second_deduction_id": second.id, "reviewer": reviewer.name}, ip_address=client_ip(request))
        db.commit()
        invalidate_data_caches()
        return {"ok": True, "request": deduction_upgrade_payload(db, row)}
    except Exception:
        db.rollback()
        remove_upload_file(file_row)
        for source_row in source_rows:
            remove_upload_file(source_row)
        raise


@router.get("/deduction-upgrades/pending")
def pending_deduction_upgrades(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code not in UPGRADE_REVIEWER_CODES:
        raise HTTPException(403, "仅GSM或TA GSM可审核声明升级工单")
    rows = db.query(DeductionUpgradeRequest).filter_by(reviewer_id=user.id, status="pending").order_by(DeductionUpgradeRequest.created_at.desc()).all()
    return {"items": [deduction_upgrade_payload(db, row) for row in rows]}


@router.get("/deduction-upgrades/reviewers")
def deduction_upgrade_reviewers(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code not in UPGRADE_REVIEWER_CODES:
        raise HTTPException(403, "仅GSM或TA GSM可转交声明升级工单")
    return {"items": statement_upgrade_reviewer_options(db)}


@router.post("/deduction-upgrades/{request_id}/transfer")
def transfer_deduction_upgrade(request_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = db.get(DeductionUpgradeRequest, request_id)
    if not row or row.status != "pending" or row.reviewer_id != user.id or user.role.code not in UPGRADE_REVIEWER_CODES:
        raise HTTPException(404, "待审核工单不存在或已转交")
    target_id = int(payload.get("reviewer_id") or 0)
    reason = str(payload.get("reason") or "").strip()
    target = db.get(Employee, target_id)
    target_role = role_at(db, target.id) if target else None
    if not reason:
        raise HTTPException(400, "转交说明必填")
    if not target or not target.is_active or not target_role or target_role.code not in UPGRADE_REVIEWER_CODES or not db.query(UserAccount).filter_by(employee_id=target.id, enabled=True).first():
        raise HTTPException(400, "请选择在职且账号启用的GSM或TA GSM")
    db.add(DeductionUpgradeTransfer(request_id=row.id, from_reviewer_id=user.id, from_reviewer_name=user.name, to_reviewer_id=target.id, to_reviewer_name=target.name, reason=reason))
    row.reviewer_id, row.reviewer_name = target.id, target.name
    write_audit(db, user.employee, "转交声明升级工单", "deduction_upgrade", row.id, after={"to": target.name, "reason": reason}, ip_address=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/deduction-upgrades/{request_id}/resolve")
def resolve_deduction_upgrade(request_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = db.get(DeductionUpgradeRequest, request_id)
    if not row or row.status != "pending" or row.reviewer_id != user.id or user.role.code not in UPGRADE_REVIEWER_CODES:
        raise HTTPException(404, "待审核工单不存在或已被处理")
    decision = str(payload.get("decision") or "")
    note = str(payload.get("handling_note") or "").strip()
    if not note:
        raise HTTPException(400, "处理说明必填")
    first, second = db.get(DeductionRecord, row.first_deduction_id), db.get(DeductionRecord, row.second_deduction_id)
    if not first or not second:
        raise HTTPException(409, "来源声明不存在")
    ensure_month_open(db, second.deduction_month, second.attraction_id_snapshot, "处理声明升级工单")
    if decision == "reject":
        second.status, second.upgrade_state = "void", "rejected"
        second.void_reason = "声明升级审核不通过：" + note
        first.upgrade_request_id, first.upgrade_role, first.upgrade_state = None, None, "eligible"
        row.status, row.handling_note, row.resolved_by, row.resolved_by_name, row.resolved_at = "rejected", note, user.id, user.name, datetime.now()
    elif decision == "approve":
        level_id = int(payload.get("result_level_id") or 0)
        confirmed = bool(payload.get("issued_confirmed"))
        level = db.get(DeductionLevel, level_id)
        if not confirmed:
            raise HTTPException(400, "请先确认已完成真实备忘录或一级警告开具")
        if not level or level.code not in {"MEMO", "WARNING_1"}:
            raise HTTPException(400, "升级结果仅可选择备忘录或一级警告")
        placeholder = StoredFile(storage_key=f"system/no-document-{secrets.token_hex(12)}", original_filename="无需上传正式文书", extension=".none", mime_type="application/octet-stream", file_size=0, sha256="0" * 64, uploaded_by=user.id, status="not_required")
        db.add(placeholder)
        db.flush()
        result = DeductionRecord(employee_id=second.employee_id, employee_no=second.employee_no, employee_name=second.employee_name, employee_role_snapshot=second.employee_role_snapshot, employee_group_id_snapshot=second.employee_group_id_snapshot, attraction_id_snapshot=second.attraction_id_snapshot, deduction_type_id=second.deduction_type_id, deduction_type_name=second.deduction_type_name, deduction_level_id=level.id, deduction_level_name=level.name, points=level.points, occurred_on=second.occurred_on, deduction_month=second.deduction_month, description=note, document_file_id=placeholder.id, submitter_id=user.id, submitter_name=user.name, submitter_role_snapshot=user.role.name, permission_scope_snapshot="声明升级审核", status="active", upgrade_role="result", upgrade_state="result")
        db.add(result)
        db.flush()
        first.upgrade_role, first.upgrade_state = "source_first", "source_first"
        second.status, second.upgrade_role, second.upgrade_state = "active", "source_second", "source_second"
        row.status, row.result_level_id, row.result_deduction_id, row.handling_note, row.issued_confirmed = "approved", level.id, result.id, note, True
        row.resolved_by, row.resolved_by_name, row.resolved_at = user.id, user.name, datetime.now()
    else:
        raise HTTPException(400, "无效处理结论")
    write_audit(db, user.employee, "处理声明升级工单", "deduction_upgrade", row.id, after={"decision": decision, "note": note}, ip_address=client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "request": deduction_upgrade_payload(db, row)}


@router.get("/deductions")
def list_deductions(month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if not ({"DEDUCTION_DIRECT", "DEDUCTION_ALL", "SYSTEM_ADMIN"} & user.permissions):
        raise HTTPException(403, "没有扣分查询权限")
    query = db.query(DeductionRecord)
    if "SYSTEM_ADMIN" not in user.permissions:
        query = query.filter(DeductionRecord.submitter_id == user.id)
    if month:
        query = query.filter(DeductionRecord.deduction_month == month)
    return [deduction_payload(row) for row in query.order_by(DeductionRecord.submitted_at.desc()).limit(500).all()]


@router.get("/deductions/pending-materials")
def pending_deduction_materials(
    scope: str = "mine",
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    """Shared material queue for LEAD and GSM collaboration.

    The default is the operator's own circle; the explicitly selected ``all``
    view preserves the agreed cross-circle support workflow.
    """
    if user.role.code not in MATERIAL_COLLABORATOR_CODES:
        raise HTTPException(403, "仅TA主管、主管、TA GSM或GSM可查看待补充材料")
    if scope not in {"mine", "all"}:
        raise HTTPException(400, "材料范围仅支持我的景点圈或全部景点圈")
    query = db.query(DeductionRecord).filter(DeductionRecord.status.in_({"pending_material", "material_failed"}))
    own_attraction_id = user.employee.attraction_id
    if scope == "mine":
        query = query.filter(DeductionRecord.attraction_id_snapshot == own_attraction_id) if own_attraction_id else query.filter(DeductionRecord.id == -1)
    rows = query.order_by(DeductionRecord.submitted_at.desc()).limit(200).all()
    return {
        "items": [deduction_payload(row) for row in rows],
        "scope": scope,
        "own_attraction_id": own_attraction_id,
    }


@router.get("/deductions/{record_id}/material-status")
def deduction_material_status(record_id: int, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = db.get(DeductionRecord, record_id)
    if not row:
        raise HTTPException(404, "扣分记录不存在")
    if row.submitter_id != user.id and "SYSTEM_ADMIN" not in user.permissions and user.role.code not in MATERIAL_COLLABORATOR_CODES:
        raise HTTPException(403, "无权查看该材料状态")
    return {"ok": True, "record": deduction_payload(row)}


@router.post("/deductions/{record_id}/material")
async def retry_deduction_material(
    record_id: int,
    request: Request,
    document: UploadFile | None = File(None),
    document_images: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    """Re-stage photos for a failed material job without creating another deduction."""
    row = db.get(DeductionRecord, record_id)
    if not row:
        raise HTTPException(404, "扣分记录不存在")
    can_collaborate = user.role.code in MATERIAL_COLLABORATOR_CODES
    if row.submitter_id != user.id and "SYSTEM_ADMIN" not in user.permissions and not can_collaborate:
        raise HTTPException(403, "仅原登记人、主管或管理员可以补充材料")
    if row.status not in {"pending_material", "material_failed"}:
        raise HTTPException(400, "该记录当前不需要补充材料")
    ensure_month_open(db, row.deduction_month, row.attraction_id_snapshot, "重新提交扣分材料")
    old_job = db.get(DeductionMaterialJob, row.material_job_id) if row.material_job_id else None
    upgrade_request = db.get(DeductionUpgradeRequest, row.upgrade_request_id) if row.upgrade_request_id else None
    first = None
    reviewer = None
    upgrade_material = False
    if upgrade_request and upgrade_request.status == "awaiting_material":
        first = db.get(DeductionRecord, upgrade_request.first_deduction_id)
        reviewer = db.get(Employee, upgrade_request.reviewer_id)
        if not first or not reviewer or first.upgrade_request_id != upgrade_request.id or row.upgrade_request_id != upgrade_request.id or first.status != "active":
            raise HTTPException(409, "关联升级声明状态已变化，无法重新提交材料")
        first.upgrade_state, first.upgrade_role = "material_processing", "source_first"
        row.upgrade_state, row.upgrade_role = "material_processing", "source_second"
        upgrade_material = True
    elif old_job and old_job.mode in {"upgrade", "upgrade_pdf_compress"}:
        first = db.get(DeductionRecord, old_job.first_deduction_id) if old_job.first_deduction_id else None
        reviewer = db.get(Employee, old_job.reviewer_id) if old_job.reviewer_id else None
        if not first or not reviewer or first.upgrade_request_id or first.status != "active":
            raise HTTPException(409, "关联升级声明状态已变化，无法重新提交材料")
        claimed = db.execute(
            update(DeductionRecord)
            .where(DeductionRecord.id == first.id, or_(DeductionRecord.upgrade_state.is_(None), DeductionRecord.upgrade_state == "eligible"))
            .values(upgrade_state="material_processing", upgrade_role="source_first")
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "关联历史声明正在被其他工单使用，请刷新后重试")
        upgrade_material = True
    photo_mode = bool([item for item in (document_images or []) if item and item.filename])
    pdf_mode = bool(document and document.filename)
    if pdf_mode == photo_mode:
        raise HTTPException(400, "请上传一个PDF，或选择1至6张照片材料")
    source_rows: list[StoredFile] = []
    output = None
    try:
        source_rows = [await stage_pdf_material(db, document, user.id)] if pdf_mode else await stage_photo_materials(db, list(document_images or []), user.id)
        output = create_pdf_placeholder(db, user.id)
        old_output = db.get(StoredFile, row.document_file_id)
        if old_output and old_output.status == "pending_conversion":
            old_output.status = "superseded"
        row.document_file_id = output.id
        row.status = "material_processing"
        row.material_status = "processing"
        row.material_error = None
        row.material_source_type = "pdf" if pdf_mode else "photos"
        row.material_uploaded_by = user.id
        row.material_uploaded_by_name = user.name
        row.material_uploaded_at = datetime.now()
        row.material_revision = int(row.material_revision or 0) + 1
        # Records originally submitted without material have no job yet.  They
        # may be supplemented later, so never dereference a missing old job.
        row.upgrade_state = "material_processing" if upgrade_material else row.upgrade_state
        queue_photo_material_job(
            db,
            row,
            output,
            source_rows,
            mode=("upgrade_pdf_compress" if upgrade_material and pdf_mode else ("upgrade" if upgrade_material else ("pdf_compress" if pdf_mode else "deduction"))),
            reviewer_id=reviewer.id if reviewer else None,
            first_deduction_id=first.id if first else None,
        )
        write_audit(db, user.employee, "补充扣分材料", "deduction", row.id, after={"material_status": "processing", "contributor": user.name, "retry_of_job_id": old_job.id if old_job else None}, ip_address=client_ip(request))
        db.commit()
    except Exception:
        db.rollback()
        remove_upload_file(output)
        for source in source_rows:
            remove_upload_file(source)
        raise
    invalidate_data_caches()
    return {"ok": True, "record": deduction_payload(row)}


@router.post("/deductions/{record_id}/void")
def void_deduction(record_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = db.get(DeductionRecord, record_id)
    if not row:
        raise HTTPException(404, "扣分记录不存在")
    if row.submitter_id != user.id and "SYSTEM_ADMIN" not in user.permissions:
        raise HTTPException(403, "只有原登记人或管理员可以作废")
    if row.upgrade_request_id:
        raise HTTPException(400, "声明升级工单关联记录不可作废；请在升级工单中完成审核处理")
    if row.status not in {"active", "pending_material", "material_failed"}:
        raise HTTPException(400, "记录已经作废")
    employee = db.get(Employee, row.employee_id)
    ensure_month_open(db, row.deduction_month, row.attraction_id_snapshot or (employee.attraction_id if employee else None), "作废扣分")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "作废原因必填")
    before = deduction_payload(row)
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
    write_audit(db, user.employee, "作废扣分", "deduction", row.id, before=before, after=deduction_payload(row), reason=reason, ip_address=client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {"ok": True}
