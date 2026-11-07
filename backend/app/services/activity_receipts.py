"""Two-party fulfillment with immutable per-award signature evidence."""
import base64
from hashlib import sha256
from io import BytesIO
from datetime import datetime
import json
import uuid
from PIL import Image, UnidentifiedImageError
from app.activities_models import Activity, ActivityLot, ActivityPrize, ActivityPrizePhoto, ActivityAward, ActivityDispatch, ActivityReceipt
from app.v2_models import Employee
from app.services import activities as service
from app.services.activity_points import digest

CONFIRMATION = '本人已收到上述奖品，确认无误。'


def issue(db, award, event, user, reason):
    if award.state != 'ready':
        service.fail('仅待领取奖品可发放，不能重复发放')
    if db.query(ActivityReceipt.id).filter_by(award_id=award.id).first():
        service.fail('已有签收档案，不能重新发放')
    prize = db.get(ActivityPrize, award.prize_id) if award.prize_id else None
    if not prize:
        service.fail('奖品尚未确定，不能发放')
    employee, lot = db.get(Employee, award.employee_id), db.get(ActivityLot, award.lot_id)
    photo = db.query(ActivityPrizePhoto.id).filter_by(prize_id=prize.id).first()
    dispatch = db.get(ActivityDispatch, award.id)
    if not dispatch:
        dispatch = ActivityDispatch(award_id=award.id, revision=0)
        db.add(dispatch)
    dispatch.revision += 1
    dispatch.issued_by, dispatch.issued_name, dispatch.issued_at = user.id, user.name, service.now()
    snapshot = {'activity_id': event.id, 'activity': event.title, 'award_id': award.id,
        'employee_id': employee.id, 'employee_name': employee.name, 'employee_no': employee.employee_no,
        'lot': lot.name, 'prize': prize.name, 'quantity': 1, 'cost': award.cost,
        'photo_url': f'/api/activities/photos/{photo.id}' if photo else None,
        'issued_by': user.id, 'issued_name': user.name, 'issued_at': dispatch.issued_at.isoformat()}
    dispatch.snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    award.state, award.reason = 'issued', reason
    service.audit(db, user, event, '活动奖品发放待签收', {'award_id': award.id, 'revision': dispatch.revision, 'snapshot': snapshot, 'reason': reason})
    return {'id': award.id, 'state': award.state}


def owner_award(db, award_id, user):
    award = db.get(ActivityAward, award_id)
    if not award or award.employee_id != user.id:
        service.fail('仅奖品接收员工本人可操作', 403)
    lot = db.get(ActivityLot, award.lot_id)
    return award, db.get(Activity, lot.activity_id)


def signing_context(db, award_id, user):
    award, event = owner_award(db, award_id, user)
    dispatch = db.get(ActivityDispatch, award.id)
    if award.state != 'issued' or not dispatch:
        service.fail('当前不是待本人签收状态，请刷新记录')
    try:
        snapshot = json.loads(dispatch.snapshot_json)
    except (ValueError, TypeError):
        service.fail('发放记录无法读取，请联系发起GSM核对')
    if not isinstance(snapshot, dict) or snapshot.get('recalled') or any([
        snapshot.get('award_id') != award.id, snapshot.get('employee_id') != user.id,
        snapshot.get('activity_id') != event.id, snapshot.get('cost') != award.cost,
        snapshot.get('issued_by') != dispatch.issued_by, snapshot.get('issued_name') != dispatch.issued_name,
        snapshot.get('issued_at') != dispatch.issued_at.isoformat(),
    ]):
        service.fail('发放信息与本人凭证不一致，请联系发起GSM核对')
    return {'revision': dispatch.revision, 'snapshot_hash': digest([snapshot, dispatch.revision, CONFIRMATION]),
            'snapshot': snapshot, 'confirmation': CONFIRMATION}


def signature_png(value):
    if not value.startswith('data:image/png;base64,'):
        service.fail('请在签名区域手写签字', 422)
    try:
        data = base64.b64decode(value.split(',', 1)[1], validate=True)
        if not data or len(data) > 200*1024:
            service.fail('签名图片为空或过大，请重新签字', 422)
        with Image.open(BytesIO(data)) as image:
            if image.format != 'PNG' or not (200 <= image.width <= 2048 and 80 <= image.height <= 1024):
                service.fail('签名图片尺寸或格式无效', 422)
            rgba = image.convert('RGBA')
            clean = Image.new('RGB', image.size, 'white')
            clean.paste(rgba, mask=rgba.getchannel('A'))
            ink = clean.convert('L').point(lambda p: 255 if p < 200 else 0)
            box = ink.getbbox()
            if not box or box[2]-box[0] < 20 or box[3]-box[1] < 8 or ink.histogram()[255] < 80:
                service.fail('签名为空或笔画不足，请手写完整签名', 422)
            output = BytesIO()
            clean.save(output, 'PNG', optimize=True)
            return output.getvalue()
    except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        service.fail('签名图片无法读取，请重新签字', 422)


