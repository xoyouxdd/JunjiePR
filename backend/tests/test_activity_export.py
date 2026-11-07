"""Receipt exports reconcile current award state with real claim logs and point entries."""
from datetime import datetime
from io import BytesIO
import json
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from sqlalchemy import event as sqlalchemy_event, text
from test_supervisor_performance import app, SessionLocal, employee_id, login
from test_activities import create, find, post, expire, money
from fastapi.testclient import TestClient
from app.activities_models import ActivityAward, Activity
from app.activity_points_models import ActivityPointsEntry
from app.services.activity_export import HEADERS
from app.v2_models import AuditLog, Employee


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def sheet(client, event_id):
    response = client.get(f'/api/activities/{event_id}/export')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert '.xlsx' in response.headers['content-disposition'] and response.headers['cache-control'] == 'private, no-store'
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ['领取核销']
    ws = workbook.active
    assert tuple(c.value for c in ws[4]) == HEADERS
    assert ws.freeze_panes == 'A5'
    assert ws.auto_filter.ref == f'A4:L{max(ws.max_row,4)}'
    assert ws.page_setup.orientation == 'landscape' and ws.page_setup.fitToWidth == 1
    return ws


def rows(ws):
    return {row[0].value: [cell.value for cell in row] for row in ws.iter_rows(min_row=5)}


def participate(client, event, count=1):
    money('CMTEST01', 500)
    login(client, 'CMTEST01')
    response = post(client, f'lots/{event["lots"][0]["id"]}/join', count=count)
    assert response.status_code == 200, response.text
    expire(event['lots'][0]['id'])
    login(client, 'GSMTEST01')
    return response.json()['numbers']



def legacy_claim(client, award_id, reason):
    """Seed a pre-signature historical record; the live API may no longer do this."""
    me = client.get('/api/me').json()
    with SessionLocal() as db:
        award = db.get(ActivityAward, award_id)
        assert award.state == 'ready'
        award.state, award.reason = 'claimed', reason
        from app.activities_models import ActivityLot
        event_id = db.get(ActivityLot, award.lot_id).activity_id
        db.add(AuditLog(operator_id=me['id'], operator_name=me['name'], action='活动奖品核销',
            entity_type='activity', entity_id=str(event_id), after_json=json.dumps({'award_id':award_id})))
        db.commit()
    return SimpleNamespace(status_code=200)

def test_claim_and_shortage_refund_each_have_their_own_row(client):
    event = create(client, helper_employee_nos=['SUPTEST01'])
    claimed, refunded = participate(client, event, 2)
    login(client, 'SUPTEST01')
    assert legacy_claim(client, claimed, '本人领取').status_code == 200
    with SessionLocal() as db:
        log = db.query(AuditLog).filter_by(entity_id=str(event['id']), action='活动奖品核销').order_by(AuditLog.id.desc()).first()
        claim_time, operator = log.created_at, log.operator_name
    login(client, 'GSMTEST01')
    assert post(client, f'awards/{refunded}/fulfill', action='refund', reason='少了一份礼盒').status_code == 200
    ws = sheet(client, event['id'])
    exported = rows(ws)
    assert len(exported) == 2
    claim, refund = exported[f'#{claimed}'], exported[f'#{refunded}']
    assert claim[2] == 'CMTEST01' and claim[3] == event['lots'][0]['name']
    assert claim[5] == 20 and claim[6] == '旧核销（无签名）' and claim[11] == 0
    assert isinstance(claim[7], datetime) and abs((claim[7]-claim_time).total_seconds()) < .0011
    assert claim[8] == operator
    assert refund[5:7] == [20,'已退款'] and refund[7:9] == [None,None] and refund[11] == 20
    refund_index = next(i for i in range(5,ws.max_row+1) if ws.cell(i,1).value == f'#{refunded}')
    assert ws.cell(refund_index,12).comment.text == '少了一份礼盒'
    assert '消耗40点' in ws['A2'].value and '退款20点' in ws['A2'].value


def test_correction_removes_old_receipt_and_refund_is_not_a_signature(client):
    event = create(client)
    award_id = participate(client, event)[0]
    assert legacy_claim(client, award_id, '误点').status_code == 200
    assert post(client, f'awards/{award_id}/fulfill', action='correct', reason='没有发出实物').status_code == 200
    record = rows(sheet(client, event['id']))[f'#{award_id}']
    assert record[6] == '待领取' and record[7:9] == [None,None]
    assert post(client, f'awards/{award_id}/fulfill', action='refund', reason='缺货').status_code == 200
    record = rows(sheet(client, event['id']))[f'#{award_id}']
    assert record[6] == '已退款' and record[7:9] == [None,None] and record[11] == 20


