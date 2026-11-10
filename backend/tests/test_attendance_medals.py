"""Isolated honor workflow regressions; never opens the user's database."""
import json
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

os.environ['RECOGNITION_V2_DATA_DIR'] = tempfile.mkdtemp(prefix='attendance-medals-')

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base, get_db
from app.database.reference_data import seed_reference_data
from app.routers import attendance_medals as routes, sick_leave_import
from app.routers.attendance_medals import current_operator
from app.services import attendance_medals as service
from app.v2_auth import V2User, current_user
from app.v2_models import (Attraction, Employee, UserAccount, Role, EmployeeRoleAssignment, AuditLog,
    AttendanceMonthlyScore, SickLeaveRecord, MedalAccountIdentity, MedalLeaveMonth,
    MedalLeaveBatch, MedalCircleRun, AttendanceMedal, MedalNotice, DeductionLevel, DeductionRecord,
    DeductionType, MonthClosure, StoredFile)


class Clock(date):
    value = date(2027, 2, 2)
    @classmethod
    def today(cls):
        return cls.value


@pytest.fixture
def system(monkeypatch):
    monkeypatch.setattr(service, 'date', Clock)
    monkeypatch.setattr(routes, 'date', Clock)
    monkeypatch.setattr(routes, 'current_operator', lambda db, user: user)
    Clock.value = date(2027, 2, 2)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def fk(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    seed_reference_data(db)
    db.add_all(MonthClosure(closure_month=month, status='closed')
               for month in ('2026-11', '2026-12', '2027-01'))
    db.commit()
    circles = db.query(Attraction).filter_by(employee_circle=True).order_by(Attraction.id).all()[:3]
    roles = {r.code:r for r in db.query(Role).all()}
    index = 0
    def make(name='张三', circle=0, role='CM', created=datetime(2026, 10, 2), **kwargs):
        nonlocal index
        index += 1
        person = Employee(employee_no=str(1700000+index), name=name, attraction_id=circles[circle].id,
            hired_on='2026-01-01', is_active=True, **kwargs)
        db.add(person); db.flush()
        db.add(EmployeeRoleAssignment(employee_id=person.id, role_id=roles[role].id, starts_on='2026-01-01'))
        if created:
            db.add(UserAccount(employee_id=person.id, login_account=person.employee_no, password_hash='unused', created_at=created))
        db.commit()
        return person
    admin = make('管理员', role='SYSTEM_ADMIN')
    importer = make('导入员', role='GSM')
    hrs = [make('圈管理员'+str(i), circle=i, role='HR_CIRCLE') for i in range(3)]
    member = make()
    holder = {}
    def switch(person=admin, role='SYSTEM_ADMIN', permissions=None):
        holder['user'] = V2User(person, None, roles[role], permissions if permissions is not None else {'SYSTEM_ADMIN'})
    switch()
    api = FastAPI()
    api.include_router(routes.router, prefix='/api')
    api.include_router(sick_leave_import.router, prefix='/api')
    def session():
        try: yield db
        finally: db.rollback()
    api.dependency_overrides[get_db] = session
    api.dependency_overrides[current_user] = lambda:holder['user']
    with TestClient(api, raise_server_exceptions=True) as client:
        yield db, client, circles, member, make, switch, hrs, importer, admin
    db.close(); engine.dispose()


def dataset(db, month='2027-01', leaves=None, issues=None):
    state = db.get(MedalLeaveMonth, month)
    if not state:
        state = MedalLeaveMonth(month=month, revision=0)
        db.add(state)
    state.revision += 1;state.confirmed_revision = None
    db.add(MedalLeaveBatch(month=month, revision=state.revision, filename='test.xls', digest='x'*64,
        leaves=json.dumps(leaves or []), issues=json.dumps(issues or []), ignored_codes='[]', imported_by='导入员'))
    db.commit()


def confirm(client, month='2027-01'):
    data = client.get('/api/attendance-medals/manage', params={'month':month}).json()
    response = client.post('/api/attendance-medals/confirm-data',json={'month':month,'revision':data['revision']})
    assert response.status_code == 200, response.text


def preview(client, circle, month='2027-01'):
    response=client.get('/api/attendance-medals/manage',params={'month':month})
    assert response.status_code == 200,response.text
    return next(i for i in response.json()['items'] if i['attraction_id']==circle.id)


def publish(client, item, **kwargs):
    body={key:item[key] for key in ('month','attraction_id','revision','fingerprint')}
    return client.post('/api/attendance-medals/publish',json={**body,**kwargs})


def leave(person, kind='事假'):
    return {'employee_id':person.id,'employee_no':person.employee_no,'date':'2027-01-05','hours':'1','leave_type':kind}


def test_separate_confirmation_scopes_idempotency_and_no_scores(system):
    db, client, circles, person, make, switch, hrs, importer, admin=system
    dataset(db)
    assert not preview(client,circles[0])['can_publish']
    switch(importer,'GSM',{'SICK_LEAVE_IMPORT'})
    confirm(client)
    assert client.post('/api/attendance-medals/publish',json={'month':'2027-01','attraction_id':circles[0].id,'revision':1,'fingerprint':'x'}).status_code==403
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    data=client.get('/api/attendance-medals/manage',params={'month':'2027-01'}).json()
    assert len(data['items'])==1 and not data['can_import'] and data['batch'] is None
    assert client.post('/api/attendance-medals/confirm-data',json={'month':'2027-01','revision':1}).status_code==403
    item=preview(client,circles[0]);result=publish(client,item)
    assert result.status_code==200,result.text
    assert db.query(AttendanceMedal).filter_by(employee_id=person.id).count()==1
    assert publish(client,item).json()['duplicate'] is True
    assert client.post('/api/attendance-medals/publish',json={**{k:item[k] for k in ('month','revision','fingerprint')},'attraction_id':circles[1].id}).status_code==403
    assert db.query(AttendanceMonthlyScore).count()==0 and db.query(SickLeaveRecord).count()==0
    assert db.query(MedalNotice).filter_by(employee_id=person.id).count()==1
    switch(person,'CM',set())
    assert client.get('/api/attendance-medals/manage',params={'month':'2027-01'}).status_code==403


def test_reimport_revocation_is_explicit_private_and_reversible(system):
    db, client, circles, person, make, switch, hrs, importer, admin=system
    other=make('李四')
    dataset(db);confirm(client);assert publish(client,preview(client,circles[0])).status_code==200
    old=preview(client,circles[0]);dataset(db,leaves=[leave(person)])
    assert db.query(AttendanceMedal).filter_by(employee_id=person.id).one().status=='awarded'
    assert publish(client,old).status_code==409
    confirm(client);item=preview(client,circles[0]);assert item['status']=='待调整'
    assert [c['action'] for c in item['changes']]==['撤回']
    assert publish(client,item).status_code==400
    assert publish(client,item,reason='补录事假').json()['revoked']==1
    switch(other,'CM',set())
    public=client.get(f'/api/attendance-medals/employees/{person.id}').json()
    assert public['count']==0 and 'assessments' not in public and 'notices' not in public
    switch(person,'CM',set())
    private=client.get(f'/api/attendance-medals/employees/{person.id}').json()
    assert private['assessments'][0]['leaves'][0]['leave_type']=='事假'
    assert any(n['reason']=='补录事假' for n in private['notices'])
    foreign=db.query(MedalNotice).filter_by(employee_id=other.id).first()
    assert client.post(f'/api/attendance-medals/notices/{foreign.id}/read').status_code==404
    switch();dataset(db);confirm(client)
    assert publish(client,preview(client,circles[0]),reason='请假已撤销').json()['awarded']==1
    assert db.query(AttendanceMedal).filter_by(employee_id=person.id).count()==1


def test_trial_never_visible_or_notified_and_future_month_blocked(system):
    db,client,circles,person,make,switch,*_=system
    for month in ('2026-11','2026-12'):
        dataset(db,month);confirm(client,month)
        assert publish(client,preview(client,circles[0],month)).status_code==200
    assert db.query(AttendanceMedal).filter_by(employee_id=person.id).count()==2
    assert db.query(MedalNotice).count()==0
    assert client.get(f'/api/attendance-medals/employees/{person.id}').json()['count']==0
    Clock.value=date(2026,11,7)
    assert client.get(f'/api/attendance-medals/employees/{person.id}').json()=={'visible':False,'items':[],'count':0}
    assert client.get('/api/attendance-medals/people',params={'keyword':'张'}).json()['items']==[]
    item=preview(client,circles[0],'2026-11');assert not item['can_publish']
    Clock.value=date(2027,1,7)
    dataset(db);confirm(client)
    assert not preview(client,circles[0])['can_publish']


def test_month_close_gates_publication_and_trial_observers_are_read_only(system):
    db,client,circles,person,make,switch,hrs,importer,admin=system
    db.query(MonthClosure).delete()
    db.commit()
    month='2026-11'
    dataset(db,month);confirm(client,month)
    item=preview(client,circles[0],month)
    assert item['status']=='待月结' and not item['can_publish']
    assert publish(client,item).status_code==409

    scoped=MonthClosure(closure_month=month,attraction_id=circles[0].id,status='closed')
    db.add(scoped);db.commit()
    assert not preview(client,circles[1],month)['can_publish']
    item=preview(client,circles[0],month)
    assert item['can_publish'] and publish(client,item).status_code==200
    assert db.query(MedalNotice).count()==0

    for role in ('GSM','AM','OM','SYSTEM_ADMIN'):
        viewer=admin if role=='SYSTEM_ADMIN' else importer if role=='GSM' else make(role,role=role)
        switch(viewer,role,{'SYSTEM_ADMIN'} if role=='SYSTEM_ADMIN' else set())
        response=client.get('/api/attendance-medals/manage',params={'month':month,'trial_view':True})
        assert response.status_code==200,response.text
        data=response.json()
        assert response.headers['Cache-Control']=='private, no-store' and not data['can_import']
        assert data['items'][0]['status']=='已发放' and data['items'][0]['read_only']
        assert any(row['employee_no']==person.employee_no for row in data['items'][0]['rows'])
        assert data['items'][1]['status']=='待月结' and not data['items'][1]['rows']
        assert all(not row['leaves'] for row in data['items'][0]['rows'])
        if role!='SYSTEM_ADMIN':assert publish(client,item).status_code==403

    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    scoped_view=client.get('/api/attendance-medals/manage',params={'month':month}).json()
    assert [row['attraction_id'] for row in scoped_view['items']]==[circles[0].id]
    for role in ('CM','TR','TA_SUPERVISOR','SUPERVISOR','TA_GSM'):
        switch(make(role,role=role),role,set())
        assert client.get('/api/attendance-medals/manage',params={'month':month,'trial_view':True}).status_code==403

    scoped.status='open';db.commit()
    switch(importer,'GSM',{'SICK_LEAVE_IMPORT'})
    reopened=client.get('/api/attendance-medals/manage',params={'month':month,'trial_view':True}).json()
    assert reopened['items'][0]['status']=='待月结' and not reopened['items'][0]['rows']

    switch();dataset(db);confirm(client)
    assert not preview(client,circles[0])['can_publish']
    db.add(MonthClosure(closure_month='2027-01',status='closed'));db.commit()
    assert preview(client,circles[0])['can_publish']


def test_formal_medal_visibility_tracks_reopen_without_changing_awards_or_scores(system):
    db, client, circles, person, make, switch, *_ = system
    peer = make('另一个景点圈员工', circle=1)
    Clock.value = date(2027, 3, 2)
    february = MonthClosure(closure_month='2027-02', status='closed')
    january = db.query(MonthClosure).filter_by(closure_month='2027-01').one()
    db.add_all([february, MonthClosure(closure_month='2027-02', attraction_id=circles[1].id, status='closed')])
    db.commit()
    for month in ('2026-12', '2027-01', '2027-02'):
        dataset(db, month)
        confirm(client, month)
        for circle in circles[:2]:
            response = publish(client, preview(client, circle, month))
            assert response.status_code == 200, response.text

    path = f'/api/attendance-medals/employees/{person.id}'
    switch(person, 'CM', set())
    original = client.get(path).json()
    assert original['count'] == 2
    notice = db.query(MedalNotice).filter_by(employee_id=person.id, month='2027-02').one()
    read_path = f'/api/attendance-medals/notices/{notice.id}/read'

    february.status = 'reopened'
    db.commit()
    own = client.get(path).json()
    assert own['count'] == 1 and [item['month'] for item in own['items']] == ['2027-01']
    assert [item['month'] for item in own['assessments']] == ['2027-01']
    assert len(own['notices']) == 1 and own['notices'][0]['id'] != notice.id
    assert own['pending_month'] == {'month': '2027-02', 'status': '待月结'}
    assert client.post(read_path).status_code == 404
    switch(peer, 'CM', set())
    assert client.get(path).json()['count'] == 1
    assert client.get(f'/api/attendance-medals/employees/{peer.id}').json()['count'] == 2

    january.status = 'reopened'
    db.commit()
    assert client.get(path).json()['count'] == 0
    february.status = 'closed'
    db.commit()
    switch(person, 'CM', set())
    restored = client.get(path).json()
    assert restored['count'] == 1 and restored['items'] == [original['items'][0]]
    assert restored['assessments'] == [original['assessments'][0]]
    assert restored['notices'] == [original['notices'][0]]
    assert client.post(read_path).status_code == 200
    assert db.query(AttendanceMedal).filter_by(employee_id=person.id, status='awarded').count() == 3
    assert db.query(MedalNotice).filter_by(employee_id=person.id).count() == 2
    assert db.query(AttendanceMonthlyScore).count() == db.query(DeductionRecord).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
def test_trial_reclose_restores_winners_when_only_ineligibility_reason_changes(system, month):
    db, client, circles, person, make, switch, hrs, importer, admin = system
    circle = circles[0]
    winner = make('全勤员工')
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    level = db.query(DeductionLevel).filter_by(code='STATEMENT').one()
    kind = db.query(DeductionType).filter_by(code='ATT_LATE_WITHIN_30').one()
    pdf = StoredFile(storage_key='late.pdf', original_filename='late.pdf', extension='.pdf',
        mime_type='application/pdf', file_size=1, sha256='0'*64, uploaded_by=admin.id, status='active')
    db.add(pdf)
    db.flush()
    db.add(DeductionRecord(employee_id=person.id, employee_no=person.employee_no, employee_name=person.name,
        employee_role_snapshot='CM', attraction_id_snapshot=circle.id, deduction_type_id=kind.id,
        deduction_type_name=kind.name, deduction_level_id=level.id, deduction_level_name=level.name,
        points=Decimal('1'), occurred_on=f'{month}-05', deduction_month=month, description='迟到',
        document_file_id=pdf.id, submitter_id=admin.id, submitter_name=admin.name, submitter_role_snapshot='GSM',
        permission_scope_snapshot='测试', status='active'))
    db.commit()
    dataset(db, month)
    confirm(client, month)
    before = preview(client, circle, month)
    assert publish(client, before).status_code == 200
    run = db.query(MedalCircleRun).filter_by(month=month, attraction_id=circle.id).one()
    published_snapshot = run.results

    def observed():
        switch(importer, 'GSM', set())
        response = client.get('/api/attendance-medals/manage', params={'month': month, 'trial_view': True})
        assert response.status_code == 200, response.text
        return next(item for item in response.json()['items'] if item['attraction_id'] == circle.id)

    original = observed()
    assert original['status'] == '已发放'
    winners = {row['employee_no'] for row in original['rows']}
    assert winner.employee_no in winners and person.employee_no not in winners
    closure.status = 'open'
    db.commit()
    assert not observed()['rows']
    switch()
    dataset(db, month, leaves=[dict(leave(person), date=f'{month}-05')])
    confirm(client, month)
    closure.status = 'closed'
    db.commit()
    assert observed() == original

    switch(hrs[0], 'HR_CIRCLE', set())
    after = preview(client, circle, month)
    old_row = next(row for row in before['rows'] if row['employee_id'] == person.id)
    new_row = next(row for row in after['rows'] if row['employee_id'] == person.id)
    assert old_row['status'] == new_row['status'] == '未获得'
    assert old_row['reason'] != new_row['reason'] and not after['changes']
    assert after['status'] == '已发放'
    assert publish(client, after).json() == {'ok': True, 'duplicate': True, 'awarded': 0, 'revoked': 0}
    assert run.results == published_snapshot
    assert db.query(DeductionRecord).one().points == Decimal('1')
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
def test_trial_reclose_hides_stale_winners_until_scoped_hr_confirms(system, month):
    db, client, circles, person, make, switch, hrs, importer, _ = system
    eligible = make('仍符合资格')
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    dataset(db, month)
    confirm(client, month)
    assert publish(client, preview(client, circles[0], month)).status_code == 200

    def observed():
        switch(importer, 'GSM', set())
        response = client.get('/api/attendance-medals/manage', params={'month': month, 'trial_view': True})
        assert response.status_code == 200, response.text
        return next(item for item in response.json()['items'] if item['attraction_id'] == circles[0].id)

    original = observed()
    winners = {row['employee_no'] for row in original['rows']}
    assert {person.employee_no, eligible.employee_no} <= winners
    closure.status = 'reopened'
    db.commit()
    hidden = observed()
    assert hidden['status'] == '待月结' and not hidden['rows']
    closure.status = 'closed'
    db.commit()
    assert observed() == original

    closure.status = 'reopened'
    db.commit()
    switch()
    dataset(db, month, leaves=[dict(leave(person), date=f'{month}-05')])
    closure.status = 'closed'
    db.commit()
    hidden = observed()
    assert hidden['status'] == '待数据就绪' and not hidden['rows']
    switch()
    confirm(client, month)
    hidden = observed()
    assert hidden['status'] == '待调整' and not hidden['rows']
    switch(hrs[1], 'HR_CIRCLE', set())
    assert publish(client, dict(preview(client, circles[1], month), attraction_id=circles[0].id)).status_code == 403
    switch(hrs[0], 'HR_CIRCLE', set())
    adjustment = preview(client, circles[0], month)
    assert adjustment['status'] == '待调整'
    assert publish(client, adjustment, reason='确认补录请假').json()['revoked'] == 1
    confirmed = observed()
    assert confirmed['status'] == '已发放'
    assert {row['employee_no'] for row in confirmed['rows']} == winners - {person.employee_no}
    switch(eligible, 'CM', set())
    assert client.get(f'/api/attendance-medals/employees/{eligible.id}').json()['count'] == 0
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
def test_trial_cross_circle_reclose_requires_target_hr_to_revoke_moved_medal(system, month):
    db, client, circles, person, make, switch, hrs, importer, _ = system
    source, target = circles[:2]
    winner = make('目标圈全勤员工', circle=1)
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    dataset(db, month)
    confirm(client, month)
    for circle in (source, target):
        assert publish(client, preview(client, circle, month)).status_code == 200
    medal = db.query(AttendanceMedal).filter_by(employee_id=person.id, month=month).one()
    assert medal.attraction_id == source.id and medal.status == 'awarded'

    def observe_target():
        switch(importer, 'GSM', set())
        response = client.get('/api/attendance-medals/manage', params={'month': month, 'trial_view': True})
        assert response.status_code == 200, response.text
        return next(item for item in response.json()['items'] if item['attraction_id'] == target.id)

    original = observe_target()
    assert original['status'] == '已发放'
    assert [row['employee_no'] for row in original['rows']] == [winner.employee_no]
    closure.status = 'open'
    db.commit()
    assert not observe_target()['rows']

    switch()
    dataset(db, month, leaves=[dict(leave(person), date=f'{month}-05')])
    confirm(client, month)
    response = client.post('/api/attendance-medals/repair-scope', json={
        'month': month, 'employee_id': person.id, 'attraction_id': target.id, 'reason': '核实历史归属'})
    assert response.status_code == 200, response.text
    closure.status = 'closed'
    db.commit()
    assert observe_target() == original
    switch()
    assert publish(client, preview(client, source, month), reason='确认来源圈调整').status_code == 200
    assert medal.attraction_id == target.id and medal.status == 'awarded'

    pending = observe_target()
    assert pending['status'] == '待调整' and not pending['rows']
    switch(hrs[1], 'HR_CIRCLE', set())
    adjustment = preview(client, target, month)
    assert [(change['employee_id'], change['action']) for change in adjustment['changes']] == [(person.id, '撤回')]
    switch(hrs[0], 'HR_CIRCLE', set())
    assert publish(client, adjustment, reason='越圈确认').status_code == 403
    switch(hrs[1], 'HR_CIRCLE', set())
    response = publish(client, adjustment, reason='撤回已失去资格的勋章')
    assert response.status_code == 200 and response.json()['revoked'] == 1, response.text
    assert medal.status == 'revoked'
    assert publish(client, adjustment).json()['duplicate'] is True
    assert observe_target() == original
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
@pytest.mark.parametrize('target_already_published', [False, True])
def test_trial_cross_circle_confirmation_rejects_old_withdrawal_list(system, month, target_already_published):
    db, client, circles, person, make, switch, hrs, *_ = system
    source, target = circles[:2]
    peer = make('目标圈原获奖员工', circle=1)
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    dataset(db, month)
    confirm(client, month)
    assert publish(client, preview(client, source, month)).status_code == 200
    if target_already_published:
        assert publish(client, preview(client, target, month)).status_code == 200
    closure.status = 'open'
    db.commit()
    dataset(db, month, leaves=[dict(leave(employee), date=f'{month}-05') for employee in (person, peer)])
    confirm(client, month)
    response = client.post('/api/attendance-medals/repair-scope', json={
        'month': month, 'employee_id': person.id, 'attraction_id': target.id, 'reason': '核实历史归属'})
    assert response.status_code == 200, response.text
    closure.status = 'closed'
    db.commit()

    switch(hrs[1], 'HR_CIRCLE', set())
    stale = preview(client, target, month)
    assert stale['status'] == ('待调整' if target_already_published else '待确认发放')
    previously_seen = {peer.id} if target_already_published else set()
    assert {(change['employee_id'], change['action']) for change in stale['changes']} == {
        (employee_id, '撤回') for employee_id in previously_seen}
    switch()
    assert publish(client, preview(client, source, month), reason='确认来源圈调整').status_code == 200

    switch(hrs[1], 'HR_CIRCLE', set())
    response = publish(client, stale, reason='确认旧名单')
    assert response.status_code == 409 and '刷新' in response.json()['detail'], response.text
    medals = db.query(AttendanceMedal).filter_by(month=month, attraction_id=target.id).all()
    expected = previously_seen | {person.id}
    assert {medal.employee_id for medal in medals if medal.status == 'awarded'} == expected
    fresh = preview(client, target, month)
    assert fresh['rows'] == stale['rows'] and fresh['revision'] == stale['revision']
    assert fresh['fingerprint'] != stale['fingerprint']
    assert {(change['employee_id'], change['action']) for change in fresh['changes']} == {
        (employee_id, '撤回') for employee_id in expected}
    response = publish(client, fresh, reason='重新核对后确认')
    assert response.status_code == 200 and response.json()['revoked'] == len(expected), response.text
    assert publish(client, stale, reason='重试旧名单').status_code == 409
    assert publish(client, fresh, reason='重新核对后确认').json() == {
        'ok': True, 'duplicate': True, 'awarded': 0, 'revoked': 0}
    assert {medal.status for medal in medals} == {'revoked'}
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
@pytest.mark.parametrize('source_moves_after_preview', [False, True])
def test_trial_highest_target_confirmation_identifies_same_name_withdrawals(system, month, source_moves_after_preview):
    db, client, circles, person, make, switch, hrs, importer, _ = system
    source, target, other_source = circles[:3]
    same_name = make(person.name, circle=2)
    winner = make('目标圈全勤员工', circle=1)
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    dataset(db, month)
    confirm(client, month)
    for circle in (source, target, other_source):
        assert publish(client, preview(client, circle, month)).status_code == 200
    medals = {medal.employee_id: medal for medal in db.query(AttendanceMedal).filter(
        AttendanceMedal.month == month, AttendanceMedal.employee_id.in_((person.id, same_name.id)))}
    closure.status = 'open'
    db.commit()
    dataset(db, month, leaves=[dict(leave(employee), date=f'{month}-05') for employee in (person, same_name)])
    confirm(client, month)
    for employee in (person, same_name):
        response = client.post('/api/attendance-medals/repair-scope', json={
            'month': month, 'employee_id': employee.id, 'attraction_id': target.id, 'reason': '核实历史归属'})
        assert response.status_code == 200, response.text
    closure.status = 'closed'
    db.commit()

    before = preview(client, target, month)
    assert before['status'] == '待调整'
    expected = [(employee.id, employee.name, employee.employee_no, '撤回') for employee in (person, same_name)]
    assert [(c['employee_id'], c['name'], c['employee_no'], c['action']) for c in before['changes']] == expected
    assert all('跨圈移入后撤回' in c['reason'] for c in before['changes'])
    switch(hrs[1], 'HR_CIRCLE', set())
    assert not preview(client, target, month)['changes']
    assert publish(client, before, reason='尝试跨圈确认').status_code == 409
    assert medals[person.id].attraction_id == source.id and medals[same_name.id].attraction_id == other_source.id
    assert all(medal.status == 'awarded' for medal in medals.values())
    switch()

    if source_moves_after_preview:
        assert publish(client, preview(client, source, month), reason='确认来源圈归属').status_code == 200
        response = publish(client, before, reason='确认旧预览')
        assert response.status_code == 409 and '刷新' in response.json()['detail'], response.text
        assert medals[person.id].attraction_id == target.id and medals[same_name.id].attraction_id == other_source.id
        assert all(medal.status == 'awarded' for medal in medals.values())
        current = preview(client, target, month)
        assert [(c['employee_id'], c['name'], c['employee_no'], c['action']) for c in current['changes']] == expected
        assert '跨圈移入后撤回' not in current['changes'][0]['reason']
        assert '跨圈移入后撤回' in current['changes'][1]['reason']
    else:
        current = before
    response = publish(client, current, reason='确认目标圈撤回')
    assert response.status_code == 200 and response.json()['revoked'] == 2, response.text
    assert all(medal.attraction_id == target.id and medal.status == 'revoked' for medal in medals.values())
    assert publish(client, current).json()['duplicate'] is True
    switch(importer, 'GSM', set())
    response = client.get('/api/attendance-medals/manage', params={'month': month, 'trial_view': True})
    observed = next(item for item in response.json()['items'] if item['attraction_id'] == target.id)
    assert observed['status'] == '已发放'
    assert [row['employee_no'] for row in observed['rows']] == [winner.employee_no]
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
def test_trial_cross_circle_confirmation_refreshes_changed_employee_number(system, month):
    from app.routers import employees

    db, client, circles, person, make, switch, hrs, _, admin = system
    client.app.include_router(employees.router, prefix='/api')
    source, target = circles[:2]
    same_name = make(person.name, circle=1)
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    dataset(db, month)
    confirm(client, month)
    for circle in (source, target):
        assert publish(client, preview(client, circle, month)).status_code == 200
    medal = db.query(AttendanceMedal).filter_by(employee_id=person.id, month=month).one()
    closure.status = 'open'
    db.commit()
    dataset(db, month, leaves=[dict(leave(person), date=f'{month}-05')])
    confirm(client, month)
    response = client.post('/api/attendance-medals/repair-scope', json={
        'month': month, 'employee_id': person.id, 'attraction_id': target.id, 'reason': '核实历史归属'})
    assert response.status_code == 200, response.text
    closure.status = 'closed'
    db.commit()

    old_source = preview(client, source, month)
    old_target = preview(client, target, month)
    old_number = person.employee_no
    assert old_source['changes'][0]['employee_no'] == old_target['changes'][0]['employee_no'] == old_number
    switch(admin, 'SYSTEM_ADMIN', {'SYSTEM_ADMIN', 'HR_MANAGE'})
    new_number = '1899991'
    response = client.post(f'/api/hr/employees/{person.id}/employee-number', json={
        'new_employee_no': new_number, 'reason': '核实员工新工号'})
    assert response.status_code == 200, response.text
    for stale in (old_source, old_target):
        response = publish(client, stale, reason='确认旧工号预览')
        assert response.status_code == 409 and '刷新' in response.json()['detail'], response.text
        assert medal.attraction_id == source.id and medal.status == 'awarded'

    fresh_source = preview(client, source, month)
    identity = (person.id, person.name, new_number)
    assert [(c['employee_id'], c['name'], c['employee_no'], c['action']) for c in fresh_source['changes']] == [
        (*identity, '移出')]
    response = publish(client, fresh_source, reason='按新工号确认来源圈')
    assert response.status_code == 200 and response.json()['revoked'] == 0, response.text
    assert medal.attraction_id == target.id and medal.status == 'awarded'
    assert publish(client, fresh_source).json()['duplicate'] is True

    switch(hrs[1], 'HR_CIRCLE', set())
    fresh_target = preview(client, target, month)
    assert [(c['employee_id'], c['name'], c['employee_no'], c['action']) for c in fresh_target['changes']] == [
        (*identity, '撤回')]
    response = publish(client, fresh_target, reason='按新工号确认撤回')
    assert response.status_code == 200 and response.json()['revoked'] == 1, response.text
    assert publish(client, fresh_target).json()['duplicate'] is True
    assert medal.status == 'revoked'
    assert db.query(AttendanceMedal).filter_by(employee_id=same_name.id, month=month).one().status == 'awarded'
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
def test_trial_source_confirmation_refreshes_changed_destination(system, month):
    db, client, circles, person, _, switch, hrs, *_ = system
    source, original_target, new_target = circles[:3]
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    dataset(db, month)
    confirm(client, month)
    for circle in circles[:3]:
        assert publish(client, preview(client, circle, month)).status_code == 200
    medal = db.query(AttendanceMedal).filter_by(employee_id=person.id, month=month).one()
    closure.status = 'open'
    db.commit()
    dataset(db, month, leaves=[dict(leave(person), date=f'{month}-05')])
    confirm(client, month)

    def repair(destination):
        response = client.post('/api/attendance-medals/repair-scope', json={
            'month': month, 'employee_id': person.id, 'attraction_id': destination.id, 'reason': '核实历史归属'})
        assert response.status_code == 200, response.text

    repair(original_target)
    closure.status = 'closed'
    db.commit()
    stale = preview(client, source, month)
    repair(new_target)
    response = publish(client, stale, reason='确认旧目标圈')
    assert response.status_code == 409 and '刷新' in response.json()['detail'], response.text
    assert medal.attraction_id == source.id and medal.status == 'awarded'
    assert [(c['employee_id'], c['action'], c['target_circle_id'], c['reason']) for c in stale['changes']] == [
        (person.id, '移出', original_target.id, f'将移入 {original_target.name}')]

    fresh = preview(client, source, month)
    assert fresh['rows'] == stale['rows'] and fresh['revision'] == stale['revision']
    assert [(c['employee_id'], c['action'], c['target_circle_id'], c['reason']) for c in fresh['changes']] == [
        (person.id, '移出', new_target.id, f'将移入 {new_target.name}')]
    response = publish(client, fresh, reason='核对新目标圈')
    assert response.status_code == 200 and response.json()['revoked'] == 0, response.text
    assert medal.attraction_id == new_target.id and medal.status == 'awarded'
    assert publish(client, fresh).json()['duplicate'] is True
    switch(hrs[2], 'HR_CIRCLE', set())
    target = preview(client, new_target, month)
    assert [(c['employee_id'], c['action']) for c in target['changes']] == [(person.id, '撤回')]
    assert publish(client, target, reason='确认撤回').json()['revoked'] == 1
    assert medal.status == 'revoked' and publish(client, target).json()['duplicate'] is True
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
@pytest.mark.parametrize(('old_status', 'eligible', 'source_first', 'action'), [
    ('awarded', True, False, '移入'), ('revoked', False, False, '移入'),
    ('revoked', True, False, '补发'), ('awarded', True, True, '确认名单'),
])
def test_trial_cross_circle_confirmation_matches_existing_medal_state(system, month, old_status, eligible, source_first, action):
    db, client, circles, person, make, switch, hrs, importer, _ = system
    source, target = circles[:2]
    winner = make('目标圈原获奖员工', circle=1)
    closure = db.query(MonthClosure).filter_by(closure_month=month).one()
    dataset(db, month)
    confirm(client, month)
    for circle in (source, target):
        assert publish(client, preview(client, circle, month)).status_code == 200
    medal = db.query(AttendanceMedal).filter_by(employee_id=person.id, month=month).one()
    leave_row = dict(leave(person), date=f'{month}-05')
    if old_status == 'revoked':
        dataset(db, month, leaves=[leave_row])
        confirm(client, month)
        assert publish(client, preview(client, source, month), reason='确认来源圈撤回').json()['revoked'] == 1

    closure.status = 'open'
    db.commit()
    dataset(db, month, leaves=[] if eligible else [leave_row])
    confirm(client, month)
    response = client.post('/api/attendance-medals/repair-scope', json={
        'month': month, 'employee_id': person.id, 'attraction_id': target.id, 'reason': '核实历史归属'})
    assert response.status_code == 200, response.text
    closure.status = 'closed'
    db.commit()

    if source_first:
        response = publish(client, preview(client, source, month), reason='确认来源圈移出')
        assert response.status_code == 200 and response.json()['moved'] == 1, response.text
        switch(hrs[1], 'HR_CIRCLE', set())
    incoming = preview(client, target, month)
    assert incoming['status'] == '待调整'
    assert [(c['employee_id'], c['action']) for c in incoming['changes']] == [(person.id, action)]
    if not source_first:
        assert incoming['changes'][0]['source_circle_id'] == source.id
        assert incoming['changes'][0]['target_circle_id'] == target.id
        switch(hrs[1], 'HR_CIRCLE', set())
        assert publish(client, incoming, reason='尝试跨圈确认').status_code == 409
        assert medal.attraction_id == source.id and medal.status == old_status
        switch()
    response = publish(client, incoming, reason='核对移入名单')
    assert response.status_code == 200, response.text
    assert response.json()['awarded'] == int(action == '补发') and response.json()['revoked'] == 0
    assert response.json().get('moved', 0) == int(not source_first)
    assert medal.attraction_id == target.id and medal.status == ('awarded' if eligible else 'revoked')
    assert publish(client, incoming).json()['duplicate'] is True
    if old_status == 'awarded' and not source_first:
        source_after = preview(client, source, month)
        assert [(c['employee_id'], c['action']) for c in source_after['changes']] == [(person.id, '确认名单')]
        response = publish(client, source_after, reason='同步来源圈获奖名单')
        assert response.status_code == 200 and response.json()['revoked'] == 0, response.text
        assert medal.attraction_id == target.id and medal.status == 'awarded'
    switch(importer, 'GSM', set())
    response = client.get('/api/attendance-medals/manage', params={'month': month, 'trial_view': True})
    observed = next(item for item in response.json()['items'] if item['attraction_id'] == target.id)
    expected = {winner.employee_no}
    if eligible:
        expected.add(person.employee_no)
    assert observed['status'] == '已发放' and {r['employee_no'] for r in observed['rows']} == expected
    assert db.query(MedalNotice).count() == db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize('month', ['2026-11', '2026-12'])
@pytest.mark.parametrize('operation', ['publish', 'repair-scope'])
def test_trial_cross_circle_rechecks_highest_role_under_write_lock(system, monkeypatch, month, operation):
    db, client, circles, person, _, _, _, _, admin = system
    source, target, other = circles[:3]
    dataset(db, month)
    confirm(client, month)
    assert publish(client, preview(client, source, month)).status_code == 200
    stale_identity = V2User(admin, db.query(UserAccount).filter_by(employee_id=admin.id).one(),
                            db.query(Role).filter_by(code='SYSTEM_ADMIN').one(), {'SYSTEM_ADMIN'})
    client.app.dependency_overrides[current_user] = lambda: stale_identity

    def read_under_lock(session, user):
        assert session.connection().connection.driver_connection.in_transaction
        return current_operator(session, user)

    monkeypatch.setattr(routes, 'current_operator', read_under_lock)
    path = '/api/attendance-medals/repair-scope'
    payload = {'month': month, 'employee_id': person.id, 'attraction_id': target.id, 'reason': '核实归属'}
    assert client.post(path, json=payload).status_code == 200
    pending = preview(client, source, month)
    locked = routes.locked

    def revoke_before_lock(session, callback):
        assignment = session.query(EmployeeRoleAssignment).filter_by(employee_id=admin.id).one()
        assignment.role_id = session.query(Role.id).filter_by(code='HR_CIRCLE').scalar()
        session.commit()
        return locked(session, callback)

    monkeypatch.setattr(routes, 'locked', revoke_before_lock)
    if operation == 'publish':
        response = publish(client, pending, reason='尝试继续跨圈处理')
        assert response.status_code == 409, response.text
    else:
        response = client.post(path, json={**payload, 'attraction_id': other.id})
        assert response.status_code == 403, response.text
    medal = db.query(AttendanceMedal).filter_by(employee_id=person.id, month=month).one()
    assert medal.attraction_id == source.id and medal.status == 'awarded'
    assert json.loads(db.get(MedalLeaveMonth, month).roster)[str(person.id)] == target.id


def test_only_effective_attendance_exceptions_and_leave_disqualify(system):
    db,client,circles,person,make,switch,*_=system
    month='2027-01'
    level=db.query(DeductionLevel).filter_by(code='STATEMENT').one()
    pdf=StoredFile(storage_key='attendance-medal-proof.pdf',original_filename='proof.pdf',extension='.pdf',
                   mime_type='application/pdf',file_size=1,sha256='0'*64,uploaded_by=person.id,status='active')
    db.add(pdf);db.flush()
    expected={person.id:'拟发放'}
    types=['ATT_LATE_WITHIN_30','ATT_LATE_OVER_30','ATT_EARLY_LEAVE_WITHIN_30','ATT_EARLY_LEAVE_OVER_30',
           'ATT_EARLY_CLOCK','ATT_LATE_CLOCK','ATT_MISSING_CLOCK','SICK_LEAVE_VIOLATION']
    cases=[(code,'active',month,'未获得') for code in types]+[
        (code,'active',month,'拟发放') for code in ('SAFETY','COURTESY','SHOW','ATT_REMOTE_CLOCK')
    ]+[
        ('ATT_MISSING_CLOCK','void',month,'拟发放'),('ATT_LATE_CLOCK','pending_material',month,'拟发放'),
        ('ATT_LATE_CLOCK','active','2026-12','拟发放'),
    ]
    for code,state,record_month,status in cases:
        employee=make(code+' '+state)
        kind=db.query(DeductionType).filter_by(code=code).one()
        db.add(DeductionRecord(employee_id=employee.id,employee_no=employee.employee_no,employee_name=employee.name,
            employee_role_snapshot='CM',attraction_id_snapshot=circles[0].id,deduction_type_id=kind.id,
            deduction_type_name=kind.name,deduction_level_id=level.id,deduction_level_name=level.name,points=Decimal('0'),
            occurred_on=record_month+'-05',deduction_month=record_month,description='测试考勤',document_file_id=pdf.id,
            submitter_id=person.id,submitter_name=person.name,submitter_role_snapshot='GSM',permission_scope_snapshot='测试',status=state))
        expected[employee.id]=status
    for state,status in [('active','未获得'),('void','拟发放'),('covered','拟发放')]:
        employee=make('跨月病假 '+state)
        db.add(SickLeaveRecord(employee_id=employee.id,attendance_month='2026-12',leave_start_date='2026-12-30',
            leave_end_date='2027-01-02',leave_days=2,charged_days=0,status=state,submitted_by=person.id,submitted_by_name=person.name))
        expected[employee.id]=status
    db.commit()
    dataset(db,month);confirm(client,month)
    item=preview(client,circles[0],month)
    actual={row['employee_id']:row['status'] for row in item['rows']}
    assert {employee_id:actual[employee_id] for employee_id in expected}==expected


def test_account_qualification_history_disabled_rebuilt_and_rehire(system):
    db,client,circles,person,make,switch,*_=system
    created=make('新员工',created=datetime(2027,1,1))
    assert service.qualification(db,created,'2027-01')[0]=='不参与'
    assert service.qualification(db,created,'2027-02')[0]=='拟发放'
    account=db.query(UserAccount).filter_by(employee_id=person.id).one();account.enabled=False;db.commit()
    assert service.qualification(db,person,'2027-01')[0]=='拟发放'
    original=db.get(MedalAccountIdentity,person.id).first_created_at
    db.delete(account);db.commit()
    db.add(UserAccount(employee_id=person.id,login_account=person.employee_no,password_hash='unused',created_at=datetime(2027,1,3)));db.commit()
    assert db.get(MedalAccountIdentity,person.id).first_created_at==original
    assert service.qualification(db,person,'2027-01')[0]=='拟发放'
    person.terminated_on='2027-01-30';person.is_active=False;db.commit()
    assert service.qualification(db,person,'2027-01')[1]=='月中离职'
    person.terminated_on='2027-01-31';db.commit()
    assert service.qualification(db,person,'2027-01')[0]=='拟发放'
    person.employment_history=json.dumps([{'hired_on':'2026-01-01','terminated_on':'2027-01-31','rehired_on':'2027-03-01','attraction_id':circles[0].id}])
    person.hired_on='2027-03-01';person.terminated_on=None;person.is_active=True;db.commit()
    assert service.qualification(db,person,'2027-01')[0]=='拟发放'
    assert service.qualification(db,person,'2027-02')[0]=='不参与'
    assert service.qualification(db,person,'2027-03')[0]=='不参与'
    assert service.qualification(db,person,'2027-04')[0]=='拟发放'


def test_snapshot_and_stale_employee_changes(system):
    db,client,circles,person,make,switch,*_=system
    dataset(db);confirm(client);old=preview(client,circles[0])
    person.terminated_on='2027-01-10';db.commit()
    assert publish(client,old).status_code==409
    person.terminated_on=None;person.attraction_id=circles[1].id;db.commit()
    assert any(r['employee_id']==person.id for r in preview(client,circles[0])['rows'])
    assert not any(r['employee_id']==person.id for r in preview(client,circles[1])['rows'])
    assert publish(client,preview(client,circles[0])).status_code==200
    person.employee_no='1999999';db.commit()
    assert client.get(f'/api/attendance-medals/employees/{person.id}').json()['count']==1


def test_issues_only_block_affected_circle_and_unknown_blocks_all(system):
    db,client,circles,*_=system
    dataset(db,issues=[{'circle_id':circles[0].id,'reason':'姓名不匹配'}]);confirm(client)
    assert not preview(client,circles[0])['can_publish']
    assert preview(client,circles[1])['can_publish']
    dataset(db,issues=[{'circle_id':None,'reason':'工号不存在'}]);confirm(client)
    assert all(not preview(client,c)['can_publish'] for c in circles)


class Sheet:
    name='全员事务'
    def __init__(self, rows):
        self.ncols=max(map(len,rows));self.rows=[r+['']*(self.ncols-len(r)) for r in rows];self.nrows=len(rows)
    def row_values(self,i):return self.rows[i]


def workbook(monkeypatch, rows):
    totals={}
    for row in rows:
        try:
            hours=Decimal(str(row[4]))
            if not hours.is_finite() or hours <= 0 or not row[3]:continue
        except (InvalidOperation,IndexError):continue
        key=(row[0],row[1],service.leave_total_type(row[3]))
        totals[key]=totals.get(key,Decimal('0'))+hours
    summaries=[[name,no,code,str(hours)] for (name,no,code),hours in totals.items()]
    sheet=Sheet([['事务:'],['员工','ID','日期','工资代码','时数'],*rows,['总数:'],['员工','ID','工资代码','时数'],*summaries])
    class Book:
        datemode=0
        def sheets(self):return [sheet]
    monkeypatch.setattr(sick_leave_import.xlrd,'open_workbook',lambda **kw:Book())
    return sheet


def test_all_leave_types_fractional_hours_rest_and_duplicate_import(system,monkeypatch):
    db,client,circles,person,*_=system
    rows=[['张三','0'+person.employee_no,'2027-01-05',kind,hours] for kind,hours in
        [('事假',1),('年假',0.25),('法定病假',8),('调休假',8),('法定节假日',8),('正常工作',8)]]
    workbook(monkeypatch,rows)
    leaves,issues,ignored=service.read_all_leaves(db,b'test','2027-01')
    assert not issues and {r['leave_type'] for r in leaves}=={'事假','年假','法定病假'}
    assert set(ignored)=={'调休假','法定节假日','正常工作'}
    files={'workbook':('all.xls',b'test')}
    first=client.post('/api/attendance-medals/import-preview',data={'month':'2027-01'},files=files).json()
    payload={'month':'2027-01','mode':'append','expected_revision':first['revision'],'file_digest':first['file_digest']}
    assert client.post('/api/attendance-medals/import',data=payload,files=files).status_code==200
    assert client.post('/api/attendance-medals/import',data=payload,files=files).status_code==409
    payload['expected_revision']=1
    assert client.post('/api/attendance-medals/import',data=payload,files=files).json()['leaves']==3
    assert db.query(AttendanceMonthlyScore).count()==0


def test_empty_month_validated_and_bad_hours_unmatched_are_blocking(system,monkeypatch):
    db,client,circles,person,*_=system
    workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','正常工作',8]])
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01');assert not leaves and not issues
    workbook(monkeypatch,[['张三',person.employee_no,'2027-02-05','事假',1]])
    assert client.post('/api/attendance-medals/import-preview',data={'month':'2027-01'},files={'workbook':('a.xls',b'x')}).status_code==400
    workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','事假',-1],['错误名字',person.employee_no,'2027-01-05','年假',1]])
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01');assert not leaves and len(issues)==2


def test_identity_repair_permissions_and_audit(system):
    db,client,circles,person,make,switch,hrs,*_=system
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    payload={'employee_id':person.id,'first_created_on':'2026-09-01','reason':'核查原账号台账'}
    assert client.post('/api/attendance-medals/repair-identity',json=payload).status_code==403
    switch();assert client.post('/api/attendance-medals/repair-identity',json=payload).status_code==200
    assert db.get(MedalAccountIdentity,person.id).first_created_at==datetime(2026,9,1)
    assert db.query(AuditLog).filter_by(action='核实全勤勋章账号原始日期').count()==1


def test_unchanged_reimport_needs_no_circle_reconfirmation(system):
    db,client,circles,person,*_=system
    dataset(db);confirm(client);assert publish(client,preview(client,circles[0])).status_code==200
    notice_count=db.query(MedalNotice).count()
    dataset(db);confirm(client)
    item=preview(client,circles[0]);assert item['status']=='已发放'
    assert publish(client,item).json()['duplicate']
    assert db.query(MedalNotice).count()==notice_count
    assert client.get('/api/attendance-medals/history',params={'month':'2027-01','attraction_id':circles[0].id}).json()['items']


def test_empty_circle_can_finish_and_atomic_failure_rolls_back(system,monkeypatch):
    db,client,circles,person,*_=system
    dataset(db);confirm(client)
    empty=preview(client,circles[2]);assert not any(r['status']=='拟发放' for r in empty['rows'])
    assert publish(client,empty).json()['awarded']==0
    assert preview(client,circles[2])['status']=='已发放'
    original=service.write_audit
    def fail(*args,**kwargs):
        if args[2]=='确认全勤勋章发放':raise RuntimeError('simulated failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(service,'write_audit',fail)
    with pytest.raises(RuntimeError,match='simulated failure'):
        publish(client,preview(client,circles[0]))
    assert db.query(AttendanceMedal).count()==0 and db.query(MedalNotice).count()==0
    assert db.query(MedalCircleRun).filter_by(attraction_id=circles[0].id).count()==0


def test_highest_scope_correction_and_history_privacy(system):
    db,client,circles,person,make,switch,hrs,*_=system
    dataset(db);confirm(client);publish(client,preview(client,circles[0]))
    payload={'month':'2027-01','employee_id':person.id,'attraction_id':circles[1].id,'reason':'核实月底归属'}
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    assert client.post('/api/attendance-medals/repair-scope',json=payload).status_code==403
    assert client.get('/api/attendance-medals/history',params={'month':'2027-01','attraction_id':circles[1].id}).status_code==403
    switch();assert client.post('/api/attendance-medals/repair-scope',json=payload).status_code==200
    before_notices=db.query(MedalNotice).count()
    assert publish(client,preview(client,circles[0]),reason='转移归属').status_code==200
    assert publish(client,preview(client,circles[1]),reason='接收归属').status_code==200
    medal=db.query(AttendanceMedal).filter_by(employee_id=person.id).one()
    assert medal.attraction_id==circles[1].id and medal.status=='awarded'
    assert db.query(MedalNotice).count()==before_notices
    switch(person,'CM',set())
    own=client.get(f'/api/attendance-medals/employees/{person.id}').json()
    assert own['count']==1 and len(own['assessments'])==1


def test_historical_circle_uses_direct_edit_audit(system):
    db,client,circles,person,*_=system
    old_circle=person.attraction_id
    person.attraction_id=circles[1].id
    db.add(AuditLog(operator_name='管理员',action='更新员工',entity_type='employee',entity_id=str(person.id),
        before_json=json.dumps({'attraction_id':old_circle}),after_json=json.dumps({'attraction_id':circles[1].id}),
        created_at=datetime(2027,2,1)))
    db.commit()
    assert service.roster(db,'2027-01')[str(person.id)]==old_circle


def test_real_biff_import_honors_include_personal_leave_without_score_writes(system):
    from pathlib import Path
    db,client,circles,person,make,*_=system
    person.employee_no='1836527';person.name='张森 Jason'
    other=make('Jason');other.employee_no='0123456'
    ann=make('Ann');ann.employee_no='1234567';db.commit()
    content=(Path(__file__).parent/'fixtures/sick_leave_identity.xls').read_bytes()
    # Equal-width SST replacement keeps the actual OLE/BIFF workbook structure.
    for before,after in [('法定病假','带薪事假'),('全薪病假','带薪事假'),('病假时间总计','带薪事假总计')]:
        assert before.encode('utf-16le') in content
        content=content.replace(before.encode('utf-16le'),after.encode('utf-16le'))
    leaves,issues,_=service.read_all_leaves(db,content,'2096-06')
    assert not issues and len(leaves)==4
    assert sum(r['leave_type']=='带薪事假' for r in leaves)==4
    assert db.query(AttendanceMonthlyScore).count()==0


def test_public_search_aliases_cache_and_temporary_identity_denied(system):
    db,client,circles,person,make,switch,*_=system
    switch(person,'CM',set())
    response=client.get('/api/attendance-medals/people',params={'keyword':'0'+person.employee_no})
    assert [r['id'] for r in response.json()['items']]==[person.id]
    assert response.headers['cache-control']=='private, no-store' and response.headers['vary']=='Cookie'
    pending=client.get(f'/api/attendance-medals/employees/{person.id}').json()
    assert pending['pending_month']=={'month':'2027-01','status':'待数据就绪'}
    temporary=make('临时访问',created=None)
    switch(temporary,'CM',set())
    assert client.get('/api/attendance-medals/people',params={'keyword':'张'}).status_code==403
    assert client.get(f'/api/attendance-medals/employees/{person.id}').status_code==403


def test_unknown_wage_code_requires_classification_and_reimport(system,monkeypatch):
    db,client,circles,person,make,switch,hrs,importer,admin=system
    workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','UNPAID',1]])
    files={'workbook':('all.xls',b'unknown-code')}
    initial=client.post('/api/attendance-medals/import-preview',data={'month':'2027-01'},files=files).json()
    assert initial['leaves']==0 and initial['issues'][0]['code_type']=='unclassified'
    payload={'month':'2027-01','mode':'replace','expected_revision':initial['revision'],'file_digest':initial['file_digest']}
    assert client.post('/api/attendance-medals/import',data=payload,files=files).status_code==200
    assert client.post('/api/attendance-medals/confirm-data',json={'month':'2027-01','revision':1}).status_code==409
    assert not preview(client,circles[0])['can_publish']
    classification={'code':'UNPAID','kind':'leave','revision':0,'reason':'HR源表该代码表示事假'}
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    assert client.post('/api/attendance-medals/classify-code',json=classification).status_code==403
    switch(importer,'GSM',{'SICK_LEAVE_IMPORT'})
    assert client.post('/api/attendance-medals/classify-code',json=classification).json()['affected_months']==['2027-01']
    assert client.post('/api/attendance-medals/classify-code',json=classification).status_code==409
    assert client.post('/api/attendance-medals/confirm-data',json={'month':'2027-01','revision':2}).status_code==409
    assert client.post('/api/attendance-medals/import',data=payload,files=files).status_code==409
    new_preview=client.post('/api/attendance-medals/import-preview',data={'month':'2027-01'},files=files).json()
    assert new_preview['leaves']==1 and not new_preview['issues']
    payload.update(expected_revision=new_preview['revision'],file_digest=new_preview['file_digest'])
    assert client.post('/api/attendance-medals/import',data=payload,files=files).status_code==200
    confirm(client)
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    item=preview(client,circles[0]);row=next(r for r in item['rows'] if r['employee_id']==person.id)
    assert row['status']=='未获得' and row['leaves'][0]['leave_type']=='UNPAID'
    assert publish(client,item).status_code==200
    assert not db.query(AttendanceMedal).filter_by(employee_id=person.id).first()
    switch()
    assert client.post('/api/attendance-medals/classify-code',json={**classification,'code':'事假','kind':'normal'}).status_code==400


def test_legacy_ignored_unknown_codes_are_not_silently_accepted(system):
    db,client,circles,person,*_=system
    dataset(db)
    batch=db.query(MedalLeaveBatch).one();batch.ignored_codes='["ANNUAL"]'
    state=db.get(MedalLeaveMonth,'2027-01');state.confirmed_revision=state.revision;db.commit()
    assert not preview(client,circles[0])['can_publish']
    issues=client.get('/api/attendance-medals/manage',params={'month':'2027-01'}).json()['batch']['issues']
    assert issues[0]['leave_type']=='ANNUAL'


def test_classification_change_invalidates_award_preview_without_revoking(system,monkeypatch):
    from app.attendance_medals_models import MedalWageCode
    db,client,circles,person,*_=system
    classification={'code':'SPECIAL','kind':'normal','revision':0,'reason':'首次核实为普通工时'}
    assert client.post('/api/attendance-medals/classify-code',json=classification).status_code==200
    workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','SPECIAL',1]])
    service.store_import(db,V2User(db.query(Employee).filter_by(name='管理员').one(),None,
        db.query(Role).filter_by(code='SYSTEM_ADMIN').one(),{'SYSTEM_ADMIN'}),'2027-01',b'code','all.xls');db.commit()
    confirm(client);old=preview(client,circles[0]);assert publish(client,old).status_code==200
    assert client.post('/api/attendance-medals/classify-code',json={**classification,'kind':'leave','revision':1,'reason':'HR更正：该代码实际为请假'}).status_code==200
    assert db.get(MedalLeaveMonth,'2027-01').confirmed_revision is None
    assert publish(client,old).status_code==409
    assert db.query(AttendanceMedal).filter_by(employee_id=person.id).one().status=='awarded'
    assert db.query(MedalWageCode).one().kind=='leave'


def test_unresolved_identity_is_redacted_for_circle_hr_and_blocks_all(system):
    db,client,circles,person,make,switch,hrs,*_=system
    raw={'circle_id':None,'row':9,'source_name':'敏感姓名','source_no':'1999999','leave_type':'病假',
         'hours':'8','reason':'工号不存在'}
    dataset(db,issues=[raw]);confirm(client)
    assert preview(client,circles[0])['issues'][0]['source_name']=='敏感姓名'
    for hr,circle in zip(hrs,circles):
        switch(hr,'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
        item=preview(client,circle)
        assert item['issues'][0]['redacted'] and not item['can_publish']
        assert '敏感姓名' not in json.dumps(item,ensure_ascii=False)
        assert '1999999' not in json.dumps(item,ensure_ascii=False)


def test_import_identity_issue_uses_same_historical_circle_as_roster(system,monkeypatch):
    db,client,circles,person,make,switch,hrs,*_=system
    old_circle=person.attraction_id;person.attraction_id=circles[1].id
    db.add(AuditLog(operator_name='管理员',action='更新员工',entity_type='employee',entity_id=str(person.id),
        before_json=json.dumps({'attraction_id':old_circle}),after_json=json.dumps({'attraction_id':circles[1].id}),
        created_at=datetime(2027,2,1)));db.commit()
    workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','事假',-1]])
    _,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert issues[0]['circle_id']==old_circle
    assert service.circle_on(db,person,'2027-01')==old_circle
    dataset(db,issues=issues);confirm(client)
    switch(hrs[1],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    assert not preview(client,circles[1])['issues']
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    assert not preview(client,circles[0])['can_publish']


def test_alias_collision_blocks_import_instead_of_selecting_arbitrary_profile(system,monkeypatch):
    db,client,circles,person,make,*_=system
    duplicate=make('张三',circle=1);duplicate.employee_no='0'+person.employee_no;db.commit()
    workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','事假',1]])
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert not leaves and '多个档案' in issues[0]['reason']
    dataset(db,issues=issues);confirm(client)
    assert all(not preview(client,c)['can_publish'] for c in circles)


def test_trial_messages_and_future_rows_are_hidden_even_if_stored(system):
    db,client,circles,person,make,switch,*_=system
    for month in ('2026-11','2026-12','2027-01','2027-02'):
        db.add(AttendanceMedal(employee_id=person.id,month=month,attraction_id=circles[0].id))
        db.add(MedalNotice(employee_id=person.id,month=month,message=month,reason='仅本人'))
    db.commit()
    trial=db.query(MedalNotice).filter_by(month='2026-11').one()
    formal=db.query(MedalNotice).filter_by(month='2027-01').one()
    switch(person,'CM',set())
    data=client.get(f'/api/attendance-medals/employees/{person.id}').json()
    assert [r['month'] for r in data['items']]==['2027-01'] and data['count']==1
    assert [r['message'] for r in data['notices']]==['2027-01']
    assert client.post(f'/api/attendance-medals/notices/{trial.id}/read').status_code==404
    assert client.post(f'/api/attendance-medals/notices/{formal.id}/read').status_code==200
    Clock.value=date(2027,1,31)
    assert client.post(f'/api/attendance-medals/notices/{formal.id}/read').status_code==404
    assert not client.get(f'/api/attendance-medals/employees/{person.id}').json()['visible']


def test_duplicate_alias_profiles_are_blocked_even_when_file_has_no_leave(system):
    db,client,circles,person,make,*_=system
    duplicate=make('另一圈重复身份',circle=1);duplicate.employee_no='0'+person.employee_no;db.commit()
    dataset(db);confirm(client)
    for circle,target in ((circles[0],person),(circles[1],duplicate)):
        item=preview(client,circle)
        row=next(r for r in item['rows'] if r['employee_id']==target.id)
        assert row['status']=='待处理' and '多个档案' in row['reason']
        assert not row['needs_identity_date'] and not item['can_publish']
        assert service.qualification(db,target,'2027-01')[0]=='待处理'
    assert preview(client,circles[2])['can_publish']


@pytest.mark.parametrize('created,first_month',[(datetime(2026,11,1),'2026-12'),(datetime(2026,11,30),'2026-12'),
    (datetime(2026,12,1),'2027-01'),(datetime(2026,12,31),'2027-01')])
def test_account_creation_natural_month_boundaries(system,created,first_month):
    db,client,circles,person,make,*_=system
    new=make('月份边界员工',created=created)
    assert service.qualification(db,new,created.strftime('%Y-%m'))[0]=='不参与'
    assert service.qualification(db,new,first_month)[0]=='拟发放'


def test_multiple_rehire_cycles_keep_full_historical_months_and_gaps(system):
    db,client,circles,person,*_=system
    person.employment_history=json.dumps([
        {'hired_on':'2026-01-01','terminated_on':'2027-01-31','rehired_on':'2027-02-15'},
        {'hired_on':'2027-02-15','terminated_on':'2027-03-31','rehired_on':'2027-05-01'}])
    person.hired_on='2027-05-01';db.commit()
    for month,expected in [('2027-01','拟发放'),('2027-02','不参与'),('2027-03','拟发放'),
                           ('2027-04','不参与'),('2027-05','不参与'),('2027-06','拟发放')]:
        assert service.qualification(db,person,month)[0]==expected


def test_honor_import_reconciles_leave_totals_and_blocks_missing_transactions(system,monkeypatch):
    db,client,circles,person,*_=system
    sheet=workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','事假',1]])
    sheet.rows[-1][3]='8'
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert len(leaves)==1 and any('总数区不一致' in r['reason'] for r in issues)
    assert all(r['circle_id']==circles[0].id for r in issues)
    dataset(db,leaves=leaves,issues=issues);confirm(client)
    assert not preview(client,circles[0])['can_publish']
    assert preview(client,circles[1])['can_publish']
    # A source summary with leave but no corresponding transaction must never
    # turn an absent transaction into a full-attendance award.
    sheet=workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','正常工作',8]])
    sheet.rows.append(['张三',person.employee_no,'事假','8','']);sheet.nrows+=1
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert not leaves and any('事务 0 小时，总数 8 小时' in r['reason'] for r in issues)


def test_missing_transaction_code_is_not_treated_as_no_leave(system,monkeypatch):
    db,client,circles,person,*_=system
    workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','正常工作',8],
                         ['张三',person.employee_no,'2027-01-06','',1]])
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert not leaves and issues[0]['circle_id']==circles[0].id
    assert '缺少工资代码' in issues[0]['reason']


def test_unknown_summary_only_code_cannot_hide_a_missing_leave_transaction(system,monkeypatch):
    db,client,circles,person,*_=system
    sheet=workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','正常工作',8]])
    sheet.rows.append(['张三',person.employee_no,'AL总计',8,'']);sheet.nrows+=1
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert not leaves and any(i.get('code_type')=='unclassified' and i['leave_type']=='AL' for i in issues)
    dataset(db,issues=issues)
    assert client.post('/api/attendance-medals/confirm-data',json={'month':'2027-01','revision':1}).status_code==409
    assert client.post('/api/attendance-medals/classify-code',json={
        'code':'AL','kind':'leave','revision':0,'reason':'总数区AL为年假'}).status_code==200
    _,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert any('事务 0 小时，总数 8 小时' in i['reason'] for i in issues)


def test_mismatched_source_name_and_leave_type_are_private_to_global_importer(system):
    db,client,circles,person,make,switch,hrs,*_=system
    raw={'circle_id':circles[0].id,'employee_id':person.id,'source_name':'圈外源表敏感姓名',
         'source_no':person.employee_no,'leave_type':'病假','hours':'8','reason':'工号对应的姓名不匹配'}
    dataset(db,issues=[raw]);confirm(client)
    assert preview(client,circles[0])['issues'][0]['source_name']=='圈外源表敏感姓名'
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    item=preview(client,circles[0]);issue=item['issues'][0]
    assert issue['redacted']
    assert '圈外源表敏感姓名' not in json.dumps(item,ensure_ascii=False)
    assert '病假' not in json.dumps(issue,ensure_ascii=False)
    assert not item['can_publish']
    switch(hrs[1],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    assert not preview(client,circles[1])['can_publish'], '姓名与工号冲突时不能假定真实请假人属于原圈'


def test_file_number_name_conflict_blocks_every_circle_until_identity_is_verified(system,monkeypatch):
    db,client,circles,person,make,switch,hrs,*_=system
    other=make('李四',circle=1)
    workbook(monkeypatch,[['李四',person.employee_no,'2027-01-05','事假',1]])
    leaves,issues,_=service.read_all_leaves(db,b'test','2027-01')
    assert not leaves and issues[0]['identity_conflict']
    dataset(db,issues=issues);confirm(client)
    for hr,circle in zip(hrs,circles):
        switch(hr,'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
        item=preview(client,circle)
        assert not item['can_publish'] and item['issues'][0]['redacted']
    assert not db.query(AttendanceMedal).filter_by(employee_id=other.id).first()


def test_classifying_summary_only_code_differently_invalidates_month(system,monkeypatch):
    db,client,circles,person,make,switch,hrs,importer,admin=system
    assert client.post('/api/attendance-medals/classify-code',json={
        'code':'BONUS','kind':'normal','revision':0,'reason':'首次按正常薪项核实'}).status_code==200
    sheet=workbook(monkeypatch,[['张三',person.employee_no,'2027-01-05','正常工作',8]])
    sheet.rows.append(['张三',person.employee_no,'BONUS总计',8,'']);sheet.nrows+=1
    user=V2User(admin,None,db.query(Role).filter_by(code='SYSTEM_ADMIN').one(),{'SYSTEM_ADMIN'})
    service.store_import(db,user,'2027-01',b'summary-only','all.xls');db.commit()
    assert 'BONUS' in json.loads(db.query(MedalLeaveBatch).one().ignored_codes)
    confirm(client);old=preview(client,circles[0])
    changed=client.post('/api/attendance-medals/classify-code',json={
        'code':'BONUS','kind':'leave','revision':1,'reason':'HR更正该缩写代表请假'})
    assert changed.status_code==200 and changed.json()['affected_months']==['2027-01']
    assert db.get(MedalLeaveMonth,'2027-01').confirmed_revision is None
    assert publish(client,old).status_code==409


def test_historical_first_award_requires_highest_but_existing_circle_adjustment_remains_scoped(system):
    db,client,circles,person,make,switch,hrs,*_=system
    Clock.value=date(2027,4,2)
    dataset(db);confirm(client)
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    item=preview(client,circles[0]);assert item['requires_highest'] and not item['can_publish']
    assert publish(client,item).status_code==403
    switch();assert publish(client,preview(client,circles[0])).status_code==200
    dataset(db,leaves=[leave(person)]);confirm(client)
    switch(hrs[0],'HR_CIRCLE',{'SICK_LEAVE_IMPORT'})
    item=preview(client,circles[0]);assert not item['requires_highest'] and item['can_publish']
    assert publish(client,item,reason='核实该月补录请假').json()['revoked']==1


@pytest.mark.parametrize('role',['CM','TR','TA_SUPERVISOR','SUPERVISOR'])
def test_frontline_and_supervisors_cannot_manage_or_read_trial_content(system,role):
    db,client,circles,person,make,switch,*_=system
    staff=make('员工隐藏范围测试',role=role)
    dataset(db,'2026-11');confirm(client,'2026-11')
    item=preview(client,circles[0],'2026-11');assert publish(client,item).status_code==200
    assert db.query(AttendanceMedal).filter_by(employee_id=staff.id,month='2026-11').count()==1
    notice=MedalNotice(employee_id=staff.id,month='2026-11',message='试用私密内容',reason='不得展示')
    db.add(notice);db.commit()
    switch(staff,role,set())
    assert client.get('/api/attendance-medals/manage',params={'month':'2026-11'}).status_code==403
    assert publish(client,item).status_code==403
    assert client.post('/api/attendance-medals/import-preview',data={'month':'2026-11'},
        files={'workbook':('source.xls',b'ignored')}).status_code==403
    assert client.post('/api/attendance-medals/import',data={'month':'2026-11','expected_revision':0,'file_digest':'x'},
        files={'workbook':('source.xls',b'ignored')}).status_code==403
    data=client.get(f'/api/attendance-medals/employees/{staff.id}').json()
    assert data['count']==0 and not data['items'] and not data['notices'] and not data['assessments']
    assert '2026-11' not in json.dumps(data)
    assert client.post(f'/api/attendance-medals/notices/{notice.id}/read').status_code==404
    Clock.value=date(2026,11,7)
    assert client.get(f'/api/attendance-medals/employees/{staff.id}').json()=={'visible':False,'items':[],'count':0}
