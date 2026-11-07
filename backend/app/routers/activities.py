"""Authenticated activity API; serialization and idempotency cover every mutation."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from io import BytesIO
import json
import uuid
from fastapi import APIRouter, Depends, Response, Request, UploadFile, File, Query
from starlette.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, ConfigDict
from sqlalchemy import text
from app.v2_auth import current_user
from app.v2_database import get_db
from app.activities_models import Activity, ActivityLot, ActivityAward, ActivityOperation, ActivityPrizePhoto, ActivityPrize, ActivityBid
from app.services import activities as service
from app.services.activity_points import digest

router = APIRouter(prefix='/activities')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class RequestKey(StrictModel):
    request_key: str = Field(min_length=8, max_length=80)


class PrizeSpec(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=2000)
    quantity: int = Field(ge=1, le=1000, strict=True)
    photo_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')


class LotSpec(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    price: int = Field(ge=1, le=1000000, strict=True)
    per_person: int = Field(default=1, ge=1, le=100, strict=True)
    step: int = Field(default=1, ge=1, le=1000000, strict=True)
    extend_seconds: Literal[0, 60] = 60
    ends_at: datetime | None = None
    prizes: list[PrizeSpec] = Field(min_length=1, max_length=30)

    @field_validator('ends_at')
    @classmethod
    def shanghai(cls, value):
        return local_time(value) if value else None


def local_time(value):
    return value.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None) if value.tzinfo else value


class CreateActivity(RequestKey):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=5000)
    kind: Literal['draw', 'auction']
    circle_ids: list[int] = Field(default_factory=list, max_length=100)
    helper_ids: list[int] = Field(default_factory=list, max_length=30)
    helper_employee_nos: list[str] = Field(default_factory=list, max_length=30)
    starts_at: datetime
    ends_at: datetime
    claim_until: datetime
    location: str = Field(min_length=1, max_length=300)
    lots: list[LotSpec] = Field(min_length=1, max_length=20)

    @field_validator('starts_at', 'ends_at', 'claim_until')
    @classmethod
    def shanghai(cls, value):
        return local_time(value)


class Join(RequestKey):
    count: int = Field(ge=1, le=100, strict=True)


class Bid(RequestKey):
    amount: int = Field(ge=1, le=1000000000, strict=True)


class Reason(RequestKey):
    reason: str = Field(min_length=1, max_length=1000)


class Fulfill(Reason):
    action: Literal['issue', 'claim', 'refund', 'expire', 'correct', 'recall', 'request_sign', 'return_refund']
    verified_return: bool = Field(default=False, strict=True)


class SignatureRequest(RequestKey):
    revision: int = Field(ge=1, strict=True)
    snapshot_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    accepted: bool = Field(strict=True)
    signature: str = Field(min_length=30, max_length=280000)


@router.post('/photos')
async def upload_photo(file: UploadFile = File(...), db=Depends(get_db), user=Depends(current_user)):
    if not service.creator(user):
        service.fail('仅活动发起业务身份可上传奖品照片', 403)
    from app.services.activity_photos import MAX_UPLOAD, normalize_photo
    try:
        data = await file.read(MAX_UPLOAD+1)
    finally:
        await file.close()
    jpeg, width, height = await run_in_threadpool(normalize_photo, data)
    photo = ActivityPrizePhoto(id=uuid.uuid4().hex, uploaded_by=user.id, jpeg=jpeg, width=width, height=height)
    result = {'id': photo.id, 'url': f'/api/activities/photos/{photo.id}', 'width': width, 'height': height, 'bytes': len(jpeg)}
    try:
        # Only abandoned uploads are removed. Bound prize photos remain historical evidence.
        db.query(ActivityPrizePhoto).filter(ActivityPrizePhoto.prize_id.is_(None),
            ActivityPrizePhoto.created_at < datetime.now()-timedelta(days=7)).delete(synchronize_session=False)
        db.add(photo)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


@router.get('/photos/{photo_id}')
def read_photo(photo_id: str, request: Request, db=Depends(get_db), user=Depends(current_user)):
    photo = db.get(ActivityPrizePhoto, photo_id)
    if not photo:
        service.fail('照片不存在', 404)
    allowed = photo.uploaded_by == user.id if photo.prize_id is None else False
    if photo.prize_id is not None:
        prize = db.get(ActivityPrize, photo.prize_id)
        lot = db.get(ActivityLot, prize.lot_id)
        event = db.get(Activity, lot.activity_id)
        allowed = service.visible(event, user) or bool(db.query(ActivityAward.id).join(ActivityLot,
            ActivityAward.lot_id == ActivityLot.id).filter(ActivityLot.activity_id == event.id,
            ActivityAward.employee_id == user.id).first()) or bool(db.query(ActivityBid.id).join(ActivityLot,
            ActivityBid.lot_id == ActivityLot.id).filter(ActivityLot.activity_id == event.id,
            ActivityBid.employee_id == user.id).first())
    if not allowed:
        service.fail('照片不存在或无权查看', 404)
    # Revalidation avoids retransferring immutable photos on each activity refresh.
    # Authorization must precede 304, including after logout and circle transfers.
    etag = f'"{photo.id}"'
    headers = {'Cache-Control': 'private, no-cache', 'Vary': 'Cookie', 'ETag': etag,
        'Content-Disposition': 'inline; filename="prize.jpg"', 'X-Content-Type-Options': 'nosniff'}
    validators = [value.strip() for value in request.headers.get('if-none-match', '').split(',')]
    if etag in validators or '*' in validators:
        return Response(status_code=304, headers=headers)
    return Response(photo.jpeg, media_type='image/jpeg', headers=headers)


def atomic(db, user, payload, action, operation):
    employee_id = user.id
    fingerprint = digest([action, payload.model_dump(mode='json', exclude={'request_key'})])
    db.rollback()
    try:
        db.execute(text('BEGIN IMMEDIATE'))
        prior = db.query(ActivityOperation).filter_by(employee_id=employee_id, request_key=payload.request_key).first()
        if prior:
            if prior.fingerprint != fingerprint:
                service.fail('同一请求编号不能用于不同操作')
            result = json.loads(prior.result_json)
        else:
            result = operation()
            db.add(ActivityOperation(employee_id=employee_id, request_key=payload.request_key,
                                    fingerprint=fingerprint, result_json=json.dumps(result, ensure_ascii=False)))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def refresh_due(db):
    # Read paths also recover settlement immediately if a worker was interrupted.
    db.rollback()
    from app.activity_worker import process_due
    process_due()


@router.get('')
def listing(response: Response, page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=50),
            view: Literal['all', 'mine', 'manage', 'sign'] = 'all', db=Depends(get_db), user=Depends(current_user)):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Cookie'
    refresh_due(db)
    from app.services.activity_queries import listing as read_listing
    db.execute(text('BEGIN'))
    return read_listing(db, user, page, page_size, view)


@router.get('/{event_id}')
def read_activity(event_id: int, response: Response, db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_queries import read_event
    response.headers.update({'Cache-Control': 'private, no-store', 'Vary': 'Cookie'})
    refresh_due(db)
    db.execute(text('BEGIN'))
    event = read_event(db, event_id, user)
    return service.detail(db, event, user, include_awards=not service.manages(event, user))


@router.get('/{event_id}/records')
def activity_records(event_id: int, response: Response, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
                     status: Literal['all','pending','ready','issued','disputed','signed','claimed','refunded','expired'] = 'ready',
                     keyword: str = Query('', max_length=80), db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_queries import read_event, records
    response.headers.update({'Cache-Control': 'private, no-store', 'Vary': 'Cookie'})
    refresh_due(db)
    db.execute(text('BEGIN'))
    return records(db, read_event(db, event_id, user), user, page, page_size, status, keyword.strip())


@router.get('/{event_id}/export')
def export_receipts(event_id: int, db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_export import export_snapshot, build_workbook
    from app.excel_export_utils import content_disposition
    from app.excel_presentation import save_export_workbook
    from app.v2_watermark import watermark_workbook
    from app.v2_models import AuditLog
    refresh_due(db)
    try:
        db.execute(text('BEGIN'))
        event = service.event_for(db, event_id, user)
        if event.owner_id != user.id:
            service.fail('仅活动发起人可导出领取核销数据', 403)
        data = export_snapshot(db, event)
        operator_id, operator_name, operator_no = user.id, user.name, user.employee.employee_no
    finally:
        db.rollback()
    workbook = build_workbook(data)
    watermark_workbook(workbook, operator_no)
    output = BytesIO()
    save_export_workbook(workbook, output)
    output.seek(0)
    db.add(AuditLog(operator_id=operator_id, operator_name=operator_name, action='导出活动领取核销',
        entity_type='activity', entity_id=str(event_id), created_at=service.now(),
        after_json=json.dumps({'rows': len(data['rows']), 'spent': data['spent'], 'refund': data['refund']}, ensure_ascii=False)))
    db.commit()
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={
        'Content-Disposition': content_disposition(f'activity_{event_id}_receipts.xlsx', f'活动领取核销_{event_id}.xlsx'),
        'Cache-Control': 'private, no-store', 'Vary': 'Cookie'})


@router.post('')
def create(payload: CreateActivity, db=Depends(get_db), user=Depends(current_user)):
    return atomic(db, user, payload, 'create', lambda: service.create(db, user, payload))


@router.post('/{event_id}/publish')
def publish(event_id: int, payload: RequestKey, db=Depends(get_db), user=Depends(current_user)):
    return atomic(db, user, payload, ['publish', event_id],
        lambda: service.publish(db, service.event_for(db, event_id, user), user))


@router.post('/lots/{lot_id}/join')
def join(lot_id: int, payload: Join, db=Depends(get_db), user=Depends(current_user)):
    refresh_due(db)
    def run():
        lot, event = service.lot_for(db, lot_id, user)
        return service.join(db, lot, event, user, payload.count)
    return atomic(db, user, payload, ['join', lot_id], run)


@router.post('/lots/{lot_id}/bid')
def bid(lot_id: int, payload: Bid, db=Depends(get_db), user=Depends(current_user)):
    refresh_due(db)
    def run():
        lot, event = service.lot_for(db, lot_id, user)
        return service.bid(db, lot, event, user, payload.amount)
    return atomic(db, user, payload, ['bid', lot_id], run)


@router.post('/lots/{lot_id}/cancel')
def cancel(lot_id: int, payload: Reason, db=Depends(get_db), user=Depends(current_user)):
    refresh_due(db)
    def run():
        lot, event = service.lot_for(db, lot_id, user)
        service.cancel_lot(db, lot, event, user, payload.reason)
        return {'id': lot.id, 'state': lot.state}
    return atomic(db, user, payload, ['cancel', lot_id], run)


@router.post('/{event_id}/cancel')
def cancel_event(event_id: int, payload: Reason, db=Depends(get_db), user=Depends(current_user)):
    refresh_due(db)
    def run():
        event = service.event_for(db, event_id, user)
        if event.owner_id != user.id:
            service.fail('仅发起GSM可取消活动', 403)
        lots = db.query(ActivityLot).filter_by(activity_id=event.id).all()
        if event.state in {'cancelled', 'completed'} or any(lot.state not in {'open', 'cancelled'} for lot in lots):
            service.fail('已有开奖或成交结果，请逐笔处理，不能整场取消')
        for lot in lots:
            if lot.state == 'open':
                service.cancel_lot(db, lot, event, user, payload.reason)
        event.state = 'cancelled'
        return {'id': event.id, 'state': event.state}
    return atomic(db, user, payload, ['cancel-event', event_id], run)


@router.post('/awards/{award_id}/fulfill')
def fulfill(award_id: int, payload: Fulfill, db=Depends(get_db), user=Depends(current_user)):
    refresh_due(db)
    def run():
        award = db.get(ActivityAward, award_id)
        if not award:
            service.fail('奖品记录不存在', 404)
        lot, event = service.lot_for(db, award.lot_id, user)
        return service.fulfill(db, award, event, user, payload.action, payload.reason, payload.verified_return)
    return atomic(db, user, payload, ['fulfill', award_id], run)


@router.get('/awards/{award_id}/signing')
def signing_context(award_id: int, response: Response, db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_receipts import signing_context as context
    response.headers.update({'Cache-Control': 'private, no-store', 'Vary': 'Cookie'})
    return context(db, award_id, user)


@router.post('/awards/{award_id}/sign')
def sign_receipt(award_id: int, payload: SignatureRequest, db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_receipts import sign
    return atomic(db, user, payload, ['sign', award_id], lambda: sign(db, award_id, user, payload))


@router.post('/awards/{award_id}/dispute')
def dispute_receipt(award_id: int, payload: Reason, db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_receipts import dispute
    return atomic(db, user, payload, ['dispute', award_id], lambda: dispute(db, award_id, user, payload.reason))


@router.get('/receipts/{receipt_id}')
def receipt_archive(receipt_id: str, response: Response, db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_receipts import receipt_for, receipt_data
    response.headers.update({'Cache-Control': 'private, no-store', 'Vary': 'Cookie'})
    receipt, award = receipt_for(db, receipt_id, user)
    return receipt_data(receipt, award)


@router.get('/receipts/{receipt_id}/signature')
def receipt_signature(receipt_id: str, db=Depends(get_db), user=Depends(current_user)):
    from app.services.activity_receipts import receipt_for
    receipt, award = receipt_for(db, receipt_id, user)
    return Response(receipt.signature_png, media_type='image/png', headers={
        'Cache-Control': 'private, no-store', 'Vary': 'Cookie', 'X-Content-Type-Options': 'nosniff'})


@router.post('/{event_id}/complete')
def complete(event_id: int, payload: RequestKey, db=Depends(get_db), user=Depends(current_user)):
    refresh_due(db)
    def run():
        event = service.event_for(db, event_id, user)
        if event.owner_id != user.id:
            service.fail('仅发起GSM可结案', 403)
        if event.state != 'published' or db.query(ActivityLot).filter_by(activity_id=event.id, state='open').count():
            service.fail('仍有未结束的奖池或拍品')
        if db.query(ActivityAward).join(ActivityLot, ActivityAward.lot_id == ActivityLot.id).filter(
            ActivityLot.activity_id == event.id, ActivityAward.state.in_(['pending', 'ready', 'issued', 'disputed'])).count():
            service.fail('仍有待开奖、待领取、待本人签收或签收异常记录')
        from app.services.activity_receipts import ensure_archives_complete
        ensure_archives_complete(db, event.id)
        event.state = 'completed'
        service.audit(db, user, event, '活动结案', {})
        return {'id': event.id, 'state': event.state}
    return atomic(db, user, payload, ['complete', event_id], run)