def test_cancelled_pool_exports_unallocated_refunds(client):
    event = create(client)
    money('CMTEST01', 500)
    login(client, 'CMTEST01')
    numbers = []
    for lot in event['lots']:
        response = post(client, f'lots/{lot["id"]}/join', count=1)
        assert response.status_code == 200
        numbers += response.json()['numbers']
    login(client, 'GSMTEST01')
    assert post(client, f'lots/{event["lots"][0]["id"]}/cancel', reason='未到货').status_code == 200
    exported = rows(sheet(client, event['id']))
    first, second = (exported[f'#{number}'] for number in numbers)
    assert first[4:7] == ['未开奖',20,'已退款'] and first[11] == 20
    assert second[4:7] == ['未开奖',50,'待开奖'] and second[11] == 0


def test_auction_exports_actual成交_price_not_starting_price(client):
    event = create(client, 'auction')
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{event["lots"][0]["id"]}/bid', amount=60).status_code == 200
    expire(event['lots'][0]['id'])
    login(client, 'GSMTEST01')
    award_id = find(client, event['id'])['lots'][0]['awards'][0]['id']
    assert list(rows(sheet(client, event['id'])).values())[0][5] == 60
    assert post(client, f'awards/{award_id}/fulfill', action='refund', reason='拍品损坏').status_code == 200
    record = rows(sheet(client, event['id']))[f'#{award_id}']
    assert record[5] == 60 and record[11] == 60


def test_export_is_owner_only_and_handles_empty_events(client):
    event = create(client, helper_employee_nos=['SUPTEST01'])
    ws = sheet(client, event['id'])
    assert ws.max_row == 4 and '共0份' in ws['A2'].value
    for account in ['CMTEST01','SUPTEST01','AMTEST01']:
        login(client, account)
        assert client.get(f'/api/activities/{event["id"]}/export').status_code == 403
    client.post('/api/logout')
    assert client.get(f'/api/activities/{event["id"]}/export').status_code == 401


def test_text_safety_and_leading_zero_employee_numbers(client):
    event = create(client, title='=HYPERLINK("bad")')
    award_id = participate(client, event)[0]
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id('CMTEST01'))
        previous = employee.name, employee.employee_no
        employee.name, employee.employee_no = '=1+1', '01234567'
        db.commit()
    try:
        ws = sheet(client, event['id'])
        record = rows(ws)[f'#{award_id}']
        assert ws['A1'].data_type != 'f' and record[1] == "'=1+1"
        assert record[2] == '01234567' and ws['C5'].number_format == '@'
    finally:
        with SessionLocal() as db:
            employee = db.get(Employee, employee_id('01234567'))
            employee.name, employee.employee_no = previous
            db.commit()


def test_missing_claim_audit_never_uses_purchase_time(client):
    event = create(client)
    award_id = participate(client, event)[0]
    with SessionLocal() as db:
        db.get(ActivityAward, award_id).state = 'claimed'
        db.commit()
    record = rows(sheet(client, event['id']))[f'#{award_id}']
    assert record[7:9] == ['未记录','未记录']


def test_refund_uses_actual_points_ledger_and_flags_mismatch(client):
    event = create(client)
    award_id = participate(client, event)[0]
    assert post(client, f'awards/{award_id}/fulfill', action='refund', reason='缺货').status_code == 200
    with SessionLocal() as db:
        for entry in db.query(ActivityPointsEntry).filter_by(kind='activity_refund'):
            if json.loads(entry.detail_json).get('award_id') == award_id:
                entry.delta = 10  # Simulate inconsistent legacy data; never invent a full refund.
        db.commit()
    record = rows(sheet(client, event['id']))[f'#{award_id}']
    assert record[6] == '退款待核对' and record[11] == 10


