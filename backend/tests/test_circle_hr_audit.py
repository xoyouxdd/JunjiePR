"""Real role/circle boundaries for all three HR accounts; no production data."""
import os
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

os.environ['RECOGNITION_V2_DATA_DIR']=str(Path(tempfile.mkdtemp(prefix='fz-hr-audit-')))
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='audit-fixture-only'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='audit-admin-only'

from app.main import app
from app.v2_database import SessionLocal
from app.v2_models import Employee, UserAccount, Role, EmployeeRoleAssignment, EmployeeActingDuty, AuditLog
from app.v2_crypto import hash_password
from app.services.audit import write_audit

HR_NUMBERS=['HR-HEAT','HR-DWARF','HR-BEAR']
ROWS={}


def login(client, no):
    r=client.post('/api/login',json={'employee_no':no,'password':'audit-admin-only' if no=='HR01' else 'audit-fixture-only'})
    assert r.status_code==200,r.text


@pytest.fixture(scope='module',autouse=True)
def seed():
    with TestClient(app):
        with SessionLocal() as db:
            for circle_index,hr_no in enumerate(HR_NUMBERS):
                hr=db.query(Employee).filter_by(employee_no=hr_no).one()
                account=db.query(UserAccount).filter_by(employee_id=hr.id).one()
                account.enabled=True;account.must_change_password=False;account.password_hash=hash_password('audit-fixture-only')
                people={}
                for i,code in enumerate(['CM','TR','SUPERVISOR','TA_SUPERVISOR','GSM','TA_GSM','AM','OM']):
                    e=Employee(employee_no=f'96{circle_index}{i}001',name=f'合成{circle_index}{code}',attraction_id=hr.attraction_id,is_active=True,hired_on='2020-01-01')
                    db.add(e);db.flush();people[code]=e
                    role=db.query(Role).filter_by(code=code).one()
                    db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=role.id,starts_on='2020-01-01',status='active'))
                db.flush()
                for code,e in people.items():
                    write_audit(db,e,'范围测试'+code,'employee',e.id,after={'secret':'must-not-return'},reason='范围合成验证')
                write_audit(db,hr,'范围测试HR','employee',people['CM'].id)
                write_audit(db,people['SUPERVISOR'],'低层操作涉及经理','employee',people['GSM'].id)
                write_audit(db,people['SUPERVISOR'],'未知范围不推断','unknown',None)
                db.add(AuditLog(operator_id=None,operator_name='合成系统',action='无操作人不推断',entity_type='employee',entity_id=str(people['CM'].id),attraction_id=hr.attraction_id))
                ROWS[hr_no]={'circle':hr.attraction_id,'people':{c:e.id for c,e in people.items()}}
            # A supervisor with an overlaid TA GSM duty must be excluded.
            pid=ROWS['HR-HEAT']['people']['SUPERVISOR'];e=db.get(Employee,pid)
            extra=Employee(employee_no='9699001',name='合成代理经理',attraction_id=e.attraction_id,is_active=True,hired_on='2020-01-01')
            db.add(extra);db.flush()
            db.add(EmployeeRoleAssignment(employee_id=extra.id,role_id=db.query(Role).filter_by(code='SUPERVISOR').one().id,starts_on='2020-01-01',status='active'))
            db.add(EmployeeActingDuty(employee_id=extra.id,role_id=db.query(Role).filter_by(code='TA_GSM').one().id,starts_on='2020-01-01',status='active'))
            db.flush();write_audit(db,extra,'主管代理经理操作','employee',extra.id)
            # Cross-circle actor on a local object must not sneak through its object scope.
            foreign=db.get(Employee,ROWS['HR-DWARF']['people']['SUPERVISOR'])
            write_audit(db,foreign,'外圈人员操作本圈对象','employee',ROWS['HR-HEAT']['people']['CM'])
            # Demoted manager: current title alone is insufficient for historical visibility.
            prior=Employee(employee_no='9699002',name='合成历史经理',attraction_id=e.attraction_id,is_active=True,hired_on='2020-01-01')
            db.add(prior);db.flush()
            yesterday=(datetime.now()-timedelta(days=1)).date().isoformat()
            db.add(EmployeeRoleAssignment(employee_id=prior.id,role_id=db.query(Role).filter_by(code='GSM').one().id,starts_on='2020-01-01',ends_on=yesterday,status='ended'))
            db.add(EmployeeRoleAssignment(employee_id=prior.id,role_id=db.query(Role).filter_by(code='SUPERVISOR').one().id,starts_on=datetime.now().date().isoformat(),status='active'))
            db.add(AuditLog(operator_id=prior.id,operator_name=prior.name,action='历史经理任内操作',entity_type='employee',entity_id=str(prior.id),attraction_id=e.attraction_id,created_at=datetime.now()-timedelta(days=2)))
            db.commit()
        yield


