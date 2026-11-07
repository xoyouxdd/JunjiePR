"""All activity mutations run under SQLite BEGIN IMMEDIATE in the caller.

Shanghai wall-clock times are stored explicitly, independent of the host timezone.
Settlement is atomic and restartable; awarded prizes are never re-drawn.
"""
from datetime import datetime, timedelta, timezone
import json
import secrets

from fastapi import HTTPException
from sqlalchemy import case, func
from app.activities_models import Activity, ActivityLot, ActivityPrize, ActivityAward, ActivityBid, ActivityPrizePhoto
from app.activity_points_models import ActivityPointsEntry
from app.services.activity_points import LEVELS
from app.v2_models import AuditLog, Employee, Attraction
from app.employee_numbers import employee_number


def now():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def fail(message, code=409):
    raise HTTPException(code, message)


def eligible(user):
    return user.base_role.code in LEVELS and not user.role.code.startswith('HR')


def creator(user):
    return eligible(user) and user.has_role('GSM', 'TA_GSM', 'AM', 'OM')


def manages(event, user):
    return event.owner_id == user.id or user.id in json.loads(event.helpers_json)


def visible(event, user):
    return manages(event, user) or (eligible(user) and event.state != 'draft' and
        (not json.loads(event.circles_json) or user.employee.attraction_id in json.loads(event.circles_json)))


def balances(db, employee_id):
    # One SQL statement gives the ledger and holds the same SQLite snapshot.
    # Two separate reads could straddle settlement and overstate spendable points.
    holds = db.query(func.coalesce(func.sum(ActivityLot.highest_price), 0)).filter(
        ActivityLot.highest_employee_id == employee_id, ActivityLot.state == 'open').scalar_subquery()
    balance, earned, frozen = db.query(
        func.coalesce(func.sum(ActivityPointsEntry.delta), 0),
        func.coalesce(func.sum(case((ActivityPointsEntry.kind.in_(['recognition', 'legacy_import']), ActivityPointsEntry.delta), else_=0)), 0),
        holds,
    ).filter(ActivityPointsEntry.employee_id == employee_id).one()
    return {'balance': int(balance), 'earned': int(earned), 'frozen': int(frozen), 'available': int(balance - frozen)}


def audit(db, user, event, action, detail):
    db.add(AuditLog(operator_id=user.id if user else None, operator_name=user.name if user else '活动自动结算',
        action=action, entity_type='activity', entity_id=event.id,
        after_json=json.dumps(detail, ensure_ascii=False), created_at=now()))


def movement(db, award, event, delta, kind):
    db.add(ActivityPointsEntry(employee_id=award.employee_id, recognition_id=None, revision=1,
        delta=delta, kind=kind, origin='activity', business_date=now().date().isoformat(),
        detail_json=json.dumps({'category': event.title, 'signer_name': '活动退款' if delta > 0 else '活动消费',
            'activity_id': event.id, 'lot_id': award.lot_id, 'award_id': award.id,
            'reason': award.reason}, ensure_ascii=False)))


def event_for(db, event_id, user):
    event = db.get(Activity, event_id)
    if not event or not visible(event, user):
        fail('活动不存在或不在参与范围内', 404)
    return event


def lot_for(db, lot_id, user):
    lot = db.get(ActivityLot, lot_id)
    if not lot:
        fail('奖池或拍品不存在', 404)
    return lot, event_for(db, lot.activity_id, user)


def participate(event, lot, user):
    if not eligible(user) or manages(event, user):
        fail('管理人员不能参与本人管理的活动，HR身份不参与积点消费', 403)
    if event.state != 'published' or lot.state != 'open' or not event.starts_at <= now() < lot.ends_at:
        fail('活动尚未开放或已经结束')