def test_export_is_one_snapshot_while_another_connection_refunds(client):
    from app.database.connection import engine
    from app.services import activities as service
    activity = create(client)
    award_id = participate(client, activity)[0]
    triggered = False
    def refund_during_read(connection, cursor, statement, parameters, context, executemany):
        nonlocal triggered
        if triggered or 'FROM activity_awards JOIN activity_lots' not in statement:
            return
        triggered = True
        with SessionLocal() as writer:
            writer.execute(text('BEGIN IMMEDIATE'))
            event = writer.get(Activity, activity['id'])
            operator = SimpleNamespace(id=event.owner_id, name='并发处理GSM')
            service.fulfill(writer, writer.get(ActivityAward, award_id), event, operator, 'refund', '现场缺货')
            writer.commit()
    sqlalchemy_event.listen(engine, 'after_cursor_execute', refund_during_read)
    try:
        record = rows(sheet(client, activity['id']))[f'#{award_id}']
        assert triggered and record[6] == '待领取' and record[11] == 0
    finally:
        sqlalchemy_event.remove(engine, 'after_cursor_execute', refund_during_read)
    record = rows(sheet(client, activity['id']))[f'#{award_id}']
    assert record[6] == '已退款' and record[11] == 20


def test_refund_mismatch_summary_does_not_count_as_completed_refund(client):
    event = create(client)
    award_id = participate(client, event)[0]
    assert post(client, f'awards/{award_id}/fulfill', action='refund', reason='缺货').status_code == 200
    with SessionLocal() as db:
        for entry in db.query(ActivityPointsEntry).filter_by(kind='activity_refund'):
            if json.loads(entry.detail_json).get('award_id') == award_id:
                entry.delta = 10
        db.commit()
    ws = sheet(client, event['id'])
    assert rows(ws)[f'#{award_id}'][11] == 10
    assert '已退款0' in ws['A2'].value and '待核对1' in ws['A2'].value


def test_duplicate_refund_entries_are_flagged_even_when_total_matches(client):
    event = create(client)
    award_id = participate(client, event)[0]
    assert post(client, f'awards/{award_id}/fulfill', action='refund', reason='缺货').status_code == 200
    with SessionLocal() as db:
        original = next(entry for entry in db.query(ActivityPointsEntry).filter_by(kind='activity_refund')
                        if json.loads(entry.detail_json).get('award_id') == award_id)
        original.delta = 10
        db.add(ActivityPointsEntry(employee_id=original.employee_id, revision=1, delta=10, kind=original.kind,
            origin=original.origin, business_date=original.business_date, detail_json=original.detail_json))
        db.commit()
    ws = sheet(client, event['id'])
    record = rows(ws)[f'#{award_id}']
    assert record[6] == '退款待核对' and record[11] == 20
    assert '已退款0' in ws['A2'].value and '待核对1' in ws['A2'].value


def test_malformed_audit_award_reference_does_not_break_whole_export(client):
    event = create(client)
    award_id = participate(client, event)[0]
    with SessionLocal() as db:
        db.add(AuditLog(operator_id=employee_id('GSMTEST01'), operator_name='旧日志', action='活动奖品核销',
            entity_type='activity', entity_id=str(event['id']), after_json=json.dumps({'award_id': []})))
        db.commit()
    record = rows(sheet(client, event['id']))[f'#{award_id}']
    assert record[6] == '待领取' and record[7:9] == [None,None]


def test_correct_then_reclaim_exports_only_new_claim_time_and_operator(client):
    event = create(client, helper_employee_nos=['SUPTEST01'])
    award_id = participate(client, event)[0]
    assert legacy_claim(client, award_id, '第一次核销').status_code == 200
    assert post(client, f'awards/{award_id}/fulfill', action='correct', reason='误点').status_code == 200
    corrected = rows(sheet(client, event['id']))[f'#{award_id}']
    assert corrected[6] == '待领取' and corrected[7:9] == [None,None]
    login(client, 'SUPTEST01')
    assert legacy_claim(client, award_id, '实际领取').status_code == 200
    with SessionLocal() as db:
        log = db.query(AuditLog).filter_by(entity_id=str(event['id']), action='活动奖品核销').order_by(AuditLog.id.desc()).first()
        expected_time, expected_operator = log.created_at, log.operator_name
    login(client, 'GSMTEST01')
    reclaimed = rows(sheet(client, event['id']))[f'#{award_id}']
    assert reclaimed[6] == '旧核销（无签名）' and reclaimed[8] == expected_operator
    assert abs((reclaimed[7]-expected_time).total_seconds()) < .0011


def test_gsm_level_helper_still_cannot_export_another_owners_activity(client):
    event = create(client, helper_employee_nos=['TAGSMTEST01'])
    login(client, 'TAGSMTEST01')
    assert find(client, event['id'])['manager'] is True
    denied = client.get(f'/api/activities/{event["id"]}/export')
    assert denied.status_code == 403 and denied.headers['content-type'].startswith('application/json')
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(entity_id=str(event['id']), action='导出活动领取核销').count() == 0