def sign(db, award_id, user, payload):
    context = signing_context(db, award_id, user)
    if not payload.accepted:
        service.fail('请确认本人已收到奖品', 422)
    if payload.revision != context['revision'] or payload.snapshot_hash != context['snapshot_hash']:
        service.fail('发放记录已变化，请重新核对后签字')
    award, event = owner_award(db, award_id, user)
    if db.query(ActivityReceipt.id).filter_by(award_id=award_id).first():
        service.fail('该凭证已签收，不能重复提交')
    png = signature_png(payload.signature)
    signed_at = service.now()
    snapshot = {**context['snapshot'], 'signing_proof': {'version': 2, 'award_id': award_id,
        'employee_id': user.id, 'account_id': user.account.id, 'revision': context['revision'],
        'signed_at': signed_at.isoformat()}}
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    receipt = ActivityReceipt(id=uuid.uuid4().hex, award_id=award_id, employee_id=user.id,
        account_id=user.account.id, dispatch_revision=context['revision'], snapshot_json=snapshot_json,
        confirmation=CONFIRMATION, signature_png=png, evidence_hash=sha256(png+snapshot_json.encode()+CONFIRMATION.encode()).hexdigest(), signed_at=signed_at)
    db.add(receipt)
    award.state, award.reason = 'signed', '员工本人已签字确认领取'
    service.audit(db, user, event, '员工本人签字核销', {'award_id': award_id, 'receipt_id': receipt.id,
        'evidence_hash': receipt.evidence_hash, 'revision': context['revision']})
    return {'id': award_id, 'state': 'signed', 'receipt_id': receipt.id}


def dispute(db, award_id, user, reason):
    award, event = owner_award(db, award_id, user)
    if award.state != 'issued':
        service.fail('仅待签收记录可反馈未收到或奖品有误')
    award.state, award.reason = 'disputed', reason
    service.audit(db, user, event, '员工反馈签收异常', {'award_id': award_id, 'reason': reason})
    return {'id': award_id, 'state': award.state}


def resolve(db, award, event, user, action, reason, verified):
    if event.owner_id != user.id:
        service.fail('仅活动发起GSM可处理签收异常', 403)
    dispatch = db.get(ActivityDispatch, award.id)
    if action == 'request_sign':
        if award.state != 'disputed' or not dispatch:
            service.fail('仅签收异常记录可重新申请本人签收')
        award.state = 'issued'
    elif action in {'recall', 'return_refund'}:
        allowed = {'issued', 'disputed'} if action == 'recall' else {'issued', 'disputed', 'signed'}
        if award.state not in allowed or not verified:
            service.fail('请先核实奖品未交付或已收回；已签收记录不能撤回发放', 422)
        if action == 'return_refund':
            award.state = 'refunded'
            award.reason = reason
            service.movement(db, award, event, award.cost, 'activity_refund')
        else:
            award.state = 'ready'
            if dispatch:
                try:
                    withdrawn = json.loads(dispatch.snapshot_json)
                    if not isinstance(withdrawn, dict):
                        raise ValueError('invalid dispatch snapshot')
                except (ValueError, TypeError):
                    service.fail('发放记录异常，请联系活动发起人核对后再撤回发放')
                withdrawn['recalled'] = True
                dispatch.snapshot_json = json.dumps(withdrawn, ensure_ascii=False, sort_keys=True)
    if dispatch:
        dispatch.revision += 1
    award.reason = reason
    if event.state == 'completed':
        event.state = 'published'
    service.audit(db, user, event, {'request_sign': '签收异常处理后请本人重签', 'recall': '撤回误发放',
        'return_refund': '核实交付后退回积点'}[action], {'award_id': award.id, 'reason': reason, 'verified_return': verified})
    return {'id': award.id, 'state': award.state}


