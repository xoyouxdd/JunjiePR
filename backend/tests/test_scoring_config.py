"""Real isolated API, permission, rule-version, credit and persistence coverage."""
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from PIL import Image

os.environ['RECOGNITION_V2_DATA_DIR']=tempfile.mkdtemp(prefix='scoring-config-')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='1234'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='HR123'

from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal, init_db
from app.v2_crypto import hash_password
from app.v2_models import ScoringPolicy, Employee, EmployeeRoleAssignment, Role, UserAccount, RecognitionType, RecognitionRecord, DeductionType, DeductionLevel, DeductionRecord, AuditLog
from app.services import scoring_policies as P

DAY=date.today().isoformat()


def login(c, account):
    c.post('/api/logout')
    response=c.post('/api/login',json={'employee_no':account,'password':'HR123' if account=='HR01' else '1234'})
    assert response.status_code==200,response.text


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        with SessionLocal() as db:
            for account in db.query(UserAccount):
                account.must_change_password=False
                if account.login_account.startswith('HR-'):
                    account.enabled=True;account.password_hash=hash_password('1234')
            db.commit()
        yield c


@pytest.fixture(autouse=True)
def reset_policy(client):
    with SessionLocal() as db:
        db.query(ScoringPolicy).delete();db.commit()


def target(role='CM'):
    with SessionLocal() as db:
        gsm=db.query(UserAccount).filter_by(login_account='GSMTEST01').one().employee
        e=Employee(employee_no='98'+uuid4().hex[:5],name='计分隔离员工',attraction_id=gsm.attraction_id,is_active=True)
        db.add(e);db.flush()
        db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=db.query(Role).filter_by(code=role).one().id,
            starts_on='2020-01-01',status='active',assignment_type='permanent'))
        db.add(UserAccount(employee_id=e.id,login_account=e.employee_no,password_hash=hash_password('1234'),enabled=True,credential_initialized=True))
        db.commit();return e.id


def config(c):
    login(c,'HR01');r=c.get('/api/hr/scoring-config');assert r.status_code==200,r.text;return r.json()


def policy(c, code, kind='recognition', scope='frontline'):
    cfg=config(c)
    rows=cfg['recognition'][scope] if kind=='recognition' else cfg['deduction']
    row=next(row for row in rows if row['code']==code)
    fields=['name','active','mode','score','count','cap','overflow'] if kind=='recognition' else ['name','active','mode','score','repeat','group','direct','manager']
    return {field:row[field] for field in fields}


def save(c, value, code='MSP', kind='recognition',scope='frontline',day=DAY):
    cfg=config(c)
    return c.post('/api/hr/scoring-config',json={'revision':cfg['revision'],'kind':kind,'scope':scope,'code':code,'effective_date':day,'policy':value})


def recognize(c, employee, code='MSP', day=DAY, key=None):
    options=c.get('/api/options').json()
    with SessionLocal() as db:
        typ=db.query(RecognitionType).filter_by(code=code).one()
        scope='supervisor' if db.query(EmployeeRoleAssignment).filter_by(employee_id=employee).first().role.code=='SUPERVISOR' else 'frontline'
        rule=P.category(db,'recognition',scope,code,day)
        issuer='special:'+code if rule['mode']=='fixed' else str(db.query(UserAccount).filter_by(login_account='GSMTEST01').one().employee_id)
        type_id=typ.id
    return c.post('/api/recognitions',data={'employee_id':employee,'recognition_date':day,'occurred_attraction_id':options['recognition_venues'][0]['id'],
        'recognition_type_id':type_id,'recognizer_employee_id':issuer,'content':'计分规则测试','same_day_duplicate_confirmed':'true','idempotency_key':key or uuid4().hex})


def credit(response):
    assert response.status_code==200,response.text
    with SessionLocal() as db:
        row=db.get(RecognitionRecord,response.json()['record']['id'])
        return float(row.fraction),float(row.credited_fraction)


def test_defaults_match_legacy_rules_and_role_rates(client):
    cfg=config(client)
    for scope in ['frontline','supervisor']:
        rows={r['code']:r for r in cfg['recognition'][scope]}
        assert rows['MSP']['score']==3 and rows['MSP']['count'] is None
        assert rows['COMMENDATION_LETTER']['count']==1 and rows['COMMENDATION_LETTER']['overflow']=='block'
        assert all(rows[code]['cap']==5 for code in ['SAFETY','COURTESY','INCLUSION','EFFICIENCY','SHOW'])
    assert [r['score'] for r in cfg['roles']['frontline']]==[.5,.5,1,1,1.5,1.5]
    assert [r['score'] for r in cfg['roles']['supervisor']]==[1,.5,1,1.5]
    active=[r for r in cfg['deduction'] if r['active']]
    assert len(active)==18
    assert sum(r['direct'] for r in active)==16
    assert [r['score'] for r in cfg['levels']]==[1,3,5,10]


