"""Employee-only signature, immutable evidence, exceptions and transaction coverage."""
from io import BytesIO
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import base64
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from test_supervisor_performance import app, SessionLocal, employee_id, login
from test_activities import create, post, find, money, expire, key
from app.activities_models import ActivityAward, ActivityReceipt, Activity
from app.services import activity_receipts as receipts


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def signature(blank=False):
    image = Image.new('RGB', (900,420), 'white')
    if not blank:
        ImageDraw.Draw(image).line([(80,210),(150,90),(220,250),(300,130),(400,260),(600,150)], fill='black', width=8)
    output = BytesIO()
    image.save(output,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()


def allocated(client):
    event = create(client, helper_employee_nos=['SUPTEST01'], lots=[{'name':'签收奖池','price':20,'per_person':1,
        'prizes':[{'name':'礼盒','quantity':1}]}])
    money('CMTEST01', 100)
    login(client,'CMTEST01')
    result = post(client,f'lots/{event["lots"][0]["id"]}/join',count=1)
    assert result.status_code == 200
    award_id = result.json()['numbers'][0]
    expire(event['lots'][0]['id'])
    login(client,'SUPTEST01')
    assert post(client,f'awards/{award_id}/fulfill',action='issue',reason='实物已交付').status_code == 200
    return event, award_id


def signing_payload(client, award_id):
    login(client,'CMTEST01')
    response = client.get(f'/api/activities/awards/{award_id}/signing')
    assert response.status_code == 200,response.text
    context = response.json()
    return {'request_key':key(),'revision':context['revision'],'snapshot_hash':context['snapshot_hash'],
            'accepted':True,'signature':signature()}


def test_two_party_signature_and_archive_are_atomic_and_employee_only(client):
    event, award_id = allocated(client)
    assert client.get(f'/api/activities/awards/{award_id}/signing').status_code == 403
    login(client,'GSMTEST01')
    assert post(client,f'{event["id"]}/complete').status_code == 409
    assert post(client,f'awards/{award_id}/fulfill',action='refund',reason='不能直接退').status_code == 409
    body = signing_payload(client,award_id)
    card = client.get('/api/activity-points/me').json()
    assert card['pending_redemption'] == {'count': 1, 'cost': 20, 'title': event['title'], 'state': 'issued', 'event_id': event['id']}
    path = f'/api/activities/awards/{award_id}/sign'
    login(client,'CMTEST02')
    assert client.post(path,json=body).status_code == 403
    login(client,'CMTEST01')
    assert client.post(path,json={**body,'signature':signature(True)}).status_code == 422
    assert client.post(path,json={**body,'accepted':False}).status_code == 422
    signed = client.post(path,json=body)
    assert signed.status_code == 200,signed.text
    assert client.post(path,json=body).json() == signed.json()
    assert client.post(path,json={**body,'request_key':key()}).status_code == 409
    archive = '/api/activities/receipts/'+signed.json()['receipt_id']
    saved = client.get(archive).json()
    assert saved['snapshot']['employee_no'] == 'CMTEST01' and saved['snapshot']['prize'] == '礼盒'
    assert client.get(saved['signature_url']).headers['content-type'] == 'image/png'
    card = client.get('/api/activity-points/me').json()
    assert card['available'] == 80 and card['earned'] == 100
    assert card['pending_redemption'] is None
    login(client,'CMTEST02')
    assert client.get(archive).status_code == 403
    assert client.get(saved['signature_url']).status_code == 403
    login(client,'SUPTEST01')
    assert client.get(archive).status_code == 200
    login(client,'GSMTEST01')
    assert post(client,f'{event["id"]}/complete').status_code == 200
    assert post(client,f'awards/{award_id}/fulfill',action='correct',reason='不能覆盖签名').status_code == 409
    with SessionLocal() as db:
        assert db.query(ActivityReceipt).filter_by(award_id=award_id).count() == 1


def test_recall_and_reissue_invalidates_an_old_signature_form(client):
    event, award_id = allocated(client)
    old = signing_payload(client,award_id)
    login(client,'GSMTEST01')
    assert post(client,f'awards/{award_id}/fulfill',action='recall',reason='误发放').status_code == 422
    assert post(client,f'awards/{award_id}/fulfill',action='recall',reason='核实未发出',verified_return=True).status_code == 200
    from test_activity_export import sheet, rows
    recalled = rows(sheet(client,event['id']))[f'#{award_id}']
    assert recalled[6] == '待领取' and recalled[7:11] == [None,None,None,None]
    record = client.get(f'/api/activities/{event["id"]}/records?status=ready').json()['items'][0]
    assert 'issued_at' not in record and 'issued_name' not in record
    assert post(client,f'awards/{award_id}/fulfill',action='issue',reason='重新发放').status_code == 200
    login(client,'CMTEST01')
    assert client.post(f'/api/activities/awards/{award_id}/sign',json=old).status_code == 409
    fresh = signing_payload(client,award_id)
    assert client.post(f'/api/activities/awards/{award_id}/sign',json=fresh).status_code == 200


def test_dispute_resolution_and_post_signature_return_keep_original_archive(client):
    event, award_id = allocated(client)
    old = signing_payload(client,award_id)
    assert post(client,f'awards/{award_id}/dispute',reason='收到的奖品不对').status_code == 200
    assert client.post(f'/api/activities/awards/{award_id}/sign',json=old).status_code == 409
    login(client,'GSMTEST01')
    assert post(client,f'{event["id"]}/complete').status_code == 409
    assert post(client,f'awards/{award_id}/fulfill',action='request_sign',reason='已更换正确奖品').status_code == 200
    body = signing_payload(client,award_id)
    signed = client.post(f'/api/activities/awards/{award_id}/sign',json=body).json()
    archive = '/api/activities/receipts/'+signed['receipt_id']
    before = client.get(archive).json()
    login(client,'GSMTEST01')
    assert post(client,f'awards/{award_id}/fulfill',action='return_refund',reason='已退回实物').status_code == 422
    assert post(client,f'awards/{award_id}/fulfill',action='return_refund',reason='已退回实物',verified_return=True).status_code == 200
    after = client.get(archive).json()
    assert after['evidence_hash'] == before['evidence_hash'] and after['signed_at'] == before['signed_at']
    assert after['current_state'] == 'refunded'
    assert post(client,f'awards/{award_id}/fulfill',action='return_refund',reason='不能再退',verified_return=True).status_code != 200
    login(client,'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 100


def test_issued_records_never_expire_as_uncollected_and_legacy_claim_cannot_bypass(client):
    event, award_id = allocated(client)
    login(client,'GSMTEST01')
    with SessionLocal() as db:
        db.get(Activity,event['id']).claim_until = receipts.service.now()-timedelta(days=1)
        db.commit()
    assert post(client,f'awards/{award_id}/fulfill',action='expire',reason='不能弃领关闭').status_code == 409
    assert post(client,f'awards/{award_id}/fulfill',action='claim',reason='旧接口不能代签').status_code == 409
    login(client,'CMTEST01')
    assert client.get('/api/activities?view=sign').json()['pending_signatures'] >= 1
    body = signing_payload(client,award_id)
    assert client.post(f'/api/activities/awards/{award_id}/sign',json=body).status_code == 200


def test_signature_failure_rolls_back_archive_and_state(client,monkeypatch):
    event,award_id = allocated(client)
    body = signing_payload(client,award_id)
    original = receipts.service.audit
    def broken(db,user,event,action,detail):
        original(db,user,event,action,detail)
        if action == '员工本人签字核销':
            db.flush()
            raise RuntimeError('模拟归档中断')
    with monkeypatch.context() as context:
        context.setattr(receipts.service,'audit',broken)
        with pytest.raises(RuntimeError,match='模拟归档中断'):
            client.post(f'/api/activities/awards/{award_id}/sign',json=body)
    with SessionLocal() as db:
        assert db.get(ActivityAward,award_id).state == 'issued'
        assert db.query(ActivityReceipt).filter_by(award_id=award_id).count() == 0
    assert client.post(f'/api/activities/awards/{award_id}/sign',json=body).status_code == 200


def test_concurrent_duplicate_signature_archives_only_once(client):
    event,award_id = allocated(client)
    body = signing_payload(client,award_id)
    clients = [TestClient(app),TestClient(app)]
    for c in clients: login(c,'CMTEST01')
    try:
        def submit(c):return c.post(f'/api/activities/awards/{award_id}/sign',json=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit,clients))
        assert all(r.status_code==200 for r in responses)
        assert responses[0].json() == responses[1].json()
    finally:
        for c in clients:c.close()


def test_different_key_concurrent_sign_and_refund_never_duplicate_money_or_archive(client):
    from app.activity_points_models import ActivityPointsEntry
    import json
    event, award_id = allocated(client)
    body = signing_payload(client, award_id)
    employees = [TestClient(app), TestClient(app)]
    managers = [TestClient(app), TestClient(app)]
    for c in employees:
        login(c, 'CMTEST01')
    for c in managers:
        login(c, 'GSMTEST01')
    try:
        def submit(c):
            return c.post(f'/api/activities/awards/{award_id}/sign', json={**body, 'request_key': key()})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, employees))
        assert sorted(r.status_code for r in responses) == [200, 409]
        receipt_id = next(r.json()['receipt_id'] for r in responses if r.status_code == 200)
        archive = f'/api/activities/receipts/{receipt_id}'
        before = employees[0].get(archive).json()
        assert employees[0].get('/api/activity-points/me').json()['available'] == 80
        def refund(c):
            return post(c, f'awards/{award_id}/fulfill', action='return_refund',
                        reason='并发退款回归：实物已收回', verified_return=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(refund, managers))
        assert sorted(r.status_code for r in responses) == [200, 422]
        after = employees[0].get(archive).json()
        assert {k: v for k, v in before.items() if k != 'current_state'} == {
            k: v for k, v in after.items() if k != 'current_state'}
        assert after['current_state'] == 'refunded'
        assert employees[0].get('/api/activity-points/me').json()['available'] == 100
        with SessionLocal() as db:
            assert db.query(ActivityReceipt).filter_by(award_id=award_id).count() == 1
            entries = [entry for entry in db.query(ActivityPointsEntry).filter_by(employee_id=employee_id('CMTEST01'))
                       if json.loads(entry.detail_json).get('award_id') == award_id]
            assert sorted((entry.kind, entry.delta) for entry in entries) == [
                ('activity_refund', 20), ('activity_spend', -20)]
    finally:
        for c in employees + managers:
            c.close()


