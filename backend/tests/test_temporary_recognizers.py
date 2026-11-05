from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

import pytest
from PIL import Image

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="temporary-recognizers-")
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal
from app.v2_crypto import hash_password
from app.services.identity import base_role_at, role_at, recognition_score_for_role, TEMPORARY_RECOGNIZER_LABEL
from app.v2_models import (Employee, EmployeeRoleAssignment, EmployeeActingDuty, GroupMembership,
                          MonthClosure, RecognitionRecord, Role, UserAccount, AuditLog,
                          TemporaryRecognitionQualification)


def login(client, account="HR01", password="HR123"):
    client.post('/api/logout')
    response = client.post('/api/login', json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


@pytest.fixture
def setup():
    with TestClient(app) as client:
        with SessionLocal() as db:
            peer = db.query(Employee).filter_by(employee_no="CMTEST02").one()
            number = str(7000000 + uuid4().int % 2000000)
            employee = Employee(employee_no=number, name='临时认可测试', attraction_id=peer.attraction_id,
                                is_active=True, hired_on='2026-01-01')
            db.add(employee); db.flush()
            role = db.query(Role).filter_by(code='TR').one()
            db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=role.id, starts_on='2026-01-01',
                                          assignment_type='permanent', status='active'))
            db.add(UserAccount(employee_id=employee.id, login_account=number, password_hash=hash_password('1234'),
                               enabled=True, must_change_password=False))
            db.commit(); eid, circle = employee.id, employee.attraction_id
        login(client)
        yield client, eid, circle, number


def info(client, eid):
    response = client.get('/api/hr/temporary-recognizers')
    assert response.status_code == 200, response.text
    return next(e for e in response.json()['employees'] if e['id'] == eid)


def save(client, eid, days, **kwargs):
    body = dict(dates=days, reason='临时主管认可安排', expected_revision=info(client, eid)['revision'])
    body.update(kwargs)
    return client.put(f'/api/hr/temporary-recognizers/{eid}', json=body)


def choices(client, circle, day):
    response = client.get('/api/recognizers', params={'attraction_id': circle, 'recognition_date': day})
    assert response.status_code == 200, response.text
    return response.json()


def entry(client, eid, day):
    options = client.get('/api/options').json()
    return dict(recognition_date=day, occurred_attraction_id=next(r['id'] for r in options['recognition_venues'] if r['name']=='热力追踪'),
                recognition_type_id=next(r['id'] for r in options['recognition_types'] if r['code']=='SAFETY'),
                recognizer_employee_id=str(eid), content='临时认可验证', idempotency_key=uuid4().hex)


def test_discrete_days_no_identity_or_permissions_and_optimistic_conflict(setup):
    client, eid, circle, number = setup
    day = date.today().isoformat()
    second = day[:8] + ('03' if day[-2:] != '03' else '05')
    before = info(client, eid)
    assert save(client, eid, [day, second, day]).status_code == 200
    assert info(client, eid)['dates'] == sorted({day, second})
    response = client.put(f'/api/hr/temporary-recognizers/{eid}', json={'dates': [], 'reason': '并发修改', 'expected_revision': before['revision']})
    assert response.status_code == 409
    rows = choices(client, circle, day)
    row = next(r for r in rows if r['id'] == eid)
    assert row['role_name'] == TEMPORARY_RECOGNIZER_LABEL and row['temporary_qualification']
    with SessionLocal() as db:
        assert role_at(db, eid).code == base_role_at(db, eid).code == 'TR'
        assert db.query(EmployeeActingDuty).filter_by(employee_id=eid).count() == 0
        assert db.query(GroupMembership).filter_by(employee_id=eid).count() == 0
        expected = recognition_score_for_role(db, db.query(Role).filter_by(code='TA_SUPERVISOR').one().id, day)
        assert Decimal(str(row['score'])) == expected
        assert db.query(AuditLog).filter_by(entity_type='employee', entity_id=eid, action='调整临时主管认可资格').count() == 1
    non_day = day[:8] + '07'
    assert eid not in {r['id'] for r in choices(client, circle, non_day)}
    login(client, number, '1234')
    me = client.get('/api/me').json()
    assert me['role_code'] == 'TR'
    assert not ({'EMPLOYEE_ADD', 'DEDUCTION_DIRECT', 'REVIEW_DIRECT', 'MEMBER_RECORDS', 'HR_MANAGE'} & set(me['permissions']))
    assert client.get('/api/hr/temporary-recognizers').status_code == 403
    assert client.put(f'/api/hr/temporary-recognizers/{eid}', json={'dates': []}).status_code == 403