@pytest.mark.parametrize('account,readable',[('HR-HEAT',True),('HR-DWARF',True),('HR-BEAR',True),('GSMTEST01',False),('SUPTEST01',False),('CMTEST01',False),('TRTEST01',False),('TATEST01',False),('TAGSMTEST01',False),('AMTEST01',False),('OMTEST01',False)])
def test_permissions_do_not_expand(client,account,readable):
    value=policy(client,'MSP');cfg=config(client)
    login(client,account)
    assert client.get('/api/hr/scoring-config').status_code==(200 if readable else 403)
    body={'revision':cfg['revision'],'kind':'recognition','scope':'frontline','code':'MSP','effective_date':DAY,'policy':value}
    assert client.post('/api/hr/scoring-config',json=body).status_code==403
    assert client.post('/api/hr/scoring-config/batch',json={'revision':cfg['revision'],'changes':[body]}).status_code==403


def test_fixed_count_zero_and_other_scope_are_independent(client):
    value=policy(client,'MSP');value.update(score=2,count=1,overflow='zero')
    assert save(client,value).status_code==200
    employee=target();supervisor=target('SUPERVISOR')
    login(client,'GSMTEST01')
    assert credit(recognize(client,employee))==(2,2)
    assert credit(recognize(client,employee))==(2,0)
    assert credit(recognize(client,supervisor))==(3,3)


def test_fixed_score_and_category_cap_partial_credit(client):
    value=policy(client,'SAFETY');value.update(mode='fixed',score=3,cap=5,count=None)
    assert save(client,value,'SAFETY').status_code==200
    employee=target();login(client,'GSMTEST01')
    assert [credit(recognize(client,employee,'SAFETY')) for _ in range(3)]==[(3,3),(3,2),(3,0)]


def test_count_block_and_idempotent_retry(client):
    value=policy(client,'MSP');value.update(count=2,overflow='block')
    assert save(client,value).status_code==200
    employee=target();login(client,'GSMTEST01');key=uuid4().hex
    first=recognize(client,employee,key=key);assert credit(first)==(3,3)
    assert recognize(client,employee,key=key).json()['duplicate']
    assert credit(recognize(client,employee))==(3,3)
    assert recognize(client,employee).status_code==409


def test_cross_month_and_future_effective_date(client):
    future=(date.today()+timedelta(days=35)).replace(day=1).isoformat()
    value=policy(client,'MSP');value.update(score=4,count=1,overflow='zero')
    assert save(client,value,day=future).status_code==200
    employee=target();login(client,'GSMTEST01')
    assert credit(recognize(client,employee))==(3,3)
    assert credit(recognize(client,employee,day=future))==(4,4)
    assert credit(recognize(client,employee,day=future))==(4,0)


def test_pending_rule_snapshot_is_frozen_before_admin_changes(client):
    value=policy(client,'MSP');value.update(score=2,count=1,overflow='zero')
    assert save(client,value).status_code==200
    # Build a pending row using the same stored snapshot as the submission path.
    employee=target()
    with SessionLocal() as db:
        from app.routers.recognitions import apply_monthly_category_cap
        typ=db.query(RecognitionType).filter_by(code='MSP').one()
        frozen=P.category(db,'recognition','frontline','MSP',DAY)
    value.update(score=9,count=None)
    assert save(client,value).status_code==200
    login(client,'GSMTEST01')
    first=recognize(client,employee)
    assert credit(first)==(9,9)
    with SessionLocal() as db:
        row=db.get(RecognitionRecord,first.json()['record']['id'])
        row.fraction=Decimal(2);row.scoring_policy_json=P.dump(frozen)
        apply_monthly_category_cap(db,row,db.get(RecognitionType,row.recognition_type_id));db.commit()
    assert credit(first)==(2,2)


def test_new_category_survives_restart_and_can_be_disabled(client):
    value=dict(name='新增固定认可'+uuid4().hex[:4],mode='fixed',score=2,count=1,overflow='zero',cap=None,active=True)
    response=save(client,value,code='');assert response.status_code==200,response.text
    code=response.json()['code'];employee=target()
    init_db();login(client,'GSMTEST01')
    assert any(r['code']==code for r in client.get('/api/options').json()['recognition_types'])
    assert credit(recognize(client,employee,code))==(2,2)
    value['active']=False;assert save(client,value,code).status_code==200
    login(client,'GSMTEST01');assert recognize(client,employee,code).status_code==400