def create(db, user, payload):
    if not creator(user):
        fail('仅GSM级别及以上业务身份可创建活动', 403)
    if not payload.starts_at < payload.ends_at < payload.claim_until:
        fail('开始、截止、领取截止时间必须依次递增', 422)
    circle_ids = sorted(set(payload.circle_ids))
    if circle_ids and db.query(Attraction).filter(Attraction.id.in_(circle_ids), Attraction.active.is_(True),
                                                Attraction.employee_circle.is_(True)).count() != len(circle_ids):
        fail('参与圈别不存在或已停用', 422)
    helpers = set(payload.helper_ids)
    for employee_no in payload.helper_employee_nos:
        employee = db.query(Employee).filter_by(employee_no=employee_number(employee_no), is_active=True).first()
        if not employee:
            fail(f'协助人员工号不存在或已停用：{employee_no}', 422)
        helpers.add(employee.id)
    helpers = sorted(helpers - {user.id})
    if helpers and db.query(Employee).filter(Employee.id.in_(helpers), Employee.is_active.is_(True)).count() != len(helpers):
        fail('协助人员不存在或已停用', 422)
    event = Activity(owner_id=user.id, title=payload.title.strip(), description=payload.description,
        kind=payload.kind, circles_json=json.dumps(circle_ids), helpers_json=json.dumps(helpers),
        starts_at=payload.starts_at, ends_at=payload.ends_at, claim_until=payload.claim_until,
        location=payload.location.strip())
    db.add(event)
    db.flush()
    for spec in payload.lots:
        deadline = spec.ends_at or payload.ends_at
        if not payload.starts_at < deadline <= payload.ends_at:
            fail('奖池或拍品截止时间必须在活动开放时间内', 422)
        if payload.kind == 'auction' and (len(spec.prizes) != 1 or spec.prizes[0].quantity != 1):
            fail('每个竞拍单元必须对应一件奖品', 422)
        lot = ActivityLot(activity_id=event.id, name=spec.name.strip(), price=spec.price,
            per_person=spec.per_person, step=spec.step, extend_seconds=spec.extend_seconds, ends_at=deadline)
        db.add(lot)
        db.flush()
        for prize in spec.prizes:
            row = ActivityPrize(lot_id=lot.id, name=prize.name.strip(), description=prize.description, quantity=prize.quantity)
            db.add(row)
            db.flush()
            if prize.photo_id:
                photo = db.get(ActivityPrizePhoto, prize.photo_id)
                if not photo or photo.uploaded_by != user.id or photo.prize_id is not None:
                    fail('奖品照片不存在、已被使用或不属于本人，请重新选择照片', 422)
                photo.prize_id = row.id
                # Flush before checking another prize, preventing the same token being bound twice.
                db.flush()
    audit(db, user, event, '创建积点活动', {'title': event.title})
    return {'id': event.id}


def publish(db, event, user):
    if event.owner_id != user.id or not creator(user):
        fail('仅发起GSM可发布活动', 403)
    if event.state != 'draft' or event.ends_at <= now():
        fail('活动已发布或截止时间已过')
    event.state = 'published'
    audit(db, user, event, '发布积点活动', {})
    return {'id': event.id, 'state': event.state}


def join(db, lot, event, user, count):
    participate(event, lot, user)
    if event.kind != 'draw':
        fail('该活动需要竞价')
    purchased = db.query(ActivityAward).filter_by(lot_id=lot.id).count()
    personal = db.query(ActivityAward).filter_by(lot_id=lot.id, employee_id=user.id).count()
    capacity = int(db.query(func.sum(ActivityPrize.quantity)).filter_by(lot_id=lot.id).scalar() or 0)
    if personal + count > lot.per_person:
        fail('已超过个人参与次数限制')
    if purchased + count > capacity:
        fail('奖品剩余份数不足')
    if balances(db, user.id)['available'] < count * lot.price:
        fail('可用积点不足')
    ids = []
    for _ in range(count):
        award = ActivityAward(lot_id=lot.id, employee_id=user.id, cost=lot.price)
        db.add(award)
        db.flush()
        movement(db, award, event, -lot.price, 'activity_spend')
        ids.append(award.id)
    audit(db, user, event, '参与必得抽奖', {'lot_id': lot.id, 'numbers': ids, 'cost': count * lot.price})
    return {'numbers': ids, 'cost': count * lot.price}


