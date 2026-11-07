"""Bounded, permission-scoped activity reads; mutations stay in activities.py."""
import json
from sqlalchemy import select, or_, and_, func
from app.activities_models import Activity, ActivityLot, ActivityAward, ActivityBid, ActivityPrize
from app.v2_models import Employee
from app.services import activities as service


def membership(column, value):
    elements = func.json_each(column).table_valued('value')
    return select(1).select_from(elements).where(elements.c.value == value).exists()


def managed(user):
    return or_(Activity.owner_id == user.id, membership(Activity.helpers_json, user.id))


def participated(user):
    awards = select(1).select_from(ActivityAward).join(ActivityLot).where(
        ActivityLot.activity_id == Activity.id, ActivityAward.employee_id == user.id).exists()
    bids = select(1).select_from(ActivityBid).join(ActivityLot).where(
        ActivityLot.activity_id == Activity.id, ActivityBid.employee_id == user.id).exists()
    return or_(awards, bids)


def scoped(db, user):
    public = and_(Activity.state != 'draft', or_(Activity.circles_json == '[]',
        membership(Activity.circles_json, user.employee.attraction_id))) if service.eligible(user) else False
    return db.query(Activity).filter(or_(managed(user), participated(user), public))


def read_event(db, event_id, user):
    event = scoped(db, user).filter(Activity.id == event_id).first()
    if not event:
        service.fail('活动不存在或不在参与范围内', 404)
    return event


def listing(db, user, page, page_size, view):
    query = scoped(db, user)
    if view == 'manage':
        query = query.filter(managed(user))
    elif view == 'mine':
        query = query.filter(participated(user))
    elif view == 'sign':
        query = query.filter(select(1).select_from(ActivityAward).join(ActivityLot).where(
            ActivityLot.activity_id == Activity.id, ActivityAward.employee_id == user.id,
            ActivityAward.state.in_(['issued', 'disputed'])).exists())
    total = query.count()
    items = query.order_by(Activity.id.desc()).offset((page-1)*page_size).limit(page_size).all()
    return {'can_create': service.creator(user), 'can_manage': db.query(scoped(db, user).filter(managed(user)).exists()).scalar(),
        'eligible': service.eligible(user), 'balance': service.balances(db, user.id),
        'pending_signatures': db.query(ActivityAward).filter(ActivityAward.employee_id == user.id,
            ActivityAward.state.in_(['issued','disputed'])).count(),
        'page': page, 'page_size': page_size, 'total': total,
        'items': [{'id': e.id, 'title': e.title, 'kind': e.kind, 'state': e.state,
                   'ends_at': e.ends_at.isoformat(), 'circle_ids': json.loads(e.circles_json)} for e in items]}


def records(db, event, user, page, page_size, status, keyword):
    manager = service.manages(event, user)
    query = db.query(ActivityAward, Employee, ActivityPrize.name).join(ActivityLot,
        ActivityAward.lot_id == ActivityLot.id).join(Employee, Employee.id == ActivityAward.employee_id).outerjoin(
        ActivityPrize, ActivityPrize.id == ActivityAward.prize_id).filter(ActivityLot.activity_id == event.id)
    if not manager:
        query = query.filter(ActivityAward.employee_id == user.id)
    # Counts describe the same search scope, before the selected state is applied.
    if keyword:
        term = keyword.strip().lstrip('#')
        # User input can be longer than SQLite's signed 64-bit primary key.
        number = int(term) if term.isascii() and term.isdigit() else None
        conditions = [ActivityAward.id == number] if number is not None and 0 < number <= 9223372036854775807 else []
        if manager:
            conditions += [Employee.name.contains(keyword, autoescape=True), Employee.employee_no.contains(keyword, autoescape=True)]
        query = query.filter(or_(*conditions) if conditions else False)
    counts = dict(query.with_entities(ActivityAward.state, func.count(ActivityAward.id)).group_by(ActivityAward.state).all())
    if status != 'all':
        query = query.filter(ActivityAward.state == status)
    total = query.count()
    items = query.order_by(ActivityAward.id).offset((page-1)*page_size).limit(page_size).all()
    from app.services.activity_receipts import award_evidence
    evidence = award_evidence(db, [a.id for a, _, _ in items])
    return {'page': page, 'page_size': page_size, 'total': total, 'counts': counts,
        'items': [{'id': a.id, 'lot_id': a.lot_id, 'cost': a.cost, 'state': a.state,
                   'prize': name, 'reason': a.reason, 'overdue': a.state == 'ready' and service.now() > event.claim_until,
                   **evidence.get(a.id, {}),
                   **({'employee_name': employee.name, 'employee_no': employee.employee_no} if manager else {})}
                  for a, employee, name in items]}