def test_stale_revision_and_atomic_role_batch(client):
    cfg=config(client)
    base=dict(revision=cfg['revision'],kind='role',scope='frontline',code='GSM',effective_date=DAY,policy={'score':2})
    invalid={**base,'code':'CM'}
    assert client.post('/api/hr/scoring-config/batch',json={'revision':cfg['revision'],'changes':[base,invalid]}).status_code==422
    assert config(client)['revision']==cfg['revision']
    assert client.post('/api/hr/scoring-config',json=base).status_code==200
    assert client.post('/api/hr/scoring-config',json=base).status_code==409
    employee=target();supervisor=target('SUPERVISOR');login(client,'GSMTEST01')
    assert credit(recognize(client,employee,'OTHER'))==(2,2)
    assert credit(recognize(client,supervisor,'OTHER'))==(.5,.5)


@pytest.mark.parametrize('update',[{'score':'NaN'},{'score':-1},{'count':True},{'count':1.5},{'cap':0},{'overflow':'ignore'},{'role':'SYSTEM_ADMIN'}])
def test_invalid_category_rules_do_not_write(client,update):
    value=policy(client,'MSP');value.update(update)
    before=config(client)['revision']
    assert save(client,value).status_code==422
    assert config(client)['revision']==before


def test_hard_deduction_boundaries_cannot_be_opened(client):
    value=policy(client,'ATT_LATE_OVER_30','deduction','all');value['direct']=True
    assert save(client,value,'ATT_LATE_OVER_30','deduction','all').status_code==422
    value=policy(client,'SAFETY','deduction','all');value.update(mode='fixed',score=3,direct=True)
    assert save(client,value,'SAFETY','deduction','all').status_code==422


def pdf():
    buf=BytesIO();Image.new('RGB',(50,50),'white').save(buf,format='PDF');return buf.getvalue()


def deduct(c,employee,code='SAFETY',level='STATEMENT',repeat=False):
    with SessionLocal() as db:
        kind=db.query(DeductionType).filter_by(code=code).one().id
        grade=db.query(DeductionLevel).filter_by(code=level).one().id
    return c.post('/api/deductions',data={'employee_id':employee,'deduction_type_id':kind,'deduction_level_id':grade,
        'occurred_on':DAY,'description':'扣分配置测试','idempotency_key':uuid4().hex,'repeat_confirmed':str(repeat).lower()},files={'document':('statement.pdf',pdf(),'application/pdf')})


def test_fixed_deduction_accumulates_and_role_visibility_stays_enforced(client):
    value=policy(client,'SAFETY','deduction','all');value.update(mode='fixed',score=2.5,direct=False,repeat='sum')
    assert save(client,value,'SAFETY','deduction','all').status_code==200
    employee=target();login(client,'SUPTEST01')
    assert not any(row['code']=='SAFETY' for row in client.get('/api/options').json()['deduction_types'])
    assert deduct(client,employee).status_code==403
    login(client,'GSMTEST01')
    for _ in range(2):
        result=deduct(client,employee);assert result.status_code==200,result.text
    with SessionLocal() as db:
        rows=db.query(DeductionRecord).filter_by(employee_id=employee).all()
        assert sum(row.points for row in rows)==5
        assert all(json.loads(row.scoring_policy_json)['mode']=='fixed' for row in rows)


def test_default_level_and_repeat_rule_can_be_configured_without_retroactive_changes(client):
    value=policy(client,'ATT_EARLY_CLOCK','deduction','all');value['repeat']='sum'
    assert save(client,value,'ATT_EARLY_CLOCK','deduction','all').status_code==200
    cfg=config(client)
    assert client.post('/api/hr/scoring-config',json=dict(revision=cfg['revision'],kind='level',scope='all',code='STATEMENT',effective_date=DAY,policy={'score':1.25})).status_code==200
    employee=target();login(client,'GSMTEST01')
    for _ in range(2):
        result=deduct(client,employee,'ATT_EARLY_CLOCK');assert result.status_code==200,result.text
    with SessionLocal() as db:
        assert [float(r.points) for r in db.query(DeductionRecord).filter_by(employee_id=employee)]==[1.25,1.25]


def test_concurrent_zero_limit_is_serialized(client):
    value=policy(client,'MSP');value.update(count=1,overflow='zero')
    assert save(client,value).status_code==200
    employee=target();login(client,'GSMTEST01')
    cookies=dict(client.cookies)
    def submit(_):
        with TestClient(app) as c:
            c.cookies.update(cookies)
            return credit(recognize(c,employee))[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,range(2)))==[0,3]