@pytest.mark.parametrize('hr_no',HR_NUMBERS)
def test_each_circle_hr_only_sees_local_frontline_and_lead(hr_no):
    with TestClient(app) as c:
        login(c,hr_no)
        result=c.get('/api/admin/logs');assert result.status_code==200,result.text
        rows=result.json()
        for code in ['CM','TR','SUPERVISOR','TA_SUPERVISOR']:
            assert any(r['action']=='范围测试'+code for r in rows)
        assert all(r['action'] in {'范围测试'+code for code in ['CM','TR','SUPERVISOR','TA_SUPERVISOR']} for r in rows)
        expected={ROWS[hr_no]['people'][code] for code in ['CM','TR','SUPERVISOR','TA_SUPERVISOR']}
        assert all(int(r['entity'].split(':')[1]) in expected for r in rows)
        assert all(set(r)=={'id','time','operator','action','entity','reason'} for r in rows)
        assert 'must-not-return' not in result.text
        # Extra query parameters cannot expand the server's circle or role boundary.
        assert c.get('/api/admin/logs?attraction_id=all&role=GSM&limit=1000').json()==rows


def test_filtered_limit_is_applied_after_excluding_senior_targets():
    with SessionLocal() as db:
        data=ROWS['HR-HEAT'];pid=data['people']['SUPERVISOR']
        for i in range(260):
            db.add(AuditLog(operator_id=pid,operator_name='合成主管',action='批量涉及经理'+str(i),
                entity_type='employee',entity_id=str(data['people']['GSM']),attraction_id=data['circle']))
        db.commit()
    with TestClient(app) as c:
        login(c,'HR-HEAT')
        r=c.get('/api/admin/logs?limit=1');assert r.status_code==200
        assert len(r.json())==1 and r.json()[0]['action'].startswith('范围测试')


def test_system_admin_keeps_full_senior_and_cross_circle_access():
    with TestClient(app) as c:
        login(c,'HR01')
        rows=c.get('/api/admin/logs?limit=1000').json()
        assert {'范围测试GSM','范围测试TA_GSM','范围测试AM','范围测试OM','主管代理经理操作','历史经理任内操作'} <= {r['action'] for r in rows}


def test_frontline_has_no_audit_endpoint_access():
    with TestClient(app) as c:
        login(c,'CMTEST01')
        assert c.get('/api/admin/logs').status_code==403


def test_import_summary_keys_preserve_identity_batch_and_payload_privacy():
    import json
    from app.services.audit import audit_summary
    def row(ident=1, operator=7, payload=None, **extra):
        return AuditLog(id=ident,operator_id=operator,operator_name='同名操作人',action='Excel更新员工',
            entity_type='employee',entity_id=str(ident),created_at=datetime(2026,10,28,15,13,35),
            after_json=payload,**extra)
    first=audit_summary(row(payload=json.dumps({'_import_batch_id':'a'*32,'secret':'private'})))
    same=audit_summary(row(2,payload=json.dumps({'_import_batch_id':'a'*32})))
    other=audit_summary(row(3,payload=json.dumps({'_import_batch_id':'b'*32})))
    assert first['batch_key']==same['batch_key']!=other['batch_key']
    assert first['batch_exact'] and 'private' not in json.dumps(first)
    legacy=audit_summary(row(payload='{broken'))
    assert not legacy['batch_exact']
    assert legacy['batch_key']==audit_summary(row(2))['batch_key']
    assert legacy['batch_key']!=audit_summary(row(3,operator=8))['batch_key']
    assert 'batch_key' not in audit_summary(row(operator=None))


def test_batch_metadata_is_added_only_after_circle_visibility_filter():
    import json
    data=ROWS['HR-HEAT']
    with SessionLocal() as db:
        for target in ['CM','GSM']:
            db.add(AuditLog(operator_id=data['people']['SUPERVISOR'],operator_name='合成主管',
                action='Excel更新员工',entity_type='employee',entity_id=str(data['people'][target]),
                attraction_id=data['circle'],after_json=json.dumps({'_import_batch_id':'c'*32,'secret':target})))
        db.commit()
    with TestClient(app) as c:
        login(c,'HR-HEAT')
        rows=[r for r in c.get('/api/admin/logs').json() if r['action']=='Excel更新员工']
        assert len(rows)==1 and rows[0]['entity']==f"employee:{data['people']['CM']}"
        assert rows[0]['batch_exact'] and 'after_json' not in rows[0] and 'secret' not in rows[0]
