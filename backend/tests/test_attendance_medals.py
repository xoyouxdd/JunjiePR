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
from app.services import attendance_medals as service
from app.v2_auth import V2User, current_user
from app.v2_models import (Attraction, Employee, UserAccount, Role, EmployeeRoleAssignment, AuditLog,
    AttendanceMonthlyScore, SickLeaveRecord, MedalAccountIdentity, MedalLeaveMonth,
    MedalLeaveBatch, MedalCircleRun, AttendanceMedal, MedalNotice)


class Clock(date):
    value = date(2027, 2, 2)
    @classmethod
    def today(cls):
        return cls.value


@pytest.fixture
def system(monkeypatch):
    monkeypatch.setattr(service, 'date', Clock)
    monkeypatch.setattr(routes, 'date', Clock)
    Clock.value = date(2027, 2, 2)
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def fk(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    seed_reference_data(db)
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
    item=preview(client,circles[0],'2026-11');assert item['can_publish']
    Clock.value=date(2027,1,7)
    dataset(db);confirm(client)
    assert not preview(client,circles[0])['can_publish']


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