def test_actual_pending_submission_keeps_its_rule_when_reviewed_after_edit(client):
    value=policy(client,'MSP');value.update(score=2,count=1,overflow='zero')
    assert save(client,value).status_code==200
    login(client,'CMTEST01')
    with SessionLocal() as db:
        employee=db.query(UserAccount).filter_by(login_account='CMTEST01').one().employee_id
    buf=BytesIO();Image.new('RGB',(5,5),'white').save(buf,format='PNG')
    options=client.get('/api/options').json()
    response=client.post('/api/recognitions',data={'recognition_date':DAY,'occurred_attraction_id':options['recognition_venues'][0]['id'],
        'recognition_type_id':next(r['id'] for r in options['recognition_types'] if r['code']=='MSP'),
        'recognizer_employee_id':'special:MSP','content':'待复核快照','idempotency_key':uuid4().hex},files={'image':('evidence.png',buf.getvalue(),'image/png')})
    assert response.status_code==200,response.text
    record_id=response.json()['record']['id']
    with SessionLocal() as db:
        row=db.get(RecognitionRecord,record_id)
        assert row.status=='pending' and row.fraction==2
        reviewer=db.query(UserAccount).filter_by(employee_id=row.assigned_reviewer_id).one().login_account
    value.update(score=9,count=None);assert save(client,value).status_code==200
    login(client,reviewer)
    result=client.post(f'/api/reviews/{record_id}',json={'action':'confirm'})
    assert result.status_code==200,result.text
    assert credit(response)==(2,2)


def test_new_fixed_deduction_survives_reference_seed_and_preserves_history(client):
    value=dict(name='自定义扣分'+uuid4().hex[:4],mode='fixed',score=.75,repeat='sum',group=[],direct=False,manager=True,active=True)
    result=save(client,value,'','deduction','all');assert result.status_code==200,result.text
    code=result.json()['code'];init_db();login(client,'GSMTEST01')
    assert any(r['code']==code for r in client.get('/api/options').json()['deduction_types'])
    employee=target();result=deduct(client,employee,code);assert result.status_code==200,result.text
    with SessionLocal() as db:
        record=db.query(DeductionRecord).filter_by(employee_id=employee).one()
        old_id=record.id;assert record.points==Decimal('.75')
    value['score']=4;value['group']=[code];assert save(client,value,code,'deduction','all').status_code==200
    with SessionLocal() as db:
        assert db.get(DeductionRecord,old_id).points==Decimal('.75')


def test_rule_save_and_audit_are_atomic(client,monkeypatch):
    from app.routers import scoring_config
    value=policy(client,'MSP');before=config(client)['revision']
    def fail(*args,**kwargs):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(scoring_config,'write_audit',fail)
    with pytest.raises(RuntimeError,match='synthetic audit failure'):
        client.post('/api/hr/scoring-config',json=dict(revision=before,kind='recognition',scope='frontline',code='MSP',effective_date=DAY,policy=value))
    assert config(client)['revision']==before


def test_snapshot_migration_preserves_old_records_and_is_idempotent():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import Session
    from app.database.migrations.schema import ensure_scoring_policy_snapshots
    engine=create_engine('sqlite://')
    try:
        with Session(engine) as db:
            for table in ['recognition_records','deduction_records']:
                db.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, points TEXT)'))
                db.execute(text(f"INSERT INTO {table} VALUES (1,'3.00')"))
            ensure_scoring_policy_snapshots(db);ensure_scoring_policy_snapshots(db)
            for table in ['recognition_records','deduction_records']:
                assert db.execute(text(f'SELECT points,scoring_policy_json FROM {table}')).one()==('3.00',None)
    finally:
        engine.dispose()


def test_monthly_count_and_cap_work_together(client):
    value=policy(client,'MSP');value.update(count=1,overflow='zero',cap=2,score=3)
    assert save(client,value).status_code==200
    employee=target();login(client,'GSMTEST01')
    assert credit(recognize(client,employee))==(3,2)
    assert credit(recognize(client,employee))==(3,0)


def test_configured_repeat_pairs_statements_without_recursive_result_levels(client):
    value=policy(client,'SAFETY','deduction','all');value['repeat']='upgrade'
    assert save(client,value,'SAFETY','deduction','all').status_code==200
    employee=target();login(client,'GSMTEST01')
    first=deduct(client,employee);assert first.status_code==200,first.text
    assert deduct(client,employee).status_code==409
    assert deduct(client,employee,repeat=True).status_code==409
    second=deduct(client,employee,level='MEMO',repeat=True);assert second.status_code==200,second.text
    with SessionLocal() as db:
        assert [float(row.points) for row in db.query(DeductionRecord).filter_by(employee_id=employee).order_by(DeductionRecord.id)]==[1,3]