def bid(db, lot, event, user, amount):
    participate(event, lot, user)
    if event.kind != 'auction':
        fail('该奖池不接受竞价')
    minimum = lot.highest_price + lot.step if lot.highest_employee_id else lot.price
    if amount < minimum:
        fail(f'最低有效出价为{minimum}积点')
    own_hold = lot.highest_price if lot.highest_employee_id == user.id else 0
    funds = balances(db, user.id)
    if funds['available'] < 0 or funds['available'] + own_hold < amount:
        fail('可用积点不足')
    lot.highest_employee_id, lot.highest_price = user.id, amount
    if lot.extend_seconds and (lot.ends_at - now()).total_seconds() <= lot.extend_seconds:
        lot.ends_at = now() + timedelta(seconds=lot.extend_seconds)
        # Preserve a practical pickup window even after repeated extensions.
        event.claim_until = max(event.claim_until, lot.ends_at + timedelta(days=1))
    db.add(ActivityBid(lot_id=lot.id, employee_id=user.id, amount=amount))
    audit(db, user, event, '积点竞价', {'lot_id': lot.id, 'amount': amount, 'ends_at': lot.ends_at.isoformat()})
    return {'amount': amount, 'ends_at': lot.ends_at.isoformat()}


def settle_lot(db, lot, event):
    if lot.state != 'open' or event.state != 'published' or lot.ends_at > now():
        return
    prizes = db.query(ActivityPrize).filter_by(lot_id=lot.id).order_by(ActivityPrize.id).all()
    if event.kind == 'draw':
        awards = db.query(ActivityAward).filter_by(lot_id=lot.id, state='pending').order_by(ActivityAward.id).all()
        stock = [p.id for p in prizes for _ in range(p.quantity)]
        if len(stock) < len(awards):
            raise RuntimeError(f'奖池{lot.id}库存约束异常，停止结算')
        secrets.SystemRandom().shuffle(stock)
        for award, prize_id in zip(awards, stock):
            award.prize_id, award.state = prize_id, 'ready'
        lot.state = 'settled'
    elif lot.highest_employee_id:
        award = ActivityAward(lot_id=lot.id, employee_id=lot.highest_employee_id, cost=lot.highest_price,
                              prize_id=prizes[0].id, state='ready')
        db.add(award)
        db.flush()
        movement(db, award, event, -award.cost, 'activity_spend')
        lot.state = 'settled'
    else:
        lot.state = 'unsold'
    audit(db, None, event, '活动自动开奖或成交', {'lot_id': lot.id, 'state': lot.state})
    db.flush()


def cancel_lot(db, lot, event, user, reason):
    if event.owner_id != user.id:
        fail('仅发起GSM可取消奖池或拍品', 403)
    if lot.state != 'open':
        fail('已经开奖或成交，请对无法发放的奖品逐笔退款')
    if event.state == 'published' and lot.ends_at <= now():
        fail('奖池或拍品已截止，不能取消；请等待开奖或成交后逐笔处理')
    for award in db.query(ActivityAward).filter_by(lot_id=lot.id, state='pending').all():
        award.state, award.reason = 'refunded', reason
        movement(db, award, event, award.cost, 'activity_refund')
    lot.state = 'cancelled'
    audit(db, user, event, '取消奖池或拍品', {'lot_id': lot.id, 'reason': reason})


def fulfill(db, award, event, user, action, reason, verified_return=False):
    if not manages(event, user):
        fail('没有发奖权限', 403)
    from app.services import activity_receipts as receipts
    if action in {'issue', 'claim'}:
        # Older clients cannot bypass employee signature by posting the old claim action.
        return receipts.issue(db, award, event, user, reason)
    if action in {'recall', 'request_sign', 'return_refund'}:
        return receipts.resolve(db, award, event, user, action, reason, verified_return)
    if action == 'refund' and user.id != event.owner_id:
        fail('仅发起GSM可办理退款', 403)
    if action == 'correct':
        if user.id != event.owner_id:
            fail('仅发起GSM可纠正误核销或误关闭', 403)
        if award.state not in {'claimed', 'expired'}:
            fail('仅已领取或逾期关闭记录可纠正')
        previous = award.state
        award.state, award.reason = 'ready', reason
        if event.state == 'completed':
            event.state = 'published'
        audit(db, user, event, '纠正活动核销状态', {'award_id': award.id, 'before': previous, 'reason': reason})
        return {'id': award.id, 'state': award.state}
    if award.state != 'ready':
        fail('仅待领取奖品可核销或退款；已处理记录不能重复操作')
    if action == 'refund':
        award.state, award.reason = 'refunded', reason
        movement(db, award, event, award.cost, 'activity_refund')
    elif action == 'expire':
        if now() <= event.claim_until:
            fail('尚未超过领取截止时间')
        award.state, award.reason = 'expired', reason
    audit(db, user, event, {'refund': '活动缺货退款', 'expire': '活动逾期关闭'}[action],
          {'award_id': award.id, 'employee_id': award.employee_id, 'cost': award.cost, 'reason': reason})
    return {'id': award.id, 'state': award.state}