@pytest.mark.parametrize('batch', [False, True])
def test_self_registration_counts_score_and_blocks_cancelling_referenced_day(setup, batch):
    client, eid, circle, _ = setup
    day = date.today().isoformat()
    assert save(client, eid, [day]).status_code == 200
    login(client, 'CMTEST02', '1234')
    data = entry(client, eid, day)
    if batch:
        data.pop('idempotency_key')
    out = BytesIO(); Image.new('RGB', (10, 10), 'blue').save(out, format='PNG')
    body = {'entries': json.dumps([data]), 'idempotency_key': uuid4().hex} if batch else data
    response = client.post('/api/recognitions/batch' if batch else '/api/recognitions', data=body,
                           files={'image': ('proof.png', out.getvalue(), 'image/png')})
    assert response.status_code == 200, response.text
    record = response.json()['records'][0] if batch else response.json()['record']
    with SessionLocal() as db:
        row = db.get(RecognitionRecord, record['id'])
        assert row.recognizer_role_snapshot == TEMPORARY_RECOGNIZER_LABEL
        assert row.recognizer_base_role_code_snapshot == 'TR'
        assert row.recognizer_role_code_snapshot == 'TR'
        assert row.status == 'pending' and row.fraction > 0
    login(client, 'SUPTEST01', '1234')
    reviewed = client.post(f"/api/reviews/{record['id']}", json={'action':'confirm'})
    assert reviewed.status_code == 200, reviewed.text
    with SessionLocal() as db:
        row = db.get(RecognitionRecord, record['id'])
        role = db.query(Role).filter_by(code='TA_SUPERVISOR').one()
        assert row.status == 'confirmed'
        assert row.fraction == recognition_score_for_role(db, role.id, day, 'CM')
    login(client)
    assert save(client, eid, []).status_code == 409
    assert info(client, eid)['dates'] == [day]


def test_outside_dates_and_self_recognizer_are_rejected(setup):
    client, eid, circle, number = setup
    day = date.today().isoformat()
    assert save(client, eid, [day]).status_code == 200
    login(client, 'SUPTEST01', '1234')
    data = entry(client, eid, day[:8] + ('08' if day[-2:] != '08' else '09'))
    with SessionLocal() as db:
        data['employee_id'] = db.query(Employee).filter_by(employee_no='CMTEST02').one().id
    assert client.post('/api/recognitions', data=data).status_code == 400
    login(client, number, '1234')
    assert client.post('/api/recognitions', data=entry(client, eid, day)).status_code == 400


def test_cancel_unreferenced_day_and_regrant_with_audit(setup):
    client, eid, circle, _ = setup
    day = date.today().isoformat()
    assert save(client, eid, [day]).status_code == 200
    assert save(client, eid, []).status_code == 200
    assert eid not in {r['id'] for r in choices(client, circle, day)}
    assert save(client, eid, [day]).status_code == 200
    with SessionLocal() as db:
        assert db.query(TemporaryRecognitionQualification).filter_by(employee_id=eid).count() == 1


def test_backdated_recognition_uses_historical_qualification(setup):
    client, eid, circle, _ = setup
    past = (date.today().replace(day=1) - timedelta(days=1)).isoformat()
    with SessionLocal() as db:
        admin = db.query(Employee).filter_by(employee_no='HR01').one()
        db.add(TemporaryRecognitionQualification(employee_id=eid, qualified_on=past, reason='历史任命', updated_by=admin.id, status='active'))
        db.commit()
    assert next(r for r in choices(client, circle, past) if r['id'] == eid)['temporary_qualification']
    login(client, 'SUPTEST01', '1234')
    data = entry(client, eid, past)
    with SessionLocal() as db:
        data['employee_id'] = db.query(Employee).filter_by(employee_no='CMTEST02').one().id
    response = client.post('/api/recognitions', data=data)
    assert response.status_code == 200, response.text
    assert response.json()['record']['status'] == 'confirmed'


