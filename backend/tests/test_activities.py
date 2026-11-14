"""Activity money, inventory, access and recovery tests against isolated SQLite."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, event as sqlalchemy_event
from test_supervisor_performance import app, SessionLocal, employee_id, login
from app.activities_models import Activity, ActivityLot, ActivityAward, ActivityBid, ActivityPrize
from app.activity_points_models import ActivityPointsEntry
from app.services import activities as service
from app.activity_worker import process_due
from app.v2_models import Employee, Attraction, AuditLog


def key():
    return str(uuid.uuid4())


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def money(employee_no, amount):
    with SessionLocal() as db:
        eid = employee_id(employee_no)
        balance = service.balances(db, eid)['balance']
        db.add(ActivityPointsEntry(employee_id=eid, revision=1, delta=amount-balance,
            kind='recognition', origin='test', business_date='2026-11-07', detail_json='{}'))
        db.commit()


def payload(kind='draw', **changes):
    current = service.now()
    result = {'request_key': key(), 'title': '测试活动'+key()[:6], 'kind': kind,
        'description': '每次必得；缺货退款', 'starts_at': (current-timedelta(hours=1)).isoformat(),
        'ends_at': (current+timedelta(days=1)).isoformat(), 'claim_until': (current+timedelta(days=3)).isoformat(),
        'location': '测试领取点', 'circle_ids': [], 'lots': [
            {'name': '20点奖池', 'price': 20, 'per_person': 5, 'extend_seconds': 0,
             'prizes': [{'name': '杯子', 'quantity': 1}, {'name': '礼盒', 'quantity': 2}]},
            {'name': '50点奖池', 'price': 50, 'per_person': 2,
             'prizes': [{'name': '大礼包', 'quantity': 2}]}]}
    if kind == 'auction':
        result['lots'] = [{'name': '竞拍礼盒', 'price': 20, 'step': 5, 'extend_seconds': 0,
                          'prizes': [{'name': '礼盒', 'quantity': 1}]}]
    result.update(changes)
    return result


def find(client, event_id):
    response = client.get(f'/api/activities/{event_id}')
    assert response.status_code == 200, response.text
    event = response.json()
    if event['manager']:
        page = 1
        while True:
            response = client.get(f'/api/activities/{event_id}/records',params={'status':'all','page':page,'page_size':50})
            assert response.status_code == 200, response.text
            records = response.json()
            for award in records['items']:
                next(lot for lot in event['lots'] if lot['id']==award['lot_id'])['awards'].append(award)
            if page*50 >= records['total']:
                break
            page += 1
    return event


def create(client, kind='draw', publish=True, **changes):
    login(client, 'GSMTEST01')
    body = payload(kind, **changes)
    response = client.post('/api/activities', json=body)
    assert response.status_code == 200, response.text
    event_id = response.json()['id']
    if publish:
        response = client.post(f'/api/activities/{event_id}/publish', json={'request_key': key()})
        assert response.status_code == 200, response.text
    return find(client, event_id)


def post(client, path, **body):
    return client.post('/api/activities/'+path, json={'request_key': key(), **body})


def expire(lot_id):
    with SessionLocal() as db:
        db.get(ActivityLot, lot_id).ends_at = service.now()-timedelta(seconds=1)
        db.commit()
    process_due()


def test_multi_pool_view_insufficient_guarantee_and_refund(client):
    event = create(client)
    cheap, expensive = event['lots']
    money('CMTEST01', 45)
    login(client, 'CMTEST01')
    assert len(find(client, event['id'])['lots']) == 2
    assert post(client, f"lots/{expensive['id']}/join", count=1).status_code == 409
    body = {'request_key': key(), 'count': 2}
    first = client.post(f"/api/activities/lots/{cheap['id']}/join", json=body)
    assert first.status_code == 200, first.text
    assert client.post(f"/api/activities/lots/{cheap['id']}/join", json=body).json() == first.json()
    card = client.get('/api/activity-points/me').json()
    assert card['available'] == 5 and card['earned'] == 45
    assert card['pending_redemption'] == {'count': 2, 'cost': 40, 'title': None, 'state': None, 'event_id': None}
    body['count'] = 1
    assert client.post(f"/api/activities/lots/{cheap['id']}/join", json=body).status_code == 409
    expire(cheap['id'])
    awards = find(client, event['id'])['lots'][0]['awards']
    assert len(awards) == 2 and all(a['prize'] and a['state'] == 'ready' for a in awards)
    snapshot = [(a['id'], a['prize']) for a in awards]
    process_due()
    assert [(a['id'], a['prize']) for a in find(client, event['id'])['lots'][0]['awards']] == snapshot
    assert post(client, f"awards/{awards[0]['id']}/fulfill", action='refund', reason='缺货').status_code == 403
    login(client, 'GSMTEST01')
    refund_body = {'request_key': key(), 'action': 'refund', 'reason': '现场少一份'}
    path = f"/api/activities/awards/{awards[0]['id']}/fulfill"
    assert client.post(path, json=refund_body).status_code == 200
    assert client.post(path, json=refund_body).status_code == 200
    assert post(client, f"awards/{awards[0]['id']}/fulfill", action='refund', reason='重复').status_code == 409
    assert post(client, f"awards/{awards[0]['id']}/fulfill", action='claim', reason='重复').status_code == 409
    assert post(client, f"awards/{awards[1]['id']}/fulfill", action='claim', reason='本人领取').status_code == 200
    summary = find(client, event['id'])['summary']
    assert summary['net_used'] == 20 and summary['refunded'] == 20 and summary['issued'] == 1 and summary['claimed'] == 0
    login(client, 'CMTEST01')
    card = client.get('/api/activity-points/me').json()
    assert card['available'] == 25 and card['earned'] == 45
    assert card['pending_redemption'] == {'count': 1, 'cost': 20, 'title': event['title'], 'state': 'issued', 'event_id': event['id']}
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action='活动缺货退款', entity_id=str(event['id'])).count() == 1


def test_capacity_and_guaranteed_prize_distribution(client):
    event = create(client)
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/join', count=4).status_code == 409
    assert post(client, f'lots/{lot_id}/join', count=3).status_code == 200
    assert post(client, f'lots/{lot_id}/join', count=1).status_code == 409
    expire(lot_id)
    awards = find(client, event['id'])['lots'][0]['awards']
    assert sorted(a['prize'] for a in awards) == ['杯子', '礼盒', '礼盒']
    login(client, 'GSMTEST01')
    assert post(client, f'{event["id"]}/complete').status_code == 409
    assert post(client, f'lots/{lot_id}/cancel', reason='不能重抽').status_code == 409


def test_auction_holds_outbid_self_raise_and_single_settlement(client):
    event = create(client, 'auction')
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    money('CMTEST02', 100)
    login(client, 'CMTEST01')
    before = client.get('/api/activity-points/me').json()['pending_redemption']
    assert post(client, f'lots/{lot_id}/bid', amount=20).status_code == 200
    card = client.get('/api/activity-points/me').json()
    assert card['available'] == 80 and card['frozen'] == 20
    assert card['pending_redemption'] == before
    assert post(client, f'lots/{lot_id}/bid', amount=25).status_code == 200
    assert client.get('/api/activity-points/me').json()['available'] == 75
    login(client, 'CMTEST02')
    assert post(client, f'lots/{lot_id}/bid', amount=25).status_code == 409
    assert post(client, f'lots/{lot_id}/bid', amount=30).status_code == 200
    login(client, 'CMTEST01')
    assert client.get('/api/activity-points/me').json()['frozen'] == 0
    assert client.get('/api/activity-points/me').json()['available'] == 100
    expire(lot_id)
    process_due()
    login(client, 'CMTEST02')
    card = client.get('/api/activity-points/me').json()
    assert card['available'] == 70 and card['frozen'] == 0
    lot = find(client, event['id'])['lots'][0]
    assert len(lot['awards']) == 1 and lot['awards'][0]['cost'] == 30
    assert post(client, f'lots/{lot_id}/bid', amount=35).status_code == 409
    login(client, 'GSMTEST01')
    assert post(client, f"awards/{lot['awards'][0]['id']}/fulfill", action='refund', reason='拍品损坏').status_code == 200
    assert post(client, f'{event["id"]}/complete').status_code == 200
    login(client, 'CMTEST02')
    assert client.get('/api/activity-points/me').json()['available'] == 100


def test_auction_extension_and_cross_activity_double_spend(client):
    auction = create(client, 'auction')
    draw = create(client)
    lot_id = auction['lots'][0]['id']
    with SessionLocal() as db:
        lot = db.get(ActivityLot, lot_id)
        lot.ends_at, lot.extend_seconds = service.now()+timedelta(seconds=30), 60
        db.commit()
    money('CMTEST01', 45)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/bid', amount=40).status_code == 200
    lot = find(client, auction['id'])['lots'][0]
    assert (service.datetime.fromisoformat(lot['ends_at'])-service.now()).total_seconds() > 50
    assert post(client, f"lots/{draw['lots'][0]['id']}/join", count=1).status_code == 409
    login(client, 'GSMTEST01')
    assert post(client, f'lots/{lot_id}/cancel', reason='取消拍卖').status_code == 200
    login(client, 'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 45


def test_auth_scope_helpers_and_cancellation(client):
    assert client.get('/api/activities').status_code == 401
    login(client, 'CMTEST01')
    assert client.post('/api/activities', json=payload()).status_code == 403
    event = create(client, helper_employee_nos=['SUPTEST01'])
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f"lots/{event['lots'][0]['id']}/join", count=1).status_code == 200
    login(client, 'SUPTEST01')
    assert find(client, event['id'])['manager'] is True
    assert post(client, f"lots/{event['lots'][0]['id']}/join", count=1).status_code == 403
    assert post(client, f"{event['id']}/cancel", reason='协助人不可以').status_code == 403
    expire(event['lots'][0]['id'])
    award = find(client, event['id'])['lots'][0]['awards'][0]
    assert post(client, f"awards/{award['id']}/fulfill", action='refund', reason='协助人不可以').status_code == 403
    assert post(client, f"awards/{award['id']}/fulfill", action='claim', reason='核对领取人').status_code == 200
    with SessionLocal() as db:
        own_circle = db.get(Employee, employee_id('CMTEST01')).attraction_id
        other = db.query(Attraction).filter(Attraction.employee_circle.is_(True), Attraction.active.is_(True), Attraction.id != own_circle).first()
        other_id = other.id
    scoped = create(client, circle_ids=[other_id])
    login(client, 'CMTEST01')
    assert scoped['id'] not in [e['id'] for e in client.get('/api/activities').json()['items']]
    assert post(client, f"lots/{scoped['lots'][0]['id']}/join", count=1).status_code == 404
    login(client, 'GSMTEST01')
    assert post(client, f"{scoped['id']}/cancel", reason='取消跨圈活动').status_code == 200
    client.post('/api/logout')
    assert client.post('/api/login', json={'employee_no':'HR01','password':'HR123'}).status_code == 200
    assert client.get('/api/activities').json()['eligible'] is False
    assert client.post('/api/activities', json=payload()).status_code == 403


def test_cancel_pool_refunds_only_affected_pool(client):
    event = create(client)
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    for lot in event['lots']:
        assert post(client, f"lots/{lot['id']}/join", count=1).status_code == 200
    login(client, 'GSMTEST01')
    assert post(client, f"lots/{event['lots'][0]['id']}/cancel", reason='该池奖品未到货').status_code == 200
    assert find(client, event['id'])['lots'][1]['state'] == 'open'
    login(client, 'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 50
    login(client, 'GSMTEST01')
    assert post(client, f"{event['id']}/cancel", reason='整场取消').status_code == 200
    login(client, 'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 100


def test_settlement_rollback_and_recovery(client, monkeypatch):
    event = create(client, 'auction')
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/bid', amount=20).status_code == 200
    with SessionLocal() as db:
        db.get(ActivityLot, lot_id).ends_at = service.now()-timedelta(seconds=1)
        db.commit()
    original = service.movement
    def broken(*args):
        original(*args)
        raise RuntimeError('模拟结算中断')
    monkeypatch.setattr(service, 'movement', broken)
    process_due()
    with SessionLocal() as db:
        assert db.query(ActivityAward).filter_by(lot_id=lot_id).count() == 0
        assert db.get(ActivityLot, lot_id).state == 'open'
    monkeypatch.setattr(service, 'movement', original)
    process_due()
    process_due()
    assert client.get('/api/activity-points/me').json()['available'] == 80
    with SessionLocal() as db:
        assert db.query(ActivityAward).filter_by(lot_id=lot_id).count() == 1


def test_concurrent_last_prize_and_duplicate_request(client):
    event = create(client, lots=[{'name':'仅一份','price':20,'per_person':1,'prizes':[{'name':'唯一奖品','quantity':1}]}])
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    money('CMTEST02', 100)
    # Independent HTTP sessions and DB connections race for the same last unit.
    clients = [TestClient(app), TestClient(app)]
    for c, account in zip(clients, ['CMTEST01','CMTEST02']):
        login(c, account)
    def attempt(c):
        return post(c, f'lots/{lot_id}/join', count=1).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, clients))
    assert sorted(statuses) == [200, 409]
    with SessionLocal() as db:
        assert db.query(ActivityAward).filter_by(lot_id=lot_id).count() == 1
    for c in clients:
        c.close()


def test_invalid_input_and_negative_balance_keeps_existing_contract(client):
    login(client, 'GSMTEST01')
    bad = payload()
    bad['lots'][0]['price'] = 0
    assert client.post('/api/activities', json=bad).status_code == 422
    bad = payload('auction')
    bad['lots'][0]['prizes'][0]['quantity'] = 2
    assert client.post('/api/activities', json=bad).status_code == 422
    event = create(client, 'auction')
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/bid', amount=80).status_code == 200
    money('CMTEST01', 10)  # Recognition revoked while an existing bid is held.
    assert client.get('/api/activity-points/me').json()['available'] == -70
    assert post(client, f'lots/{lot_id}/bid', amount=85).status_code == 409
    expire(lot_id)
    assert client.get('/api/activity-points/me').json()['available'] == -70
    assert find(client, event['id'])['lots'][0]['awards'][0]['cost'] == 80


def test_concurrent_duplicate_join_and_refund(client):
    event = create(client)
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    clients = [TestClient(app), TestClient(app)]
    for c in clients:
        login(c, 'CMTEST01')
    body = {'request_key': key(), 'count': 1}
    def join(c):
        r = c.post(f'/api/activities/lots/{lot_id}/join', json=body)
        assert r.status_code == 200, r.text
        return r.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(join, clients))
    assert results[0] == results[1]
    expire(lot_id)
    for c in clients:
        login(c, 'GSMTEST01')
    award_id = results[0]['numbers'][0]
    def refund(c):
        return post(c, f'awards/{award_id}/fulfill', action='refund', reason='并发退款').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(refund, clients)) == [200, 409]
    login(client, 'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 100
    for c in clients:
        c.close()


def test_claim_correction_expiry_and_reopening_completed_activity(client):
    event = create(client, 'auction')
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/bid', amount=20).status_code == 200
    expire(lot_id)
    login(client, 'GSMTEST01')
    award_id = find(client, event['id'])['lots'][0]['awards'][0]['id']
    assert post(client, f'awards/{award_id}/fulfill', action='expire', reason='未到期').status_code == 409
    from test_activity_export import legacy_claim
    assert legacy_claim(client, award_id, '旧人工核销').status_code == 200
    assert post(client, f'{event["id"]}/complete').status_code == 200
    assert post(client, f'awards/{award_id}/fulfill', action='correct', reason='误点核销，实物未发').status_code == 200
    assert find(client, event['id'])['state'] == 'published'
    assert post(client, f'awards/{award_id}/fulfill', action='refund', reason='实物缺货').status_code == 200
    assert post(client, f'{event["id"]}/complete').status_code == 200
    login(client, 'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 100


def test_pickup_expiry_does_not_silently_refund(client):
    event = create(client, 'auction')
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/bid', amount=20).status_code == 200
    expire(lot_id)
    with SessionLocal() as db:
        db.get(Activity, event['id']).claim_until = service.now()-timedelta(seconds=1)
        db.commit()
    login(client, 'GSMTEST01')
    award_id = find(client, event['id'])['lots'][0]['awards'][0]['id']
    assert post(client, f'awards/{award_id}/fulfill', action='expire', reason='已提醒，宽限期结束').status_code == 200
    login(client, 'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 80


def test_transfer_keeps_history_but_not_new_participation(client):
    with SessionLocal() as db:
        eid = employee_id('CMTEST01')
        original = db.get(Employee, eid).attraction_id
        other = db.query(Attraction).filter(Attraction.employee_circle.is_(True), Attraction.active.is_(True),
                                           Attraction.id != original).first().id
    event = create(client, circle_ids=[original])
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f"lots/{event['lots'][0]['id']}/join", count=1).status_code == 200
    try:
        with SessionLocal() as db:
            db.get(Employee, eid).attraction_id = other
            db.commit()
        history = find(client, event['id'])
        assert history['in_scope'] is False
        assert len(history['lots'][0]['awards']) == 1
        assert post(client, f"lots/{event['lots'][0]['id']}/join", count=1).status_code == 404
    finally:
        with SessionLocal() as db:
            db.get(Employee, eid).attraction_id = original
            db.commit()


@pytest.mark.parametrize('kind', ['draw', 'auction'])
def test_cancel_rechecks_deadline_inside_transaction(client, monkeypatch, kind):
    """Simulate preflight finishing before cutoff and lock acquisition after cutoff."""
    from app.routers import activities as routes
    activity = create(client, kind)
    lot_id = activity['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/'+('join' if kind == 'draw' else 'bid'),
                **({'count': 1} if kind == 'draw' else {'amount': 20})).status_code == 200
    login(client, 'GSMTEST01')
    monkeypatch.setattr(routes, 'refresh_due', lambda db: None)
    with SessionLocal() as db:
        db.get(ActivityLot, lot_id).ends_at = service.now()-timedelta(seconds=1)
        db.commit()
    assert post(client, f'lots/{lot_id}/cancel', reason='截止后尝试取消').status_code == 409
    assert post(client, f'{activity["id"]}/cancel', reason='截止后整场取消').status_code == 409
    process_due()
    with SessionLocal() as db:
        assert db.get(ActivityLot, lot_id).state == 'settled'
        award = db.query(ActivityAward).filter_by(lot_id=lot_id).one()
        assert award.state == 'ready' and award.cost == 20
        assert service.balances(db, employee_id('CMTEST01'))['balance'] == 80


@pytest.mark.parametrize('read_card', [False, True])
def test_balance_snapshot_during_auction_settlement(client, read_card):
    """A settlement between SQL reads must not create phantom spendable points."""
    from app.services.activity_points import personal_card
    from app.database.connection import engine
    activity = create(client, 'auction')
    lot_id = activity['lots'][0]['id']
    eid = employee_id('CMTEST01')
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/bid', amount=20).status_code == 200
    with SessionLocal() as db:
        before_used = personal_card(db, eid)['used']
    triggered = False
    def after_query(connection, cursor, statement, parameters, context, executemany):
        nonlocal triggered
        # Run a real concurrent commit immediately after the first ledger read.
        if triggered or not statement.lstrip().upper().startswith('SELECT') or 'sum(activity_points_entries.delta)' not in statement:
            return
        if 'activity_points_entries.kind =' in statement.split('WHERE')[-1]:
            return
        triggered = True
        with SessionLocal() as writer:
            writer.execute(text('BEGIN IMMEDIATE'))
            lot = writer.get(ActivityLot, lot_id)
            lot.ends_at = service.now()-timedelta(seconds=1)
            service.settle_lot(writer, lot, writer.get(Activity, lot.activity_id))
            writer.commit()
    sqlalchemy_event.listen(engine, 'after_cursor_execute', after_query)
    try:
        with SessionLocal() as reader:
            snapshot = personal_card(reader, eid) if read_card else service.balances(reader, eid)
        assert triggered
        assert snapshot['available'] == 80
        if read_card:
            # Both a pre-settlement and a post-settlement snapshot are valid.
            assert (snapshot['used'], snapshot['frozen']) in {(before_used, 20), (before_used+20, 0)}
    finally:
        sqlalchemy_event.remove(engine, 'after_cursor_execute', after_query)


def test_simultaneous_bids_cannot_reuse_points_across_auctions(client):
    first = create(client, 'auction')
    second = create(client, 'auction')
    money('CMTEST01', 70)
    clients = [TestClient(app), TestClient(app)]
    for c in clients:
        login(c, 'CMTEST01')
    try:
        def bid(pair):
            c, activity = pair
            return post(c, f'lots/{activity["lots"][0]["id"]}/bid', amount=60).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(bid, zip(clients, [first, second]))) == [200, 409]
        with SessionLocal() as db:
            assert service.balances(db, employee_id('CMTEST01'))['available'] == 10
    finally:
        login(client, 'GSMTEST01')
        for activity in [first, second]:
            post(client, f'lots/{activity["lots"][0]["id"]}/cancel', reason='清理测试冻结')
        for c in clients:
            c.close()


def test_claim_and_refund_race_has_one_outcome(client):
    activity = create(client)
    lot_id = activity['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/join', count=1).status_code == 200
    expire(lot_id)
    login(client, 'GSMTEST01')
    award_id = find(client, activity['id'])['lots'][0]['awards'][0]['id']
    clients = [TestClient(app), TestClient(app)]
    for c in clients:
        login(c, 'GSMTEST01')
    try:
        def handle(pair):
            c, action = pair
            return post(c, f'awards/{award_id}/fulfill', action=action, reason='并发处理').status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(handle, zip(clients, ['claim', 'refund']))) == [200, 409]
        with SessionLocal() as db:
            award = db.get(ActivityAward, award_id)
            assert award.state in {'issued', 'refunded'}
            expected = 80 if award.state == 'issued' else 100
            assert service.balances(db, employee_id('CMTEST01'))['balance'] == expected
    finally:
        for c in clients:
            c.close()


def test_shortage_refund_failure_rolls_back_and_same_request_can_retry(client, monkeypatch):
    from app.activities_models import ActivityOperation
    activity = create(client)
    lot_id = activity['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/join', count=1).status_code == 200
    expire(lot_id)
    login(client, 'GSMTEST01')
    award_id = find(client, activity['id'])['lots'][0]['awards'][0]['id']
    body = {'request_key': key(), 'action': 'refund', 'reason': '现场缺货'}
    path = f'/api/activities/awards/{award_id}/fulfill'
    original = service.movement
    def fail_after_write(db, *args):
        original(db, *args)
        db.flush()
        raise RuntimeError('模拟退款提交前中断')
    with monkeypatch.context() as context:
        context.setattr(service, 'movement', fail_after_write)
        with pytest.raises(RuntimeError, match='模拟退款提交前中断'):
            client.post(path, json=body)
    with SessionLocal() as db:
        assert db.get(ActivityAward, award_id).state == 'ready'
        assert service.balances(db, employee_id('CMTEST01'))['balance'] == 80
        assert db.query(ActivityOperation).filter_by(request_key=body['request_key']).count() == 0
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json=body).status_code == 200
    with SessionLocal() as db:
        assert db.get(ActivityAward, award_id).state == 'refunded'
        assert service.balances(db, employee_id('CMTEST01'))['balance'] == 100


def test_concurrent_multi_pool_purchases_share_one_balance(client):
    activity = create(client)
    money('CMTEST01', 60)
    clients = [TestClient(app), TestClient(app)]
    for c in clients:
        login(c, 'CMTEST01')
    try:
        def purchase(pair):
            c, lot = pair
            return post(c, f'lots/{lot["id"]}/join', count=1).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(purchase, zip(clients, activity['lots']))) == [200, 409]
        with SessionLocal() as db:
            awards = db.query(ActivityAward).filter(ActivityAward.lot_id.in_([lot['id'] for lot in activity['lots']])).all()
            assert len(awards) == 1
            assert service.balances(db, employee_id('CMTEST01'))['available'] == 60-awards[0].cost
    finally:
        for c in clients:
            c.close()