def test_concurrent_multiple_count_block_cannot_exceed_quota(client):
    value=policy(client,'MSP');value.update(count=2,overflow='block')
    assert save(client,value).status_code==200
    employee=target();login(client,'GSMTEST01');cookies=dict(client.cookies)
    def submit(_):
        with TestClient(app) as c:
            c.cookies.update(cookies)
            return recognize(c,employee).status_code
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sorted(pool.map(submit,range(3)))==[200,200,409]


def test_policy_dates_must_be_canonical_and_cannot_corrupt_version_order(client):
    value=policy(client,'MSP');before=config(client)['revision']
    assert save(client,value,day=DAY.replace('-','')).status_code==422
    assert config(client)['revision']==before


def test_category_monthly_caps_are_independent(client):
    for code in ['SAFETY','COURTESY']:
        value=policy(client,code);value.update(mode='fixed',score=3,cap=5)
        assert save(client,value,code).status_code==200
    employee=target();login(client,'GSMTEST01')
    assert credit(recognize(client,employee,'SAFETY'))==(3,3)
    assert credit(recognize(client,employee,'SAFETY'))==(3,2)
    assert credit(recognize(client,employee,'COURTESY'))==(3,3)


def test_smallest_configurable_cap_uses_decimal_boundary(client):
    value=policy(client,'MSP');value.update(score=1,cap=.01)
    assert save(client,value).status_code==200
    employee=target();login(client,'GSMTEST01')
    assert credit(recognize(client,employee))==(1,.01)
    assert credit(recognize(client,employee))==(1,0)


def test_configuration_does_not_add_a_99_point_ceiling_to_legacy_role_scores(client):
    cfg=config(client)
    response=client.post('/api/hr/scoring-config',json=dict(revision=cfg['revision'],kind='role',scope='frontline',code='GSM',effective_date=DAY,policy={'score':100}))
    assert response.status_code==200,response.text
    employee=target();login(client,'GSMTEST01')
    assert credit(recognize(client,employee,'OTHER'))==(100,100)


def self_target(c):
    from app.v2_models import GroupMembership
    employee=target()
    with SessionLocal() as db:
        source=db.query(UserAccount).filter_by(login_account='CMTEST01').one()
        membership=db.query(GroupMembership).filter_by(employee_id=source.employee_id,status='active').first()
        assert membership
        db.add(GroupMembership(employee_id=employee,group_id=membership.group_id,starts_on='2020-01-01',status='active'))
        account=db.query(UserAccount).filter_by(employee_id=employee).one().login_account
        db.commit()
    login(c,account)
    return employee


def png():
    buf=BytesIO();Image.new('RGB',(8,8),'white').save(buf,format='PNG');return buf.getvalue()


def batch(c,code='MSP',extra=None):
    options=c.get('/api/options').json()
    with SessionLocal() as db:
        typ=db.query(RecognitionType).filter_by(code=code).one().id
    item=dict(recognition_date=DAY,occurred_attraction_id=options['recognition_venues'][0]['id'],recognition_type_id=typ,
              recognizer_employee_id='special:'+code,content='批量规则测试')
    if extra:item.update(extra)
    items = [item, item.copy()]
    key = uuid4().hex
    for _ in range(len(items) + 1):
        response = c.post('/api/recognitions/batch',data={'entries':json.dumps(items),'idempotency_key':key},
                          files={'image':('evidence.png',png(),'image/png')})
        detail = response.json().get('detail')
        if not isinstance(detail, dict) or detail.get('code') != 'SAME_DAY_RECOGNITION_DUPLICATE':
            return response
        items[detail['entry_index'] - 1].update(same_day_duplicate_confirmed=True,
                                               same_day_duplicate_token=detail['same_day_duplicate_token'])
    pytest.fail('Unexpected repeated batch duplicate confirmation')


def review_as_leader(c,record_id,action='confirm'):
    with SessionLocal() as db:
        record=db.get(RecognitionRecord,record_id)
        account=db.query(UserAccount).filter_by(employee_id=record.assigned_reviewer_id).one().login_account
    login(c,account)
    return c.post(f'/api/reviews/{record_id}',json={'action':action,'note':'测试复核'})