def receipt_for(db, receipt_id, user):
    receipt = db.get(ActivityReceipt, receipt_id)
    if not receipt:
        service.fail('签收档案不存在', 404)
    award = db.get(ActivityAward, receipt.award_id)
    event = db.get(Activity, db.get(ActivityLot, award.lot_id).activity_id)
    if receipt.employee_id != user.id and not service.manages(event, user):
        service.fail('无权查看签收档案', 403)
    if archive_problem(receipt, award, event.id):
        service.fail('签收档案校验失败，请联系发起GSM核对')
    return receipt, award


def archive_problem(receipt, award, event_id):
    """Same evidence validation for private viewing, exports and completion.

    V1 archives remain readable without rewriting old evidence. V2 also seals
    the account, timestamp and dispatch revision in the hashed snapshot.
    """
    try:
        snapshot = json.loads(receipt.snapshot_json)
        if not isinstance(snapshot, dict) or any([
            receipt.award_id != award.id, receipt.employee_id != award.employee_id,
            snapshot.get('award_id') != award.id, snapshot.get('employee_id') != award.employee_id,
            snapshot.get('activity_id') != event_id, not receipt.signature_png, not receipt.confirmation,
        ]):
            return '签收档案与当前凭证或员工不一致'
        if not snapshot.get('issued_name') or not snapshot.get('issued_at'):
            return '签收档案缺少发放信息'
        datetime.fromisoformat(snapshot['issued_at'])
        expected = sha256(receipt.signature_png+receipt.snapshot_json.encode()+receipt.confirmation.encode()).hexdigest()
        if receipt.evidence_hash != expected:
            return '签名或档案内容校验失败'
        proof = snapshot.get('signing_proof')
        if proof is not None and proof != {'version': 2, 'award_id': award.id,
            'employee_id': receipt.employee_id, 'account_id': receipt.account_id,
            'revision': receipt.dispatch_revision, 'signed_at': receipt.signed_at.isoformat()}:
            return '签收时间、账号或发放版本校验失败'
    except (ValueError, TypeError, AttributeError):
        return '签收档案格式异常'
    return ''


def ensure_archives_complete(db, event_id):
    awards = {a.id: a for a in db.query(ActivityAward).join(ActivityLot, ActivityAward.lot_id == ActivityLot.id).filter(
        ActivityLot.activity_id == event_id)}
    if not awards:
        return
    seen = set()
    for receipt in db.query(ActivityReceipt).filter(ActivityReceipt.award_id.in_(list(awards))).yield_per(25):
        if archive_problem(receipt, awards[receipt.award_id], event_id):
            service.fail('签收档案存在异常，请核对后再结案')
        seen.add(receipt.award_id)
    if any(a.state == 'signed' and a.id not in seen for a in awards.values()):
        service.fail('已签收记录缺少签名档案，请核对后再结案')


def receipt_data(receipt, award):
    return {'id': receipt.id, 'award_id': receipt.award_id, 'snapshot': json.loads(receipt.snapshot_json),
        'confirmation': receipt.confirmation, 'signed_at': receipt.signed_at.isoformat(),
        'signature_url': f'/api/activities/receipts/{receipt.id}/signature',
        'evidence_hash': receipt.evidence_hash, 'current_state': award.state}


def award_evidence(db, award_ids):
    if not award_ids:
        return {}
    result = {}
    for dispatch in db.query(ActivityDispatch.award_id, ActivityDispatch.revision, ActivityDispatch.issued_name,
            ActivityDispatch.issued_at, ActivityDispatch.snapshot_json).filter(ActivityDispatch.award_id.in_(award_ids)):
        result[dispatch.award_id] = {'dispatch_revision': dispatch.revision}
        try:
            snapshot = json.loads(dispatch.snapshot_json)
            if not isinstance(snapshot, dict):
                raise ValueError('invalid dispatch snapshot')
        except (ValueError, TypeError):
            result[dispatch.award_id]['evidence_problem'] = '发放记录异常，请联系活动发起人核对'
            continue
        if not snapshot.get('recalled'):
            result[dispatch.award_id].update({'issued_name': dispatch.issued_name, 'issued_at': dispatch.issued_at.isoformat()})
    for receipt in db.query(ActivityReceipt.id, ActivityReceipt.award_id, ActivityReceipt.signed_at).filter(ActivityReceipt.award_id.in_(award_ids)):
        result.setdefault(receipt.award_id, {}).update({'receipt_id': receipt.id, 'signed_at': receipt.signed_at.isoformat()})
    return result