def detail(db, event, user, *, include_awards=True):
    manager = manages(event, user)
    lots = db.query(ActivityLot).filter_by(activity_id=event.id).order_by(ActivityLot.id).all()
    result = {'id': event.id, 'title': event.title, 'description': event.description, 'kind': event.kind,
        'state': event.state, 'starts_at': event.starts_at.isoformat(), 'ends_at': event.ends_at.isoformat(),
        'claim_until': event.claim_until.isoformat(), 'location': event.location,
        'circle_ids': json.loads(event.circles_json), 'helper_ids': json.loads(event.helpers_json) if manager else [],
        'manager': manager, 'owner': event.owner_id == user.id, 'server_time': now().isoformat(),
        'in_scope': not json.loads(event.circles_json) or user.employee.attraction_id in json.loads(event.circles_json), 'lots': []}
    for lot in lots:
        prizes = db.query(ActivityPrize).filter_by(lot_id=lot.id).all()
        photos = {pid: f'/api/activities/photos/{photo_id}' for pid, photo_id in db.query(
            ActivityPrizePhoto.prize_id, ActivityPrizePhoto.id).join(ActivityPrize,
            ActivityPrizePhoto.prize_id == ActivityPrize.id).filter(ActivityPrize.lot_id == lot.id)}
        awards_query = db.query(ActivityAward).filter_by(lot_id=lot.id)
        awards = (awards_query.all() if manager else awards_query.filter_by(employee_id=user.id).all()) if include_awards else []
        names = {p.id: p.name for p in prizes}
        from app.services.activity_receipts import award_evidence
        evidence = award_evidence(db, [a.id for a in awards])
        result['lots'].append({'id': lot.id, 'name': lot.name, 'price': lot.price, 'step': lot.step,
            'per_person': lot.per_person, 'extend_seconds': lot.extend_seconds, 'state': lot.state,
            'ends_at': lot.ends_at.isoformat(), 'highest_price': lot.highest_price,
            'leading': lot.highest_employee_id == user.id,
            'remaining': sum(p.quantity for p in prizes) - awards_query.count() if event.kind == 'draw' else None,
            'my_count': awards_query.filter_by(employee_id=user.id).count(),
            'prizes': [{'name': p.name, 'description': p.description, 'quantity': p.quantity,
                        'photo_url': photos.get(p.id)} for p in prizes],
            'awards': [{'id': a.id, 'cost': a.cost, 'state': a.state, 'prize': names.get(a.prize_id),
                'reason': a.reason, 'photo_url': photos.get(a.prize_id), 'overdue': a.state == 'ready' and now() > event.claim_until,
                **evidence.get(a.id, {}),
                **({'employee_name': db.get(Employee, a.employee_id).name,
                    'employee_no': db.get(Employee, a.employee_id).employee_no} if manager else {})} for a in awards],
            'bids': [{'amount': b.amount, 'at': b.created_at.isoformat(), 'mine': b.employee_id == user.id,
                **({'employee_name': db.get(Employee, b.employee_id).name} if manager else {})}
                for b in db.query(ActivityBid).filter(ActivityBid.lot_id == lot.id,
                    True if manager else ActivityBid.employee_id == user.id).order_by(ActivityBid.id.desc()).limit(100)]})
    if manager:
        totals = db.query(ActivityAward.state, func.count(ActivityAward.id), func.sum(ActivityAward.cost)).join(
            ActivityLot, ActivityAward.lot_id == ActivityLot.id).filter(ActivityLot.activity_id == event.id).group_by(ActivityAward.state).all()
        counts = {state: count for state, count, cost in totals}
        charged = sum(cost for state, count, cost in totals)
        refunded = sum(cost for state, count, cost in totals if state == 'refunded')
        result['summary'] = {'records': sum(counts.values()), 'charged': charged, 'refunded': refunded,
            'net_used': charged - refunded, 'ready': counts.get('ready', 0),
            'claimed': counts.get('signed', 0), 'legacy_claimed': counts.get('claimed', 0),
            'issued': counts.get('issued', 0), 'disputed': counts.get('disputed', 0),
            'frozen': sum(lot.highest_price for lot in lots if lot.state == 'open')}
    return result