def test_batch_count_block_is_atomic_and_cannot_inject_scores(client):
    value=policy(client,'MSP');value.update(score=2,count=1,overflow='block')
    assert save(client,value).status_code==200
    employee=self_target(client)
    assert batch(client).status_code==409
    with SessionLocal() as db:
        assert db.query(RecognitionRecord).filter_by(employee_id=employee).count()==0
    assert batch(client,extra={'fraction':999}).status_code==400


@pytest.mark.parametrize('rule,expected',[
    ({'score':3,'count':1,'overflow':'zero','cap':None},[3,0]),
    ({'score':3,'count':None,'overflow':'zero','cap':5},[3,2]),
])
def test_batch_and_review_share_count_and_cap_then_restore(client,rule,expected):
    value=policy(client,'MSP');value.update(rule)
    assert save(client,value).status_code==200
    self_target(client);response=batch(client)
    assert response.status_code==200,response.text
    ids=[r['id'] for r in response.json()['records']]
    assert review_as_leader(client,ids[1]).status_code==200
    assert review_as_leader(client,ids[0]).status_code==200
    with SessionLocal() as db:
        assert [float(db.get(RecognitionRecord,i).credited_fraction) for i in reversed(ids)]==expected
    assert review_as_leader(client,ids[1],'restore').status_code==200
    assert review_as_leader(client,ids[1]).status_code==200
    with SessionLocal() as db:
        assert sum(db.get(RecognitionRecord,i).credited_fraction for i in ids)<=sum(expected)


def test_rejected_count_block_restore_cannot_exceed_reserved_slots(client):
    value=policy(client,'MSP');value.update(count=2,overflow='block')
    assert save(client,value).status_code==200
    employee=self_target(client);response=batch(client);assert response.status_code==200,response.text
    ids=[r['id'] for r in response.json()['records']]
    assert review_as_leader(client,ids[0],'reject').status_code==200
    login(client,'GSMTEST01');assert recognize(client,employee).status_code==200
    assert review_as_leader(client,ids[0],'restore').status_code==409
    with SessionLocal() as db:assert db.get(RecognitionRecord,ids[0]).status=='rejected'


def material_draft(c,employee,code='SAFETY'):
    with SessionLocal() as db:
        kind=db.query(DeductionType).filter_by(code=code).one().id
        grade=db.query(DeductionLevel).filter_by(code='STATEMENT').one().id
    result=c.post('/api/deductions',data={'employee_id':employee,'deduction_type_id':kind,'deduction_level_id':grade,
        'occurred_on':DAY,'description':'待补材料测试','idempotency_key':uuid4().hex})
    assert result.status_code==200,result.text
    return result.json()['record']['id']


def test_material_completion_keeps_fixed_snapshot_after_disable_and_no_double_score(client,monkeypatch):
    from app import deduction_materials as dm
    monkeypatch.setattr(dm,'worker_tick',lambda:False)
    value=policy(client,'SAFETY','deduction','all');value.update(mode='fixed',score=2.5,direct=False,repeat='sum')
    assert save(client,value,'SAFETY','deduction','all').status_code==200
    employee=target();login(client,'GSMTEST01');record_id=material_draft(client,employee)
    value.update(score=9,active=False);assert save(client,value,'SAFETY','deduction','all').status_code==200
    login(client,'CMTEST01')
    assert client.post(f'/api/deductions/{record_id}/material',files={'document':('proof.pdf',pdf(),'application/pdf')}).status_code==403
    login(client,'SUPTEST01')
    result=client.post(f'/api/deductions/{record_id}/material',files={'document':('proof.pdf',pdf(),'application/pdf')})
    assert result.status_code==200,result.text
    assert dm.process_next_deduction_material_job()
    assert not dm.process_next_deduction_material_job()
    with SessionLocal() as db:
        row=db.get(DeductionRecord,record_id)
        assert row.status=='active' and row.points==Decimal('2.5')
        assert db.query(DeductionRecord).filter_by(employee_id=employee).count()==1


def test_material_supplement_and_worker_cannot_bypass_frozen_upgrade(client,monkeypatch):
    from app import deduction_materials as dm
    monkeypatch.setattr(dm,'worker_tick',lambda:False)
    value=policy(client,'SAFETY','deduction','all');value['repeat']='upgrade'
    assert save(client,value,'SAFETY','deduction','all').status_code==200
    employee=target();login(client,'GSMTEST01');record_id=material_draft(client,employee)
    assert deduct(client,employee).status_code==409 # pending draft prevents duplicate
    # Upload while no active history exists; another active event arrives before conversion.
    result=client.post(f'/api/deductions/{record_id}/material',files={'document':('proof.pdf',pdf(),'application/pdf')})
    assert result.status_code==200,result.text
    value['repeat']='sum';assert save(client,value,'SAFETY','deduction','all').status_code==200
    login(client,'GSMTEST01');assert deduct(client,employee).status_code==200
    assert dm.process_next_deduction_material_job()
    with SessionLocal() as db:
        row=db.get(DeductionRecord,record_id)
        assert row.status=='material_failed'
    assert client.post(f'/api/deductions/{record_id}/material',files={'document':('proof.pdf',pdf(),'application/pdf')}).status_code==409