def test_validation_scope_and_month_close(setup):
    client, eid, circle, _ = setup
    day = date.today().isoformat()
    assert save(client, eid, [day], reason=' ').status_code == 400
    assert save(client, eid, ['2026-13-01']).status_code == 400
    assert save(client, eid, [day[:7]+'-32']).status_code == 400
    outside = (date.today().replace(day=1)-timedelta(days=1)).isoformat()
    assert save(client, eid, [outside]).status_code == 400
    # Scope enforcement is independent of the client's employee picker.
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(login_account='HR-DWARF').one()
        account.enabled=True; account.must_change_password=False; account.password_hash=hash_password('1234'); db.commit()
    login(client, 'HR-DWARF', '1234')
    overview = client.get('/api/hr/temporary-recognizers').json()
    assert eid not in {e['id'] for e in overview['employees']}
    assert client.put(f'/api/hr/temporary-recognizers/{eid}', json={'dates':[day]}).status_code == 403
    login(client)
    with SessionLocal() as db:
        closure = MonthClosure(closure_month=day[:7], attraction_id=circle, status='closed')
        db.add(closure); db.commit(); cid=closure.id
    try:
        assert save(client, eid, [day]).status_code == 423
    finally:
        with SessionLocal() as db:
            db.delete(db.get(MonthClosure,cid)); db.commit()


def test_actual_acting_duty_and_ineligible_staff_cannot_be_granted(setup):
    client, eid, _, _ = setup
    day = date.today().isoformat()
    with SessionLocal() as db:
        role = db.query(Role).filter_by(code='TA_SUPERVISOR').one()
        db.add(EmployeeActingDuty(employee_id=eid, role_id=role.id, starts_on=day, ends_on=day, status='active'))
        db.commit()
    assert save(client, eid, [day]).status_code == 409
    with SessionLocal() as db:
        db.query(EmployeeActingDuty).filter_by(employee_id=eid).delete()
        db.get(Employee, eid).is_active=False; db.commit()
    # Ineligible staff disappear from the creation picker, and direct requests fail.
    from app.services.temporary_recognition import revision
    response = client.put(f'/api/hr/temporary-recognizers/{eid}', json={'dates':[day], 'reason':'测试', 'expected_revision': revision([])})
    assert response.status_code == 400


def test_cancellation_between_validation_and_write_cannot_create_recognition(setup, monkeypatch):
    client, eid, _, _ = setup
    day = date.today().isoformat()
    assert save(client, eid, [day]).status_code == 200
    login(client, 'SUPTEST01', '1234')
    data = entry(client, eid, day)
    with SessionLocal() as db:
        data['employee_id'] = db.query(Employee).filter_by(employee_no='CMTEST02').one().id
    from app.routers import recognitions
    original = recognitions.recognition_score_for_role

    def cancel_after_validation(*args, **kwargs):
        score = original(*args, **kwargs)
        with SessionLocal() as other:
            row = other.query(TemporaryRecognitionQualification).filter_by(employee_id=eid, qualified_on=day).one()
            row.status = 'cancelled'
            other.commit()
        return score

    monkeypatch.setattr(recognitions, 'recognition_score_for_role', cancel_after_validation)
    response = client.post('/api/recognitions', data=data)
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        assert db.query(RecognitionRecord).filter_by(recognizer_employee_id=eid).count() == 0


@pytest.mark.parametrize('legacy_snapshot', [False, True])
def test_temporary_issuer_and_operator_do_not_count_as_leader_signers(setup, legacy_snapshot):
    client, eid, circle, _ = setup
    day = date.today().isoformat()
    assert save(client, eid, [day]).status_code == 200
    login(client, 'SUPTEST01', '1234')
    data = entry(client, eid, day)
    with SessionLocal() as db:
        target = db.query(Employee).filter_by(employee_no='CMTEST02').one()
        supervisor = db.query(Employee).filter_by(employee_no='SUPTEST01').one().id
        data['employee_id'] = target.id
    response = client.post('/api/recognitions', data=data)
    assert response.status_code == 200, response.text
    rid = response.json()['record']['id']
    with SessionLocal() as db:
        record = db.get(RecognitionRecord, rid)
        role = db.query(Role).filter_by(code='TA_SUPERVISOR').one()
        assert record.recognizer_employee_id == eid
        assert record.operator_employee_id == supervisor
        assert record.fraction == recognition_score_for_role(db, role.id, day, 'CM')
        if legacy_snapshot:
            record.recognizer_role_code_snapshot = 'TA_SUPERVISOR'
            db.commit()
        else:
            assert record.recognizer_role_code_snapshot == record.recognizer_base_role_code_snapshot == 'TR'
    login(client, 'GSMTEST01', '1234')
    params = dict(start_date=day, end_date=day, category='leader', sort_by='count', page_size=500)
    response = client.get('/api/pr-rankings', params=params)
    assert response.status_code == 200, response.text
    rows = response.json()['rows']
    assert eid not in {r['employee_id'] for r in rows}
    assert next(r for r in rows if r['employee_id']==supervisor)['count'] == 0
    exported = client.get('/api/pr-rankings/export', params=params)
    assert exported.status_code == 200, exported.text
    from openpyxl import load_workbook
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    # The exported name is unique to this fixture's employee number.
    with SessionLocal() as db:
        number = db.get(Employee,eid).employee_no
    assert not any(str(value) == number for sheet in workbook for row in sheet.values for value in row)
    workbook.close()


