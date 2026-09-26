"""File serving and shared option endpoints."""
from __future__ import annotations

from io import BytesIO
import re
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user
from app.v2_database import EMPLOYEE_CIRCLES, FILE_DIR, LEGACY_CIRCLE_BY_VENUE, RECOGNITION_VENUES, get_db
from app.v2_models import Attraction, DeductionLevel, DeductionRecord, DeductionType, Employee, RecognitionRecord, RecognitionAttachment, RecognitionType, Role, SickLeaveRecord, StoredFile
from app.v2_services import direct_member_ids, write_audit
from app.v2_watermark import watermark_image, watermark_pdf
from app.v2_preview_cache import watermarked_preview_cache
from app.routers._shared import (
    CIRCLE_HR_MANAGED_ROLE_CODES,
    DEDICATED_RECOGNITION_TYPE_CODES,
    DEDUCTION_TYPE_ORDER,
    DIRECT_HIDDEN_DEDUCTION_CODES,
    PREVIEW_IMAGE_EXTENSIONS,
    PREVIEW_PDF_EXTENSIONS,
    REPEAT_CONTROLLED_DEDUCTION_CODES,
    SCOPED_HR_ROLE_CODE,
    SPECIAL_RECOGNITION_TYPES,
    direct_only_deduction_user,
    scoped_hr_attraction_ids,
)

router = APIRouter()


def deduction_download_name(record: DeductionRecord, suffix: str) -> str:
    """Use event snapshots without changing the stored material or its original name."""
    parts = (record.occurred_on, record.employee_name, record.deduction_type_name)
    safe = [re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', str(value)).strip(' .') or '未记录' for value in parts]
    return '_'.join(safe) + suffix


@router.get("/options")
def options(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    recognition_types = db.query(RecognitionType).filter(RecognitionType.active.is_(True)).order_by(RecognitionType.id).all()
    deduction_types = db.query(DeductionType).filter(DeductionType.active.is_(True)).all()
    deduction_order = {code: index for index, code in enumerate(DEDUCTION_TYPE_ORDER)}
    deduction_types.sort(key=lambda row: (deduction_order.get(row.code, len(deduction_order)), row.id))
    if direct_only_deduction_user(user):
        deduction_types = [row for row in deduction_types if row.code not in DIRECT_HIDDEN_DEDUCTION_CODES]
    role_query = db.query(Role).filter(Role.active.is_(True))
    if "HR_MANAGE" in user.permissions and "SYSTEM_ADMIN" not in user.permissions:
        role_query = role_query.filter(Role.code.in_(CIRCLE_HR_MANAGED_ROLE_CODES))
    circle_rows = (
        db.query(Attraction)
        .filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True))
        .all()
    )
    venue_rows = (
        db.query(Attraction)
        .filter(Attraction.active.is_(True), Attraction.recognition_venue.is_(True))
        .all()
    )
    circles_by_name = {row.name: row for row in circle_rows}
    venues_by_name = {row.name: row for row in venue_rows}
    circles = [circles_by_name[name] for name in EMPLOYEE_CIRCLES if name in circles_by_name]
    venues = [venues_by_name[name] for name in RECOGNITION_VENUES if name in venues_by_name]
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None:
        circles = [row for row in circles if row.id in allowed_attractions]
        allowed_circle_names = {row.name for row in circles}
        venues = [row for row in venues if LEGACY_CIRCLE_BY_VENUE.get(row.name, row.name) in allowed_circle_names]
    circle_payload = [{"id": row.id, "name": row.name} for row in circles]
    return {
        "roles": [{"id": row.id, "code": row.code, "name": row.name} for row in role_query.order_by(Role.rank).all()],
        # Keep the legacy key for existing management screens; it now contains circles only.
        "attractions": circle_payload,
        "employee_circles": circle_payload,
        "recognition_venues": [{"id": row.id, "name": row.name} for row in venues],
        "recognition_types": [
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "fixed_score": float(SPECIAL_RECOGNITION_TYPES[row.code]["score"]) if row.code in SPECIAL_RECOGNITION_TYPES else None,
                "monthly_limit": SPECIAL_RECOGNITION_TYPES[row.code]["monthly_limit"] if row.code in SPECIAL_RECOGNITION_TYPES else None,
                "dedicated_entry": row.code in DEDICATED_RECOGNITION_TYPE_CODES,
            }
            for row in recognition_types
        ],
        "deduction_types": [
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "repeat_check": row.code in REPEAT_CONTROLLED_DEDUCTION_CODES,
            }
            for row in deduction_types
        ],
        "deduction_levels": [{"id": row.id, "code": row.code, "name": row.name, "points": float(row.points)} for row in db.query(DeductionLevel).filter(DeductionLevel.active.is_(True)).order_by(DeductionLevel.points).all()],
    }