@pytest.mark.parametrize('legacy',[False,True])
def test_level_correction_uses_frozen_or_legacy_levels_never_later_policy(client,legacy):
    employee=target();login(client,'GSMTEST01');result=deduct(client,employee)
    assert result.status_code==200,result.text
    record_id=result.json()['record']['id']
    with SessionLocal() as db:
        row=db.get(DeductionRecord,record_id)
        if legacy:row.scoring_policy_json=None
        original=row.deduction_level_id
        memo=db.query(DeductionLevel).filter_by(code='MEMO').one().id
        db.commit()
    value=policy(client,'SAFETY','deduction','all');value.update(mode='fixed',score=8,direct=False,repeat='sum')
    assert save(client,value,'SAFETY','deduction','all').status_code==200
    result=client.patch(f'/api/deductions/{record_id}/level',json={'level_id':memo,'expected_level_id':original,'reason':'更正测试'})
    assert result.status_code==200,result.text
    with SessionLocal() as db:assert db.get(DeductionRecord,record_id).points==3


def test_pre_cap_configured_credit_agrees_with_aggregate_and_restart(client):
    from sqlalchemy import text
    from app.score_queries import employee_month_scores
    historical='2026-08-12'
    value=policy(client,'MSP');value.update(score=3,count=1,overflow='zero')
    assert save(client,value,day=historical).status_code==200
    employee=target();login(client,'GSMTEST01')
    assert credit(recognize(client,employee,day=historical))==(3,3)
    second=recognize(client,employee,day=historical);assert credit(second)==(3,0)
    with SessionLocal() as db:
        assert employee_month_scores(db,'2026-08',[employee])[0]['recognition_score']==3
        assert db.execute(text('SELECT recognition_score FROM v_employee_month_scores WHERE employee_id=:e AND score_month=:m'),{'e':employee,'m':'2026-08'}).scalar()==3
    with SessionLocal() as db:
        from app.database.migrations.schema import ensure_recognition_credit_cap_and_poc_columns
        ensure_recognition_credit_cap_and_poc_columns(db)
    init_db()
    assert credit(second)==(3,0)
    with SessionLocal() as db:
        account=db.query(UserAccount).filter_by(employee_id=employee).one().login_account
    login(client,account)
    dashboard=client.get('/api/dashboard',params={'month':'2026-08'})
    assert dashboard.status_code==200,dashboard.text
    assert dashboard.json()['category_scores']['MSP']==3


@pytest.mark.parametrize('date_value',[DAY.replace('-',''),date.today().strftime('%G-W%V-%u')])
def test_noncanonical_dates_cannot_bypass_policy_versions_or_monthly_limits(client,date_value):
    value=policy(client,'MSP');value.update(count=1,overflow='block')
    assert save(client,value).status_code==200
    employee=target();login(client,'GSMTEST01');options=client.get('/api/options').json()
    form=dict(employee_id=employee,recognition_date=date_value,occurred_attraction_id=options['recognition_venues'][0]['id'],
        recognition_type_id=next(r['id'] for r in options['recognition_types'] if r['code']=='MSP'),
        recognizer_employee_id='special:MSP',content='月份绕过测试')
    assert client.post('/api/recognitions',data=form).status_code==400
    assert client.get('/api/scoring/deduction-options',params={'on_date':date_value}).status_code==422
    self_target(client)
    assert batch(client,extra={'recognition_date':date_value}).status_code==400


def test_legacy_material_uses_original_repeat_rules_even_after_config_changes(client):
    from fastapi import HTTPException
    employee=target();login(client,'GSMTEST01')
    first=deduct(client,employee,'ATT_EARLY_CLOCK');assert first.status_code==200,first.text
    record_id=material_draft(client,employee,'ATT_LATE_CLOCK')
    value=policy(client,'ATT_LATE_CLOCK','deduction','all');value['repeat']='sum'
    assert save(client,value,'ATT_LATE_CLOCK','deduction','all').status_code==200
    with SessionLocal() as db:
        row=db.get(DeductionRecord,record_id);row.scoring_policy_json=None;db.flush()
        with pytest.raises(HTTPException,match='409'):P.validate_material_repeat(db,row)


