"""Recognition entry and review endpoints."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user, require_permissions
from app.v2_database import FILE_DIR, LEGACY_CIRCLE_BY_VENUE, get_db
from app.v2_models import Attraction, DeductionFollowUp, DeductionRecord, Employee, EmployeeRoleAssignment, RecognitionRecord, RecognitionAttachment, RecognitionMonthlyQuota, RecognitionReview, RecognitionType, Role, SickLeaveRecord, UserAccount
from app.recognition_encouragement import encouragement_options
from app.v2_services import FRONTLINE_CODES, LEADER_CODES, RECOGNIZER_CODES, current_leader_for_employee, direct_member_ids, recognition_score_for_role, recognizer_role_for_date, recognizer_options, role_at, save_image_upload, write_audit
from app.routers._shared import (
    ATTENDANCE_FILTER_STATUSES,
    DEDICATED_RECOGNITION_TYPE_CODES,
    DEDUCTION_FILTER_STATUSES,
    MONTHLY_CATEGORY_CAP_CODES,
    MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE,
    MONTHLY_CATEGORY_CAP_LIMIT,
    RECOGNITION_FILTER_STATUSES,
    SCOPED_HR_ROLE_CODE,
    SPECIAL_RECOGNITION_TYPES,
    client_ip,
    deduction_follow_up_payload,
    deduction_payload,
    ensure_enabled_frontline_target,
    ensure_month_open,
    ensure_scoped_hr_attraction,
    ensure_scoped_hr_employee,
    existing_submission,
    invalidate_data_caches,
    like_escaped_pattern,
    normalize_request_key,
    parse_iso_date,
    recognition_payload,
    remember_submission,
    sick_leave_payloads,
    submission_payload_digest,
    void_operator_snapshot,
)

router = APIRouter()


def ensure_enabled_poc_target(db: Session, employee_id: int) -> tuple[Employee, Role]:
    target = db.get(Employee, employee_id)
    target_role = role_at(db, employee_id) if target else None
    account_enabled = bool(target and db.query(UserAccount.id).filter(UserAccount.employee_id == target.id, UserAccount.enabled.is_(True)).first())
    if not target or not target.is_active or not account_enabled or not target_role or target_role.code not in (FRONTLINE_CODES | LEADER_CODES):
        raise HTTPException(400, "POC仅可认可在职、账号启用的CM/TR、TA主管或主管")
    return target, target_role


def apply_monthly_category_cap(db: Session, row: RecognitionRecord, recognition_type: RecognitionType) -> None:
    """Freeze the actually credited score when a record becomes confirmed."""
    row.credited_fraction = Decimal("0.00")
    row.monthly_cap_rule_code = None
    row.monthly_cap_status = "not_applicable"
    row.monthly_cap_limit = None
    row.monthly_cap_confirmed_before = None
    row.monthly_cap_reason = None
    row.monthly_cap_evaluated_at = datetime.now()
    if recognition_type.code not in MONTHLY_CATEGORY_CAP_CODES or date.fromisoformat(row.recognition_date) < MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE:
        row.credited_fraction = Decimal(row.fraction or 0)
        row.monthly_cap_status = "legacy_not_limited" if date.fromisoformat(row.recognition_date) < MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE else "not_applicable"
        return
    previous = db.query(func.coalesce(func.sum(RecognitionRecord.credited_fraction), 0)).filter(
        RecognitionRecord.employee_id == row.employee_id,
        RecognitionRecord.recognition_month == row.recognition_month,
        RecognitionRecord.recognition_type_id == row.recognition_type_id,
        RecognitionRecord.status == "confirmed",
        RecognitionRecord.id != (row.id or -1),
    ).scalar() or Decimal("0.00")
    previous = Decimal(previous)
    raw = Decimal(row.fraction or 0)
    credit = min(raw, max(Decimal("0.00"), MONTHLY_CATEGORY_CAP_LIMIT - previous))
    row.credited_fraction = credit
    row.monthly_cap_rule_code = "five_category_monthly_5_points"
    row.monthly_cap_limit = MONTHLY_CATEGORY_CAP_LIMIT
    row.monthly_cap_confirmed_before = previous
    if credit == raw:
        row.monthly_cap_status = "within_limit"
    elif credit > 0:
        row.monthly_cap_status = "partially_capped"
        row.monthly_cap_reason = f"本月{recognition_type.name}已确认 {previous:.2f} 分，本条仅计入 {credit:.2f} 分（单项上限5分）"
    else:
        row.monthly_cap_status = "capped_zero"
        row.monthly_cap_reason = f"本月{recognition_type.name}已达5分上限，本条保留认可记录但不再计分"


@router.get("/recognizers")
def get_recognizers(attraction_id: int, recognition_date: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    circle = db.get(Attraction, attraction_id)
    if not circle or not circle.active or not circle.employee_circle:
        raise HTTPException(400, "请选择有效员工景点圈")
    ensure_scoped_hr_attraction(db, user, attraction_id)
    if recognition_date:
        parse_iso_date(recognition_date, "认可日期")
    rows = recognizer_options(db, attraction_id, recognition_date)
    rows.extend(
        {
            "id": rule["option_id"],
            "employee_no": "",
            "name": rule["name"],
            "role_code": code,
            "role_name": "系统固定分值",
            "score": float(rule["score"]),
            "special": True,
        }
        for code, rule in SPECIAL_RECOGNITION_TYPES.items()
    )
    return rows


@router.post("/recognitions")
async def create_recognition(
    request: Request,
    recognition_date: str = Form(...),
    occurred_attraction_id: int = Form(...),
    recognition_type_id: int = Form(...),
    recognizer_employee_id: str = Form(...),
    content: str = Form(...),
    employee_id: int | None = Form(None),
    idempotency_key: str | None = Form(None),
    same_day_duplicate_confirmed: bool = Form(False),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    request_key = normalize_request_key(idempotency_key)
    payload_digest = submission_payload_digest(
        {
            "employee_id": int(employee_id or 0),
            "recognition_date": str(recognition_date or "").strip(),
            "occurred_attraction_id": int(occurred_attraction_id or 0),
            "recognition_type_id": int(recognition_type_id or 0),
            "recognizer_employee_id": str(recognizer_employee_id or "").strip(),
            "content": str(content or "").strip(),
            "same_day_duplicate_confirmed": bool(same_day_duplicate_confirmed),
        }
    )
    duplicate_row = existing_submission(db, user.id, "recognition", request_key, RecognitionRecord, payload_digest)
    if duplicate_row:
        return {"ok": True, "record": recognition_payload(duplicate_row), "duplicate": True}
    recognition_date = str(recognition_date or "")
    parse_iso_date(recognition_date, "认可日期")
    content = str(content or "").strip()
    if not content or len(content) > 20:
        raise HTTPException(400, "认可内容必填且不能超过20字")
    target_id = int(employee_id or user.id)
    target, target_role = ensure_enabled_frontline_target(db, target_id, "加分")
    ensure_scoped_hr_employee(db, user, target)
    is_self = target.id == user.id and user.role.code in FRONTLINE_CODES
    if not is_self and "EMPLOYEE_ADD" not in user.permissions:
        raise HTTPException(403, "没有员工加分权限")
    attraction = db.get(Attraction, int(occurred_attraction_id or 0))
    if not attraction or not attraction.active or not attraction.recognition_venue:
        raise HTTPException(400, "请选择有效认可发生景点")
    target_circle = db.get(Attraction, target.attraction_id) if target.attraction_id else None
    if not target_circle or not target_circle.active or not target_circle.employee_circle:
        raise HTTPException(400, "被加分员工未配置有效景点圈")
    ensure_month_open(db, recognition_date[:7], target_circle.id, "新增签卡")
    if user.role.code == SCOPED_HR_ROLE_CODE and LEGACY_CIRCLE_BY_VENUE.get(attraction.name, attraction.name) != target_circle.name:
        raise HTTPException(403, "景点圈HR只能登记所属景点圈发生的认可")
    recognition_type = db.get(RecognitionType, int(recognition_type_id or 0))
    if not recognition_type or not recognition_type.active:
        raise HTTPException(400, "请选择有效认可类型")
    if recognition_type.code in DEDICATED_RECOGNITION_TYPE_CODES:
        raise HTTPException(400, "POC特别贡献只能通过专用入口开具")
    selected_recognizer = str(recognizer_employee_id or "").strip()
    special_rule = SPECIAL_RECOGNITION_TYPES.get(recognition_type.code)
    recognizer_role = None
    if special_rule:
        if selected_recognizer != special_rule["option_id"]:
            raise HTTPException(400, f"认可类型为{recognition_type.name}时，认可人/签卡人必须选择{recognition_type.name}")
        recognizer = user.employee
        recognizer_name = special_rule["name"]
        recognizer_role_name = "系统固定分值"
        fraction = special_rule["score"]
    else:
        if selected_recognizer.startswith("special:"):
            raise HTTPException(400, "普通认可类型必须选择实际认可人/签卡人")
        try:
            recognizer_id = int(selected_recognizer)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "请选择有效认可人") from exc
        recognizer = db.get(Employee, recognizer_id)
        recognizer_role = recognizer_role_for_date(db, recognizer.id, recognition_date) if recognizer else None
        if not recognizer or not recognizer.is_active or not recognizer_role or recognizer_role.code not in RECOGNIZER_CODES:
            raise HTTPException(400, "请选择有效认可人")
        allowed_ids = {row["id"] for row in recognizer_options(db, target_circle.id, recognition_date)}
        if recognizer.id not in allowed_ids:
            raise HTTPException(400, "认可人必须是TALEAD/LEAD或TAGSM及以上的非HR在职人员")
        recognizer_name = recognizer.name
        recognizer_role_name = recognizer_role.name
        fraction = recognition_score_for_role(db, recognizer_role.id, recognition_date)
    quota_code = recognition_type.code if special_rule and special_rule["monthly_limit"] else None
    active_same_day_rows = (
        db.query(RecognitionRecord)
        .filter(
            RecognitionRecord.employee_id == target.id,
            RecognitionRecord.recognition_date == recognition_date,
            RecognitionRecord.recognition_type_id == recognition_type.id,
            RecognitionRecord.recognizer_employee_id == recognizer.id,
            RecognitionRecord.status.in_(("pending", "confirmed")),
        )
        .order_by(RecognitionRecord.submitted_at.asc(), RecognitionRecord.id.asc())
        .all()
    )
    duplicate_group = None
    duplicate_sequence = None
    if active_same_day_rows:
        duplicate_group = f"{recognition_date}:{target.id}:{recognition_type.id}:{recognizer.id}"
        if not same_day_duplicate_confirmed:
            # A concurrent retry can reach the same-day reminder after the
            # first request has committed.  Re-check the idempotency receipt so
            # the caller receives its original record rather than a false
            # duplicate warning.
            duplicate_row = existing_submission(db, user.id, "recognition", request_key, RecognitionRecord)
            if duplicate_row:
                return {"ok": True, "record": recognition_payload(duplicate_row), "duplicate": True}
            raise HTTPException(
                409,
                {
                    "code": "SAME_DAY_RECOGNITION_DUPLICATE",
                    "message": "该员工今日已由该认可人登记过同类认可，请确认是否为另一项独立表现。",
                    "previous_records": [
                        {
                            "id": item.id,
                            "submitted_at": item.submitted_at.strftime("%Y-%m-%d %H:%M"),
                            "status": item.status,
                            "content": item.content,
                        }
                        for item in active_same_day_rows
                    ],
                },
            )
        for index, item in enumerate(active_same_day_rows, start=1):
            item.same_day_duplicate_group = duplicate_group
            item.same_day_duplicate_sequence = index
        duplicate_sequence = len(active_same_day_rows) + 1
    if quota_code and db.query(RecognitionMonthlyQuota).filter_by(
        employee_id=target.id, quota_code=quota_code, quota_month=recognition_date[:7]
    ).first():
        raise HTTPException(409, "该员工本月已经登记过表扬信，每名员工每月只能获得一次表扬信加分")
    reviewer = current_leader_for_employee(db, target.id) if is_self else None
    if is_self and (not image or not image.filename):
        raise HTTPException(400, "CM/TR本人登记必须上传1张认可图片")
    file_row = None
    row = RecognitionRecord(
        employee_id=target.id,
        employee_no=target.employee_no,
        employee_name=target.name,
        employee_role_snapshot=target_role.name,
        employee_role_code_snapshot=target_role.code,
        home_attraction_id=target.attraction_id,
        home_attraction_name=target.attraction.name if target.attraction else "",
        occurred_attraction_id=attraction.id,
        recognition_date=recognition_date,
        recognition_month=recognition_date[:7],
        recognition_type_id=recognition_type.id,
        recognition_type_name=recognition_type.name,
        content=content,
        recognizer_employee_id=recognizer.id,
        recognizer_name=recognizer_name,
        recognizer_role_snapshot=recognizer_role_name,
        recognizer_role_code_snapshot=recognizer_role.code if recognizer_role else None,
        operator_employee_id=user.id,
        operator_name=user.name,
        operator_role_snapshot=user.role.name,
        operator_role_code_snapshot=user.role.code,
        source="self" if is_self else "manager",
        fraction=fraction,
        status="pending" if is_self else "confirmed",
        same_day_duplicate_group=duplicate_group,
        same_day_duplicate_sequence=duplicate_sequence,
        assigned_reviewer_id=reviewer.id if reviewer else None,
    )
    try:
        db.add(row)
        db.flush()
        if row.status == "confirmed":
            apply_monthly_category_cap(db, row, recognition_type)
            db.flush()
        if quota_code:
            db.add(
                RecognitionMonthlyQuota(
                    employee_id=target.id,
                    quota_code=quota_code,
                    quota_month=recognition_date[:7],
                    recognition_id=row.id,
                )
            )
            db.flush()
        if is_self and image:
            file_row = await save_image_upload(db, image, user.id)
            row.attachments.append(RecognitionAttachment(file_id=file_row.id, attachment_type="evidence", sort_order=1))
            db.flush()
        remember_submission(db, user.id, "recognition", request_key, row.id, payload_digest)
        audit_payload = recognition_payload(row)
        if file_row:
            audit_payload["image"] = {"sha256": file_row.sha256, "size": file_row.file_size, "type": file_row.mime_type}
        write_audit(db, user.employee, "登记签卡", "recognition", row.id, after=audit_payload, ip_address=client_ip(request))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if file_row:
            path = (FILE_DIR / file_row.storage_key).resolve()
            if FILE_DIR.resolve() in path.parents:
                path.unlink(missing_ok=True)
        duplicate_row = existing_submission(db, user.id, "recognition", request_key, RecognitionRecord, payload_digest)
        if duplicate_row:
            return {"ok": True, "record": recognition_payload(duplicate_row), "duplicate": True}
        if quota_code:
            raise HTTPException(409, "该员工本月已经登记过表扬信，每名员工每月只能获得一次表扬信加分") from exc
        raise
    except Exception:
        db.rollback()
        if file_row:
            path = (FILE_DIR / file_row.storage_key).resolve()
            if FILE_DIR.resolve() in path.parents:
                path.unlink(missing_ok=True)
        raise
    invalidate_data_caches()
    response = {"ok": True, "record": recognition_payload(row)}
    if is_self:
        response["encouragement_options"] = encouragement_options(
            record_id=row.id,
            recognition_type_name=recognition_type.name,
            content=row.content,
            stage="submitted",
        )
    return response


@router.post("/recognitions/poc")
def create_poc_recognition(
    request: Request,
    recognition_date: str = Form(...),
    employee_id: int = Form(...),
    points: str = Form(...),
    poc_period_type: str = Form(...),
    poc_reason: str = Form(...),
    idempotency_key: str | None = Form(None),
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("POC_ISSUE")),
):
    if user.role.code not in {"TA_GSM", "GSM", "AM"}:
        raise HTTPException(403, "仅TA GSM、GSM、AM可以开具POC特别贡献")
    request_key = normalize_request_key(idempotency_key)
    payload_digest = submission_payload_digest(
        {
            "employee_id": int(employee_id),
            "recognition_date": str(recognition_date or "").strip(),
            "points": str(points or "").strip(),
            "poc_period_type": str(poc_period_type or "").strip(),
            "poc_reason": str(poc_reason or "").strip(),
        }
    )
    duplicate_row = existing_submission(db, user.id, "poc", request_key, RecognitionRecord, payload_digest)
    if duplicate_row:
        return {"ok": True, "record": recognition_payload(duplicate_row), "duplicate": True}
    recognition_date = str(recognition_date or "")
    parse_iso_date(recognition_date, "认可日期")
    try:
        amount = Decimal(str(points))
    except InvalidOperation as exc:
        raise HTTPException(400, "POC分值必须为1至5的整数") from exc
    if amount not in {Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")}:
        raise HTTPException(400, "POC分值必须为1至5分")
    if poc_period_type not in {"month", "quarter"}:
        raise HTTPException(400, "请选择月度或季度认可周期")
    reason = str(poc_reason or "").strip()
    if not reason or len(reason) > 100:
        raise HTTPException(400, "特别贡献原因必填且不能超过100字")
    target, target_role = ensure_enabled_poc_target(db, int(employee_id))
    target_circle = db.get(Attraction, target.attraction_id) if target.attraction_id else None
    if not target_circle or not target_circle.employee_circle:
        raise HTTPException(400, "被认可员工未配置有效景点圈")
    ensure_month_open(db, recognition_date[:7], target_circle.id, "新增POC特别贡献")
    recognition_type = db.query(RecognitionType).filter_by(code="POC", active=True).first()
    if not recognition_type:
        raise HTTPException(500, "POC认可类型尚未初始化")
    parsed = date.fromisoformat(recognition_date)
    period_key = f"{parsed.year}-{parsed.month:02d}" if poc_period_type == "month" else f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"
    row = RecognitionRecord(
        employee_id=target.id, employee_no=target.employee_no, employee_name=target.name,
        employee_role_snapshot=target_role.name, employee_role_code_snapshot=target_role.code,
        home_attraction_id=target.attraction_id, home_attraction_name=target.attraction.name if target.attraction else "",
        occurred_attraction_id=target_circle.id, recognition_date=recognition_date, recognition_month=recognition_date[:7],
        recognition_type_id=recognition_type.id, recognition_type_name=recognition_type.name, content="POC特别贡献",
        recognizer_employee_id=user.id, recognizer_name=user.name, recognizer_role_snapshot=user.role.name,
        recognizer_role_code_snapshot=user.role.code, operator_employee_id=user.id, operator_name=user.name,
        operator_role_snapshot=user.role.name, operator_role_code_snapshot=user.role.code, source="manager",
        fraction=amount, credited_fraction=amount, monthly_cap_status="not_applicable", status="confirmed",
        poc_period_type=poc_period_type, poc_period_key=period_key, poc_reason=reason,
    )
    try:
        db.add(row)
        db.flush()
        remember_submission(db, user.id, "poc", request_key, row.id, payload_digest)
        write_audit(db, user.employee, "登记POC特别贡献", "recognition", row.id, after=recognition_payload(row), ip_address=client_ip(request))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        duplicate_row = existing_submission(db, user.id, "poc", request_key, RecognitionRecord, payload_digest)
        if duplicate_row:
            return {"ok": True, "record": recognition_payload(duplicate_row), "duplicate": True}
        raise HTTPException(409, "POC特别贡献提交冲突，请刷新后重试") from exc
    invalidate_data_caches()
    return {"ok": True, "record": recognition_payload(row)}


@router.delete("/recognitions/{record_id}")
def withdraw_recognition(record_id: int, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = db.get(RecognitionRecord, record_id)
    if not row:
        raise HTTPException(404, "签卡记录不存在")
    if user.id not in (row.employee_id, row.operator_employee_id) and "SYSTEM_ADMIN" not in user.permissions:
        raise HTTPException(403, "只有CM/TR本人、原代录人或管理员可以撤回")
    if row.status == "void":
        raise HTTPException(400, "签卡记录已经撤回")
    employee = db.get(Employee, row.employee_id)
    ensure_month_open(db, row.recognition_month, row.home_attraction_id or (employee.attraction_id if employee else None), "撤回签卡")
    before = recognition_payload(row)
    role_code, role_name, permission_scope = void_operator_snapshot(db, user)
    row.voided_from_status = row.status
    row.status = "void"
    row.voided_by = user.id
    row.voided_by_name = user.name
    row.voided_by_role_code = role_code
    row.voided_by_role_name = role_name
    row.void_permission_scope_snapshot = permission_scope
    row.voided_at = datetime.now()
    row.void_reason = "用户撤回"
    db.query(RecognitionMonthlyQuota).filter(RecognitionMonthlyQuota.recognition_id == row.id).delete(synchronize_session=False)
    db.flush()
    write_audit(
        db,
        user.employee,
        "撤回签卡（留档）",
        "recognition",
        row.id,
        before=before,
        after=recognition_payload(row),
        reason=row.void_reason,
        ip_address=client_ip(request),
    )
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "deducted_score": before["credited_fraction"] if before["status"] == "confirmed" else 0}


@router.get("/entered-recognitions")
def entered_recognitions(month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if "EMPLOYEE_ADD" not in user.permissions and "SYSTEM_ADMIN" not in user.permissions:
        raise HTTPException(403, "没有代录查询权限")
    query = db.query(RecognitionRecord).filter(
        RecognitionRecord.operator_employee_id == user.id,
        RecognitionRecord.status != "void",
    )
    if month:
        query = query.filter(RecognitionRecord.recognition_month == month)
    return [recognition_payload(row) for row in query.order_by(RecognitionRecord.submitted_at.desc()).limit(500).all()]


@router.get("/reviews")
def reviews(
    view: Literal["queue", "history"] = "queue",
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("REVIEW_DIRECT")),
):
    member_ids = direct_member_ids(db, user.id)
    query = (
        db.query(RecognitionRecord)
        .filter(
            RecognitionRecord.employee_id.in_(member_ids) if member_ids else RecognitionRecord.id == -1,
        )
    )
    if view == "history":
        query = query.filter(RecognitionRecord.status.in_(("confirmed", "rejected")))
        query = query.order_by(RecognitionRecord.submitted_at.desc(), RecognitionRecord.id.desc())
    else:
        query = query.filter(RecognitionRecord.status == "pending")
        query = query.order_by(RecognitionRecord.submitted_at.asc(), RecognitionRecord.id.asc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    return {
        "view": view,
        "items": [recognition_payload(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


@router.post("/reviews/{record_id}")
def review_recognition(record_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("REVIEW_DIRECT"))):
    row = db.get(RecognitionRecord, record_id)
    if not row or row.employee_id not in direct_member_ids(db, user.id):
        raise HTTPException(404, "直属组员签卡不存在")
    employee = db.get(Employee, row.employee_id)
    ensure_month_open(db, row.recognition_month, row.home_attraction_id or (employee.attraction_id if employee else None), "复核签卡")
    action = str(payload.get("action") or "")
    mapping = {"confirm": "confirmed", "reject": "rejected", "restore": "pending"}
    if action not in mapping:
        raise HTTPException(400, "无效操作")
    before = row.status
    after = mapping[action]
    allowed_actions = {
        "pending": {"confirm", "reject"},
        "confirmed": {"restore"},
        "rejected": {"restore"},
    }
    if action not in allowed_actions.get(before, set()):
        raise HTTPException(409, "记录状态已经变化，请刷新后按当前状态操作")
    review_note = str(payload.get("note") or "").strip() or None
    claimed = db.execute(
        update(RecognitionRecord)
        .where(RecognitionRecord.id == row.id, RecognitionRecord.status == before)
        .values(
            status=after,
            reviewed_by=user.id,
            reviewed_by_name=user.name,
            reviewed_at=datetime.now(),
            review_note=review_note,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "记录已被其他人处理，请刷新后重试")
    db.refresh(row)
    recognition_type = db.get(RecognitionType, row.recognition_type_id)
    if after == "confirmed" and recognition_type:
        apply_monthly_category_cap(db, row, recognition_type)
    elif after in {"pending", "rejected"}:
        row.credited_fraction = Decimal("0.00")
        row.monthly_cap_status = "not_applicable"
        row.monthly_cap_reason = None
    quota_code = recognition_type.code if recognition_type and recognition_type.code == "COMMENDATION_LETTER" else None
    quota = db.query(RecognitionMonthlyQuota).filter_by(recognition_id=row.id).first()
    if after == "rejected" and quota:
        db.delete(quota)
    elif after in {"pending", "confirmed"} and quota_code and not quota:
        occupied = db.query(RecognitionMonthlyQuota).filter_by(
            employee_id=row.employee_id, quota_code=quota_code, quota_month=row.recognition_month
        ).first()
        if occupied:
            raise HTTPException(409, "该员工本月已有其他表扬信记录，当前记录不能还原")
        db.add(
            RecognitionMonthlyQuota(
                employee_id=row.employee_id,
                quota_code=quota_code,
                quota_month=row.recognition_month,
                recognition_id=row.id,
            )
        )
    db.add(
        RecognitionReview(
            recognition_id=row.id,
            action=action,
            before_status=before,
            after_status=after,
            reviewer_id=user.id,
            reviewer_name=user.name,
            note=row.review_note,
        )
    )
    write_audit(db, user.employee, "复核签卡", "recognition", row.id, before={"status": before}, after={"status": after}, reason=row.review_note, ip_address=client_ip(request))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "该员工本月已有其他表扬信记录，当前记录不能还原") from exc
    invalidate_data_caches()
    return {"ok": True, "record": recognition_payload(row)}


@router.get("/my-entries")
def my_entries(
    month: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    record_type: str = "all",
    scope: str = "mine",
    page: int = 1,
    page_size: int = 100,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    if not ({"EMPLOYEE_ADD", "SICK_REGISTER", "DEDUCTION_DIRECT", "DEDUCTION_ALL", "SYSTEM_ADMIN"} & user.permissions):
        raise HTTPException(403, "没有登记记录查询权限")
    if record_type not in {"all", "recognition", "deduction", "sick_leave", "follow_up"}:
        raise HTTPException(400, "记录类型无效")
    if scope not in {"mine", "supervisors"}:
        raise HTTPException(400, "查询范围无效")
    is_supervisor_entry_view = user.role.code in LEADER_CODES
    if scope == "supervisors" and not is_supervisor_entry_view:
        raise HTTPException(403, "仅TA主管或主管可查看全部主管登记记录")
    if scope == "supervisors" and record_type in {"recognition", "follow_up"}:
        raise HTTPException(400, "全部主管登记记录仅查询扣分和缺勤")
    parsed_start = parse_iso_date(start_date, "开始日期") if start_date else None
    parsed_end = parse_iso_date(end_date, "结束日期") if end_date else None
    if parsed_start and parsed_end and parsed_start > parsed_end:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    page = max(1, page)
    page_size = min(max(10, page_size), 200)
    items: list[dict] = []
    supervisor_submitter_ids: set[int] = set()
    supervisor_role_names: set[str] = set()
    if scope == "supervisors":
        today_value = date.today().isoformat()
        supervisor_submitter_ids = {
            int(employee_id)
            for (employee_id,) in (
                db.query(EmployeeRoleAssignment.employee_id)
                .join(Role, Role.id == EmployeeRoleAssignment.role_id)
                .filter(
                    EmployeeRoleAssignment.status == "active",
                    EmployeeRoleAssignment.starts_on <= today_value,
                    or_(EmployeeRoleAssignment.ends_on.is_(None), EmployeeRoleAssignment.ends_on >= today_value),
                    Role.code.in_(LEADER_CODES),
                )
                .all()
            )
        }
        supervisor_role_names = {
            name for (name,) in db.query(Role.name).filter(Role.code.in_(LEADER_CODES), Role.active.is_(True)).all()
        }
    if scope != "supervisors" and record_type in {"all", "recognition"}:
        query = db.query(RecognitionRecord).filter(RecognitionRecord.operator_employee_id == user.id)
        if start_date:
            query = query.filter(RecognitionRecord.recognition_date >= start_date)
        if end_date:
            query = query.filter(RecognitionRecord.recognition_date <= end_date)
        if month and not (start_date or end_date):
            query = query.filter(RecognitionRecord.recognition_month == month)
        if keyword:
            value = like_escaped_pattern(keyword)
            query = query.filter(or_(RecognitionRecord.employee_name.like(value, escape="\\"), RecognitionRecord.employee_no.like(value, escape="\\")))
        if status in RECOGNITION_FILTER_STATUSES:
            query = query.filter(RecognitionRecord.status == status)
        elif status in ATTENDANCE_FILTER_STATUSES:
            query = query.filter(RecognitionRecord.id == -1)
        else:
            query = query.filter(RecognitionRecord.status != "void")
        items.extend(recognition_payload(row) for row in query.order_by(RecognitionRecord.submitted_at.desc()).all())
    if record_type in {"all", "deduction"}:
        query = db.query(DeductionRecord)
        if scope == "supervisors":
            if not supervisor_submitter_ids and not supervisor_role_names:
                query = query.filter(DeductionRecord.id == -1)
            else:
                scope_filters = []
                if supervisor_submitter_ids:
                    scope_filters.append(DeductionRecord.submitter_id.in_(supervisor_submitter_ids))
                if supervisor_role_names:
                    scope_filters.append(DeductionRecord.submitter_role_snapshot.in_(supervisor_role_names))
                query = query.filter(or_(*scope_filters))
        else:
            query = query.filter(DeductionRecord.submitter_id == user.id)
        if start_date:
            query = query.filter(DeductionRecord.occurred_on >= start_date)
        if end_date:
            query = query.filter(DeductionRecord.occurred_on <= end_date)
        if month and not (start_date or end_date):
            query = query.filter(DeductionRecord.deduction_month == month)
        if keyword:
            value = like_escaped_pattern(keyword)
            query = query.filter(or_(DeductionRecord.employee_name.like(value, escape="\\"), DeductionRecord.employee_no.like(value, escape="\\")))
        if status in DEDUCTION_FILTER_STATUSES:
            query = query.filter(DeductionRecord.status == status)
        elif status in RECOGNITION_FILTER_STATUSES or status in ATTENDANCE_FILTER_STATUSES:
            query = query.filter(DeductionRecord.id == -1)
        for row in query.order_by(DeductionRecord.submitted_at.desc()).all():
            payload = deduction_payload(row)
            if row.submitter_id == user.id and row.status in {"pending_material", "material_failed"}:
                payload["available_actions"] = [*payload["available_actions"], "void"]
            items.append(payload)
    if record_type in {"all", "sick_leave"}:
        query = db.query(SickLeaveRecord)
        if scope == "supervisors":
            query = query.filter(SickLeaveRecord.submitted_by.in_(supervisor_submitter_ids) if supervisor_submitter_ids else SickLeaveRecord.id == -1)
        else:
            query = query.filter(SickLeaveRecord.submitted_by == user.id)
        if start_date:
            query = query.filter(SickLeaveRecord.leave_end_date >= start_date)
        if end_date:
            query = query.filter(SickLeaveRecord.leave_start_date <= end_date)
        if month and not (start_date or end_date):
            query = query.filter(SickLeaveRecord.attendance_month == month)
        if keyword:
            value = like_escaped_pattern(keyword)
            query = query.join(Employee, Employee.id == SickLeaveRecord.employee_id).filter(or_(Employee.name.like(value, escape="\\"), Employee.employee_no.like(value, escape="\\")))
        if status in ATTENDANCE_FILTER_STATUSES:
            query = query.filter(SickLeaveRecord.status == status)
        elif status in RECOGNITION_FILTER_STATUSES:
            query = query.filter(SickLeaveRecord.id == -1)
        items.extend(sick_leave_payloads(db, query.order_by(SickLeaveRecord.submitted_at.desc()).all()))
    if scope != "supervisors" and record_type in {"all", "follow_up"}:
        query = db.query(DeductionFollowUp).filter(DeductionFollowUp.supervisor_id == user.id)
        if start_date:
            query = query.filter(DeductionFollowUp.occurred_on >= start_date)
        if end_date:
            query = query.filter(DeductionFollowUp.occurred_on <= end_date)
        if month and not (start_date or end_date):
            query = query.filter(DeductionFollowUp.occurred_on.like(f"{month}%"))
        if keyword:
            value = f"%{keyword.strip()}%"
            query = query.filter(or_(DeductionFollowUp.employee_name.like(value), DeductionFollowUp.employee_no.like(value)))
        if status in {"pending", "issued"}:
            query = query.filter(DeductionFollowUp.status == status)
        elif status in {"active", "void", "confirmed", "rejected"}:
            query = query.filter(DeductionFollowUp.id == -1)
        items.extend(deduction_follow_up_payload(row) for row in query.order_by(DeductionFollowUp.created_at.desc()).all())
    items.sort(key=lambda row: row["submitted_at"], reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    active_deductions = sum(row["points"] for row in items if row["record_type"] == "deduction" and row["status"] == "active")
    recognition_score = sum(row.get("credited_fraction", row["fraction"]) for row in items if row["record_type"] == "recognition" and row["status"] == "confirmed")
    active_sick_days = sum(row["leave_days"] for row in items if row["record_type"] == "sick_leave" and row["status"] == "active")
    charged_sick_days = sum(row["charged_days"] for row in items if row["record_type"] == "sick_leave" and row["status"] == "active")
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "scope": scope,
        "summary": {
            "recognition_count": sum(row["record_type"] == "recognition" for row in items),
            "recognition_score": round(recognition_score, 2),
            "deduction_count": sum(row["record_type"] == "deduction" for row in items),
            "deduction_score": round(active_deductions, 2),
            "sick_leave_count": sum(row["record_type"] == "sick_leave" for row in items),
            "active_sick_days": round(active_sick_days, 1),
            "charged_sick_days": charged_sick_days,
            "follow_up_count": sum(row["record_type"] == "follow_up" for row in items),
        },
    }