@router.get("/files/{file_id}")
def download_file(file_id: int, preview: bool = False, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = db.get(StoredFile, file_id)
    if not row or row.status != "active":
        raise HTTPException(404, "文件不存在")
    authorized = row.uploaded_by == user.id or "SYSTEM_ADMIN" in user.permissions
    if user.role.code == SCOPED_HR_ROLE_CODE:
        managed_attractions = scoped_hr_attraction_ids(db, user) or set()
    else:
        managed_attractions = {
            attraction_id
            for (attraction_id,) in db.query(Attraction.id)
            .filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True))
            .all()
        } if {"DATA_VIEW", "DATA_EXPORT"} & user.permissions else set()
    recognition_link = db.query(RecognitionAttachment).filter(RecognitionAttachment.file_id == file_id).first()
    if recognition_link:
        recognition = db.get(RecognitionRecord, recognition_link.recognition_id)
        authorized = authorized or bool(
            recognition
            and (
                user.id in {recognition.employee_id, recognition.operator_employee_id, recognition.assigned_reviewer_id, recognition.reviewed_by}
                or recognition.employee_id in direct_member_ids(db, user.id)
                or recognition.home_attraction_id in managed_attractions
            )
        )
    deduction = db.query(DeductionRecord).filter(DeductionRecord.document_file_id == file_id).first()
    if deduction:
        authorized = authorized or "DECLARATION_STATS_VIEW" in user.permissions or user.id in {deduction.employee_id, deduction.submitter_id} or deduction.employee_id in direct_member_ids(db, user.id) or deduction.attraction_id_snapshot in managed_attractions
    sick_leave = db.query(SickLeaveRecord).filter(SickLeaveRecord.proof_file_id == file_id).first()
    if sick_leave:
        sick_employee = db.get(Employee, sick_leave.employee_id)
        authorized = authorized or user.id in {sick_leave.employee_id, sick_leave.submitted_by} or sick_leave.employee_id in direct_member_ids(db, user.id) or bool((sick_leave.attraction_id_snapshot or (sick_employee.attraction_id if sick_employee else None)) in managed_attractions)
    if not authorized:
        raise HTTPException(403, "没有权限查看该文件")
    path = (FILE_DIR / row.storage_key).resolve()
    if FILE_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(404, "文件不存在")
    suffix = path.suffix.lower()
    image_suffixes = PREVIEW_IMAGE_EXTENSIONS
    preview_suffixes = image_suffixes | PREVIEW_PDF_EXTENSIONS
    if preview and suffix not in preview_suffixes:
        raise HTTPException(415, "该材料不是可预览图片或PDF")
    cache_hit = False
    try:
        if suffix == ".pdf":
            content = watermark_pdf(path, user.employee.employee_no)
            media_type = "application/pdf"
        elif suffix in image_suffixes:
            if preview:
                cache_key = f"{row.sha256}:{user.employee.employee_no}:image-preview-v1-1920"
                cached = watermarked_preview_cache.get(cache_key)
                if cached:
                    content, media_type = cached
                    cache_hit = True
                else:
                    content, media_type = watermark_image(path, user.employee.employee_no, max_dimension=1920)
                    watermarked_preview_cache.put(cache_key, content, media_type)
            else:
                content, media_type = watermark_image(path, user.employee.employee_no)
        else:
            raise HTTPException(415, "该文件类型暂不支持安全水印下载")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, "文件水印生成失败，请联系管理员") from exc
    context_type = "recognition" if recognition_link else "deduction" if deduction else "sick_leave" if sick_leave else "stored_file"
    download_name = deduction_download_name(deduction, suffix) if deduction and not preview else row.original_filename
    write_audit(
        db,
        user.employee,
        "查看带水印材料" if preview else "下载带水印材料",
        context_type,
        file_id,
        after={
            "filename": row.original_filename,
            "download_filename": download_name,
            "watermark_account": user.employee.employee_no,
            "delivery_mode": "preview" if preview else "download",
            "preview_cache": "hit" if cache_hit else "miss" if preview else "not_used",
        },
    )
    db.commit()
    inline_preview = preview and (media_type.startswith("image/") or media_type == "application/pdf")
    headers = {
        "Content-Disposition": f"{'inline' if inline_preview else 'attachment'}; filename*=UTF-8''{quote(download_name, safe='')}",
        "Cache-Control": "private, no-store",
        "X-Preview-Cache": "HIT" if cache_hit else "MISS" if preview else "BYPASS",
    }
    return StreamingResponse(BytesIO(content), media_type=media_type, headers=headers)