def test_export_separates_issue_signature_and_preserves_archive_after_refund(client):
    from test_activity_export import sheet, rows
    event,award_id = allocated(client)
    login(client,'GSMTEST01')
    waiting = rows(sheet(client,event['id']))[f'#{award_id}']
    assert waiting[6] == '已发放，待签收' and waiting[7] and waiting[8]
    assert waiting[9:11] == [None,None] and waiting[11] == 0
    body = signing_payload(client,award_id)
    signed = client.post(f'/api/activities/awards/{award_id}/sign',json=body).json()
    login(client,'GSMTEST01')
    record = rows(sheet(client,event['id']))[f'#{award_id}']
    assert record[6] == '已签收' and record[9] and record[10] == signed['receipt_id']
    assert post(client,f'awards/{award_id}/fulfill',action='return_refund',reason='已核实奖品退回',verified_return=True).status_code == 200
    refunded = rows(sheet(client,event['id']))[f'#{award_id}']
    assert refunded[6] == '已退款' and refunded[9:11] == record[9:11] and refunded[11] == 20


def test_claim_alias_issues_but_never_completes_signature(client):
    event,award_id = allocated(client)
    login(client,'GSMTEST01')
    assert post(client,f'awards/{award_id}/fulfill',action='recall',reason='误操作已核实',verified_return=True).status_code == 200
    result = post(client,f'awards/{award_id}/fulfill',action='claim',reason='旧客户端')
    assert result.status_code == 200 and result.json()['state'] == 'issued'
    with SessionLocal() as db:
        assert db.query(ActivityReceipt).filter_by(award_id=award_id).count() == 0