def test_rehire_cancels_old_upcoming_qualification_and_keeps_historical_recognition(setup, monkeypatch):
    client, eid, circle, number = setup
    today = date.today()
    day, past = today.isoformat(), (today-timedelta(days=10)).isoformat()
    last = (today.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
    assert save(client, eid, sorted({day,last.isoformat()})).status_code == 200
    with SessionLocal() as db:
        admin = db.query(Employee).filter_by(employee_no='HR01').one()
        db.add(TemporaryRecognitionQualification(employee_id=eid, qualified_on=past, status='active', reason='历史资格', updated_by=admin.id))
        db.commit()
    login(client, 'SUPTEST01', '1234')
    data=entry(client,eid,past)
    with SessionLocal() as db:
        data['employee_id']=db.query(Employee).filter_by(employee_no='CMTEST02').one().id
    response=client.post('/api/recognitions',data=data)
    assert response.status_code==200,response.text
    rid=response.json()['record']['id']
    login(client)
    with SessionLocal() as db:
        employee=db.get(Employee,eid)
        employee.is_active=False;employee.terminated_on=(today-timedelta(days=5)).isoformat()
        account=db.query(UserAccount).filter_by(employee_id=eid).one()
        account.enabled=False;account.disabled_at=datetime.now()-timedelta(days=4)
        db.commit()
    removed=client.request('DELETE',f'/api/hr/employees/{eid}/account',json={})
    assert removed.status_code==200,removed.text
    body={'employee_no':number,'name':'临时认可测试','role_code':'TR','attraction_id':circle}
    from app.routers import employees as employee_routes
    def fail_after_rehire_cleanup(*args, **kwargs):
        raise RuntimeError('synthetic post-rehire failure')
    with monkeypatch.context() as patch:
        patch.setattr(employee_routes,'synchronize_gsm_management_scope',fail_after_rehire_cleanup)
        with pytest.raises(RuntimeError,match='synthetic post-rehire failure'):
            client.post('/api/hr/employees',json=body)
    with SessionLocal() as db:
        assert db.get(Employee,eid).account_deleted_at is not None
        assert db.query(UserAccount).filter_by(employee_id=eid).count()==0
        assert all(r.status=='active' for r in db.query(TemporaryRecognitionQualification).filter_by(employee_id=eid).all())
    response=client.post('/api/hr/employees',json=body)
    assert response.status_code==200,response.text
    assert response.json()['rehired'] and response.json()['employee']['id']==eid
    with SessionLocal() as db:
        rows=db.query(TemporaryRecognitionQualification).filter_by(employee_id=eid).all()
        assert all(r.status=='cancelled' for r in rows if r.qualified_on>=day)
        assert next(r for r in rows if r.qualified_on==past).status=='active'
        record=db.get(RecognitionRecord,rid)
        assert record.status=='confirmed' and record.recognizer_role_snapshot==TEMPORARY_RECOGNIZER_LABEL
        assert role_at(db,eid).code=='TR'
        audit=db.query(AuditLog).filter_by(entity_type='employee',entity_id=eid,action='重新入职注册').one()
        assert json.loads(audit.after_json)['cancelled_temporary_recognizer_dates']==sorted({day,last.isoformat()})
    assert eid not in {r['id'] for r in choices(client,circle,day)}
    assert next(r for r in choices(client,circle,past) if r['id']==eid)['temporary_qualification']
    assert save(client,eid,[day]).status_code==200
