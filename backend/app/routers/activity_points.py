"""Private personal card and administrator-only historical reconciliation."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.v2_auth import current_user
from app.v2_database import get_db
from app.v2_models import AuditLog
from app.services import activity_points as points
from app.services import legacy_points as legacy

router = APIRouter()


def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"


def admin(user):
    if "SYSTEM_ADMIN" not in user.permissions:
        raise HTTPException(403, "仅系统管理员可执行历史积点补计")


@router.get("/activity-points/me")
def my_card(response: Response, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
            db=Depends(get_db), user=Depends(current_user)):
    private(response)
    eligible = user.base_role.code in points.LEVELS and not user.role.code.startswith("HR")
    if not eligible:
        return {"eligible": False}
    return {"eligible": True, **points.personal_card(db, user.id, page, page_size)}


@router.get("/activity-points/backfill-preview")
def preview(response: Response, db=Depends(get_db), user=Depends(current_user)):
    private(response)
    admin(user)
    return points.backfill_preview(db)


class BackfillRequest(BaseModel):
    fingerprint: str = Field(min_length=64, max_length=64)


@router.post("/activity-points/backfill")
def apply(payload: BackfillRequest, db=Depends(get_db), user=Depends(current_user)):
    admin(user)
    db.rollback()
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        result = points.backfill_apply(db, payload.fingerprint)
        db.add(AuditLog(operator_id=user.id, operator_name=user.name,
            action="历史活动积点补计", entity_type="activity_points", after_json=json.dumps(result, ensure_ascii=False)))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def legacy_locked(db, operation):
    db.rollback()
    try:
        db.execute(text('BEGIN IMMEDIATE'))
        result = operation()
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@router.get('/activity-points/legacy/options')
def legacy_options(response: Response, db=Depends(get_db), user=Depends(current_user)):
    private(response)
    return {'circles': [{'id': c.id, 'name': c.name} for c in legacy.circles(db, user)]}


@router.get('/activity-points/legacy/roster')
def legacy_roster(circle_id: int, response: Response, db=Depends(get_db), user=Depends(current_user)):
    private(response)
    return {'items': [legacy.public_row(r) for r in legacy.roster(db, user, circle_id)]}


@router.post('/activity-points/legacy/export')
def legacy_export(circle_id: int, include_archived: bool = False, db=Depends(get_db), user=Depends(current_user)):
    from urllib.parse import quote
    from app.services.legacy_points_xlsx import workbook
    def operation():
        batch, rows = legacy.make_export(db, user, circle_id)
        if not include_archived:
            rows = [r for r in rows if r['status'] == '在职']
            batch.roster_json = json.dumps(rows, ensure_ascii=False)
        return workbook(batch, rows)
    content = legacy_locked(db, operation)
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': "attachment; filename*=UTF-8''"+quote('历史积点补录名单.xlsx'),
                 'Cache-Control': 'private, no-store', 'Vary': 'Cookie'})


@router.post('/activity-points/legacy/preview')
async def legacy_preview(circle_id: int, file: UploadFile = File(...), db=Depends(get_db), user=Depends(current_user)):
    legacy.formal_gsm(db, user)
    if not (file.filename or '').lower().endswith('.xlsx'):
        raise HTTPException(400, '请上传本页面导出的.xlsx名单')
    content = await file.read(5*1024*1024+1)
    return legacy_locked(db, lambda: legacy.preview(db, user, circle_id, content))


class LegacyApplyRequest(BaseModel):
    preview_id: str = Field(min_length=32, max_length=32)


@router.post('/activity-points/legacy/apply')
def legacy_apply(payload: LegacyApplyRequest, db=Depends(get_db), user=Depends(current_user)):
    return legacy_locked(db, lambda: legacy.apply(db, user, payload.preview_id))