def test_sign_and_recall_race_cannot_erase_a_signature(client):
    event,award_id = allocated(client)
    body = signing_payload(client,award_id)
    employee,manager = TestClient(app),TestClient(app)
    login(employee,'CMTEST01');login(manager,'GSMTEST01')
    try:
        def sign():return employee.post(f'/api/activities/awards/{award_id}/sign',json=body)
        def recall():return post(manager,f'awards/{award_id}/fulfill',action='recall',reason='核实未发出',verified_return=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            signing,recalling = pool.submit(sign),pool.submit(recall)
            results = [signing.result(),recalling.result()]
        assert sum(r.status_code==200 for r in results) == 1
        with SessionLocal() as db:
            state = db.get(ActivityAward,award_id).state
            count = db.query(ActivityReceipt).filter_by(award_id=award_id).count()
            assert (state,count) in {('ready',0),('signed',1)}
    finally:
        employee.close();manager.close()


def test_archive_and_export_keep_the_signed_prize_and_employee_snapshot(client):
    from app.v2_models import Employee
    from app.activities_models import ActivityPrize
    from test_activity_export import sheet, rows
    event,award_id = allocated(client)
    body = signing_payload(client,award_id)
    result = client.post(f'/api/activities/awards/{award_id}/sign',json=body).json()
    original = client.get('/api/activities/receipts/'+result['receipt_id']).json()['snapshot']
    eid = employee_id('CMTEST01')
    with SessionLocal() as db:
        employee = db.get(Employee,eid)
        old_name = employee.name
        prize = db.get(ActivityPrize,db.get(ActivityAward,award_id).prize_id)
        old_prize = prize.name
        employee.name,prize.name = '之后改名','之后更改的名称'
        db.commit()
    try:
        login(client,'GSMTEST01')
        data = rows(sheet(client,event['id']))[f'#{award_id}']
        assert data[1] == original['employee_name'] and data[4] == original['prize']
        assert client.get('/api/activities/receipts/'+result['receipt_id']).json()['snapshot'] == original
    finally:
        with SessionLocal() as db:
            db.get(Employee,eid).name = old_name
            db.get(ActivityPrize,db.get(ActivityAward,award_id).prize_id).name = old_prize
            db.commit()


def signed_record(client):
    event,award_id = allocated(client)
    body = signing_payload(client,award_id)
    response = client.post(f'/api/activities/awards/{award_id}/sign',json=body)
    assert response.status_code == 200,response.text
    return event,award_id,response.json()['receipt_id']


def test_corrupt_signature_is_not_exported_as_completed_or_allowed_to_close(client):
    from test_activity_export import sheet, rows
    event,award_id,receipt_id = signed_record(client)
    with SessionLocal() as db:
        db.get(ActivityReceipt,receipt_id).signature_png = b'damaged signature'
        db.commit()
    login(client,'GSMTEST01')
    assert client.get('/api/activities/receipts/'+receipt_id).status_code == 409
    ws = sheet(client,event['id'])
    assert rows(ws)[f'#{award_id}'][6] == '签收待核对'
    assert '已签收0' in ws['A2'].value and '待核对1' in ws['A2'].value
    assert post(client,f'{event["id"]}/complete').status_code == 409


def test_missing_signature_archive_blocks_completion(client):
    event,award_id,receipt_id = signed_record(client)
    with SessionLocal() as db:
        db.delete(db.get(ActivityReceipt,receipt_id))
        db.commit()
    login(client,'GSMTEST01')
    assert post(client,f'{event["id"]}/complete').status_code == 409


@pytest.mark.parametrize('field',['signed_at','account_id','dispatch_revision'])
def test_receipt_time_and_account_metadata_are_checked(client,field):
    from test_activity_export import sheet, rows
    event,award_id,receipt_id = signed_record(client)
    with SessionLocal() as db:
        receipt = db.get(ActivityReceipt,receipt_id)
        setattr(receipt,field,getattr(receipt,field)+(timedelta(days=1) if field=='signed_at' else 1))
        db.commit()
    login(client,'GSMTEST01')
    assert client.get('/api/activities/receipts/'+receipt_id).status_code == 409
    assert rows(sheet(client,event['id']))[f'#{award_id}'][6] == '签收待核对'


def test_signed_refund_rollback_preserves_signature_and_can_retry(client,monkeypatch):
    event,award_id,receipt_id = signed_record(client)
    before = client.get('/api/activities/receipts/'+receipt_id).json()
    login(client,'GSMTEST01')
    body = {'request_key':key(),'action':'return_refund','reason':'实物已退回','verified_return':True}
    original = receipts.service.movement
    def interrupted(db,*args):
        original(db,*args)
        db.flush()
        raise RuntimeError('退款中断')
    with monkeypatch.context() as context:
        context.setattr(receipts.service,'movement',interrupted)
        with pytest.raises(RuntimeError,match='退款中断'):
            client.post(f'/api/activities/awards/{award_id}/fulfill',json=body)
    assert client.get('/api/activities/receipts/'+receipt_id).json() == before
    assert client.post(f'/api/activities/awards/{award_id}/fulfill',json=body).status_code == 200
    assert client.post(f'/api/activities/awards/{award_id}/fulfill',json=body).status_code == 200
    after = client.get('/api/activities/receipts/'+receipt_id).json()
    assert after['current_state'] == 'refunded' and after['evidence_hash'] == before['evidence_hash']
    login(client,'CMTEST01')
    assert client.get('/api/activity-points/me').json()['available'] == 100


def test_old_signature_archives_remain_readable_without_rewriting_them(client):
    import json
    from hashlib import sha256
    from test_activity_export import sheet, rows
    event,award_id,receipt_id = signed_record(client)
    with SessionLocal() as db:
        receipt = db.get(ActivityReceipt,receipt_id)
        snapshot = json.loads(receipt.snapshot_json)
        snapshot.pop('signing_proof')
        receipt.snapshot_json = json.dumps(snapshot,ensure_ascii=False,sort_keys=True)
        receipt.evidence_hash = sha256(receipt.signature_png+receipt.snapshot_json.encode()+receipt.confirmation.encode()).hexdigest()
        old_hash = receipt.evidence_hash
        db.commit()
    assert client.get('/api/activities/receipts/'+receipt_id).status_code == 200
    login(client,'GSMTEST01')
    assert rows(sheet(client,event['id']))[f'#{award_id}'][6] == '已签收'
    assert post(client,f'{event["id"]}/complete').status_code == 200
    with SessionLocal() as db:
        assert db.get(ActivityReceipt,receipt_id).evidence_hash == old_hash


def test_damaged_archive_does_not_hide_a_successful_verified_refund(client):
    from test_activity_export import sheet, rows
    event,award_id,receipt_id = signed_record(client)
    with SessionLocal() as db:
        db.get(ActivityReceipt,receipt_id).signature_png = b'damaged'
        db.commit()
    login(client,'GSMTEST01')
    assert post(client,f'awards/{award_id}/fulfill',action='return_refund',reason='实物已收回',verified_return=True).status_code == 200
    ws = sheet(client,event['id'])
    row = rows(ws)[f'#{award_id}']
    assert row[6] == '已退款/档案待核对' and row[11] == 20 and row[10] == receipt_id
    assert '已退款1' in ws['A2'].value and '待核对1' in ws['A2'].value
    with SessionLocal() as db:
        assert db.get(ActivityReceipt,receipt_id).signature_png == b'damaged'


@pytest.mark.parametrize('bad_snapshot',['[]','{broken','wrong_employee'])
def test_signing_rejects_a_malformed_or_wrong_employee_dispatch(client,bad_snapshot):
    import json
    from app.activities_models import ActivityDispatch
    event,award_id = allocated(client)
    with SessionLocal() as db:
        dispatch = db.get(ActivityDispatch,award_id)
        if bad_snapshot == 'wrong_employee':
            snapshot = json.loads(dispatch.snapshot_json)
            snapshot['employee_id'] = employee_id('CMTEST02')
            dispatch.snapshot_json = json.dumps(snapshot)
        else:
            dispatch.snapshot_json = bad_snapshot
        db.commit()
    login(client,'CMTEST01')
    assert client.get(f'/api/activities/awards/{award_id}/signing').status_code == 409
    # Damaged dispatch metadata must not make the history and record list crash.
    if bad_snapshot != 'wrong_employee':
        detail = client.get(f'/api/activities/{event["id"]}')
        assert detail.status_code == 200
        award = next(a for lot in detail.json()['lots'] for a in lot['awards'] if a['id'] == award_id)
        assert award['evidence_problem']
        assert 'issued_at' not in award
        login(client,'GSMTEST01')
        records = client.get(f'/api/activities/{event["id"]}/records',params={'status':'issued'})
        assert records.status_code == 200
        assert records.json()['items'][0]['evidence_problem']
        from test_activity_export import sheet, rows
        assert rows(sheet(client,event['id']))[f'#{award_id}'][6] == '签收待核对'


@pytest.mark.parametrize('bad_snapshot',['[]','{broken'])
def test_recall_damaged_dispatch_is_rejected_without_changing_records(client,bad_snapshot):
    from app.activities_models import ActivityDispatch
    from app.activity_points_models import ActivityPointsEntry
    event,award_id = allocated(client)
    with SessionLocal() as db:
        db.get(ActivityDispatch,award_id).snapshot_json = bad_snapshot
        db.commit()
        before_entries = db.query(ActivityPointsEntry).count()
        before_revision = db.get(ActivityDispatch,award_id).revision
    login(client,'GSMTEST01')
    response = post(client,f'awards/{award_id}/fulfill',action='recall',reason='核实未交付',verified_return=True)
    assert response.status_code == 409,response.text
    assert '核对' in response.json()['detail']
    with SessionLocal() as db:
        assert db.get(ActivityAward,award_id).state == 'issued'
        assert db.get(ActivityDispatch,award_id).snapshot_json == bad_snapshot
        assert db.get(ActivityDispatch,award_id).revision == before_revision
        assert db.query(ActivityPointsEntry).count() == before_entries
        assert db.query(ActivityReceipt).filter_by(award_id=award_id).count() == 0