@pytest.mark.parametrize('legacy',[False,True])
def test_upgrade_review_never_uses_later_fixed_policy_or_level_score(client,legacy):
    from app.v2_models import DeductionUpgradeRequest
    code='ATT_EARLY_CLOCK'
    employee=self_target(client);login(client,'TATEST01')
    first=deduct(client,employee,code);assert first.status_code==200,first.text
    with SessionLocal() as db:
        kind=db.query(DeductionType).filter_by(code=code).one().id
        reviewer=db.query(UserAccount).filter_by(login_account='GSMTEST01').one().employee_id
        memo=db.query(DeductionLevel).filter_by(code='MEMO').one().id
    submitted=client.post('/api/deduction-upgrades',data={'employee_id':employee,'deduction_type_id':kind,
        'occurred_on':DAY,'description':'冻结升级规则','reviewer_id':reviewer},files={'document':('proof.pdf',pdf(),'application/pdf')})
    assert submitted.status_code==200,submitted.text
    request_id=submitted.json()['request']['id']
    with SessionLocal() as db:
        req=db.get(DeductionUpgradeRequest,request_id)
        if legacy:db.get(DeductionRecord,req.second_deduction_id).scoring_policy_json=None
        db.commit()
    value=policy(client,code,'deduction','all');value.update(mode='fixed',score=9,repeat='sum',direct=False)
    assert save(client,value,code,'deduction','all').status_code==200
    cfg=config(client)
    assert client.post('/api/hr/scoring-config',json=dict(revision=cfg['revision'],kind='level',scope='all',code='MEMO',effective_date=DAY,policy={'score':7})).status_code==200
    login(client,'GSMTEST01')
    reviewed=client.post(f'/api/deduction-upgrades/{request_id}/resolve',json={'decision':'approve','result_level_id':memo,'handling_note':'已开具文书','issued_confirmed':True})
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['request']['result_points']==3
    assert reviewed.json()['request']['second_record']['actual_points']==0


def test_sick_leave_linked_fixed_deduction_and_direct_role_gate(client):
    value=policy(client,'SICK_LEAVE_VIOLATION','deduction','all');value.update(mode='fixed',score=2.25,repeat='sum',direct=False)
    assert save(client,value,'SICK_LEAVE_VIOLATION','deduction','all').status_code==200
    employee=self_target(client)
    form=dict(employee_id=employee,leave_start_date=DAY,leave_end_date=DAY,is_violation='true',idempotency_key=uuid4().hex)
    login(client,'TATEST01')
    assert client.post('/api/sick-leaves',data=form,files={'proof':('proof.png',png(),'image/png')}).status_code==403
    login(client,'GSMTEST01')
    assert client.post('/api/sick-leaves',data=form,files={'proof':('proof.png',png(),'image/png')}).status_code==403
    login(client,'HR01')
    assert client.post('/api/sick-leaves',data=form,files={'proof':('proof.png',png(),'image/png')}).status_code==403
    # This legacy entry has no current default role grants. Exercise its scoring
    # code with a test-only explicit capability, without changing production roles.
    from app.v2_auth import current_user, V2User
    with SessionLocal() as db:
        account=db.query(UserAccount).filter_by(login_account='GSMTEST01').one()
        role=db.query(Role).filter_by(code='GSM').one()
        operator=V2User(employee=account.employee,account=account,role=role,permissions={'SICK_REGISTER','DEDUCTION_ALL'})
    app.dependency_overrides[current_user]=lambda:operator
    try:
        result=client.post('/api/sick-leaves',data=form,files={'proof':('proof.png',png(),'image/png'),'violation_document':('statement.pdf',pdf(),'application/pdf')})
        assert result.status_code==200,result.text
    finally:
        app.dependency_overrides.pop(current_user,None)
    with SessionLocal() as db:
        row=db.query(DeductionRecord).filter_by(employee_id=employee).one()
        assert row.points==Decimal('2.25') and row.status=='active'


def test_concurrent_pdf_deductions_cannot_bypass_repeat_upgrade(client):
    value=policy(client,'SAFETY','deduction','all');value['repeat']='upgrade'
    assert save(client,value,'SAFETY','deduction','all').status_code==200
    employee=target();login(client,'GSMTEST01');cookies=dict(client.cookies)
    def submit(_):
        with TestClient(app) as c:
            c.cookies.update(cookies)
            return deduct(c,employee).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit,range(2)))==[200,409]
    with SessionLocal() as db:
        assert db.query(DeductionRecord).filter_by(employee_id=employee,status='active').count()==1
