"""Type markers, internship expiry, V3 import and scope use isolated fictional data."""
import os
import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

os.environ['RECOGNITION_V2_DATA_DIR']=str(Path(tempfile.mkdtemp(prefix='fz-employee-types-')))
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='type-fixture-only'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='type-admin-only'

from app.main import app
from app.v2_database import SessionLocal
from app.v2_models import Employee, UserAccount, AuditLog, UserSession, EmployeeRoleAssignment, Attraction
from app.services import employee_types as T
from app.v2_crypto import verify_password, hash_password
from app.services.employee_import import HEADERS


@pytest.fixture(scope='module',autouse=True)
def setup():
    with TestClient(app):
        with SessionLocal() as db:
            for no in ['HR-HEAT','HR-DWARF','HR-BEAR']:
                e=db.query(Employee).filter_by(employee_no=no).one();a=db.query(UserAccount).filter_by(employee_id=e.id).one()
                a.enabled=True;a.must_change_password=False;a.password_hash=hash_password('type-fixture-only')
            db.commit()
        yield


def login(c,no='HR01',password=None):
    r=c.post('/api/login',json={'employee_no':no,'password':password or ('type-admin-only' if no=='HR01' else 'type-fixture-only')})
    assert r.status_code==200,r.text


def circle(c):
    return next(x['id'] for x in c.get('/api/options').json()['employee_circles'] if x['name']=='热力追踪')


def create(c,no,**extra):
    return c.post('/api/hr/employees',json={'employee_no':no,'name':'合成Type员工'+no,'role_code':'CM','attraction_id':circle(c),**extra})


def book(rows,headers=None):
    wb=Workbook();ws=wb.active;ws.append(headers or HEADERS)
    for row in rows:ws.append(row)
    out=BytesIO();wb.save(out)
    return {'workbook':('type-fixture.xlsx',out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}


def test_type_is_optional_and_independent_of_role_and_group():
    with TestClient(app) as c:
        login(c)
        for i,kind in enumerate(['', 'Comp28', 'Comp40', ' intern ']):
            r=create(c,'982300'+str(i),employee_type=kind)
            assert r.status_code==200,r.text
            e=r.json()['employee'];assert e['base_role_code']=='CM' and e['group_id'] is None
            assert e['employee_type']==('Intern' if i==3 else kind)
            assert e['internship_ends_on']=='' and e['account_enabled']


def test_invalid_type_or_non_intern_date_never_creates_employee():
    with TestClient(app) as c:
        login(c)
        for payload in [{'employee_type':'Comp28','internship_ends_on':'2026-12-18'},
                        {'internship_ends_on':'2026-12-18'}, {'employee_type':'Intern','internship_ends_on':'2026-02-30'},
                        {'employee_type':'x'*81}, {'employee_type':123}]:
            assert create(c,'9823010',**payload).status_code==400
        with SessionLocal() as db: assert not db.query(Employee).filter_by(employee_no='9823010').first()


def test_due_next_day_revokes_existing_session_and_never_resets_disabled_time(monkeypatch):
    today=T.business_today()
    with TestClient(app) as admin,TestClient(app) as employee:
        login(admin)
        r=create(admin,'9823020',employee_type='Intern',internship_ends_on=today.isoformat())
        assert r.status_code==200 and r.json()['employee']['account_enabled']
        pid=r.json()['employee']['id']
        with SessionLocal() as db:
            a=db.query(UserAccount).filter_by(employee_id=pid).one();a.must_change_password=False;db.commit()
        login(employee,'9823020','3020')
        assert employee.get('/api/me').status_code==200
        monkeypatch.setattr(T,'business_today',lambda:today+timedelta(days=1))
        assert employee.get('/api/me').status_code==401
        with SessionLocal() as db:
            a=db.query(UserAccount).filter_by(employee_id=pid).one();stamp=a.disabled_at
            assert not a.enabled and stamp
            assert db.query(UserSession).filter_by(account_id=a.id).count()==0
            assert T.expire_accounts(db,pid)==0
            assert a.disabled_at==stamp
            assert db.query(AuditLog).filter_by(entity_id=str(pid),action='实习到期自动停用账号').count()==1
            assert db.get(Employee,pid).is_active
        assert employee.post('/api/login',json={'employee_no':'9823020','password':'3020'}).status_code==401
        # Clear or extend expiry alone never re-enables an already disabled login.
        assert admin.put(f'/api/hr/employees/{pid}',json={'account_enabled':True}).status_code==400
        assert admin.put(f'/api/hr/employees/{pid}',json={'internship_ends_on':''}).status_code==200
        item=next(e for e in admin.get('/api/hr/employees').json() if e['id']==pid)
        assert not item['account_enabled'] and item['internship_ends_on']==''
        assert admin.put(f'/api/hr/employees/{pid}',json={'account_enabled':True}).status_code==200
        with SessionLocal() as db:assert db.query(UserAccount).filter_by(employee_id=pid).one().disabled_at is None


def test_expired_create_disable_extension_and_formal_conversion_keep_wait():
    with TestClient(app) as c:
        login(c)
        past=(T.business_today()-timedelta(days=1)).isoformat()
        r=create(c,'9823030',employee_type='Intern',internship_ends_on=past)
        assert r.status_code==200,r.text
        p=r.json()['employee'];assert not p['account_enabled'] and p['employment_status']=='active'
        pid=p['id']
        with SessionLocal() as db:stamp=db.query(UserAccount).filter_by(employee_id=pid).one().disabled_at
        assert c.put(f'/api/hr/employees/{pid}',json={'internship_ends_on':(T.business_today()+timedelta(days=90)).isoformat()}).status_code==200
        assert c.put(f'/api/hr/employees/{pid}',json={'employee_type':'Comp40'}).status_code==200
        with SessionLocal() as db:
            e=db.get(Employee,pid);a=db.query(UserAccount).filter_by(employee_id=pid).one()
            assert e.employee_type=='Comp40' and not e.internship_ends_on and a.disabled_at==stamp and not a.enabled
        assert c.request('DELETE',f'/api/hr/employees/{pid}/account',json={}).status_code==400
        with SessionLocal() as db:
            a=db.query(UserAccount).filter_by(employee_id=pid).one();a.disabled_at=datetime.now()-timedelta(hours=72,seconds=1);db.commit()
        assert c.request('DELETE',f'/api/hr/employees/{pid}/account',json={}).status_code==200


def test_preflight_skip_existing_defaults_lead_alias_and_expired_import():
    with TestClient(app) as c:
        login(c)
        old=create(c,'9823040',employee_type='Comp28').json()['employee']
        rows=[['9823040','不应覆盖','CM','热力追踪','Intern','2026-12-18'],
              ['9823041','合成Lead','lead','热力追踪','Comp40',''],
              ['9823042','合成到期实习','tr','热力追踪',' INTERN ',(T.business_today()-timedelta(days=1)).isoformat()]]
        files=book(rows)
        preview=c.post('/api/hr/import-employees',files=files,data={'preview':'true'})
        assert preview.status_code==200,preview.text
        p=preview.json();assert p['would_create']==2 and len(p['duplicates'])==1 and len(p['expired'])==1
        with SessionLocal() as db:assert not db.query(Employee).filter_by(employee_no='9823041').first()
        assert c.post('/api/hr/import-employees',files=files).status_code==409
        result=c.post('/api/hr/import-employees',files=files,data={'expected_digest':p['digest'],'skip_existing':'true'})
        assert result.status_code==200,result.text
        assert result.json()['created']==2 and result.json()['skipped']==1
        with SessionLocal() as db:
            assert db.get(Employee,old['id']).name==old['name'] and db.get(Employee,old['id']).employee_type=='Comp28'
            lead=db.query(Employee).filter_by(employee_no='9823041').one()
            role=db.query(EmployeeRoleAssignment).filter_by(employee_id=lead.id).one()
            assert role.role.code=='SUPERVISOR' and role.ends_on is None
            assert role.starts_on==T.business_today().isoformat()
            a=db.query(UserAccount).filter_by(employee_id=lead.id).one()
            assert a.enabled and a.must_change_password and verify_password('3041',a.password_hash)
            intern=db.query(Employee).filter_by(employee_no='9823042').one()
            assert intern.is_active and not db.query(UserAccount).filter_by(employee_id=intern.id).one().enabled


def test_import_is_atomic_rejects_old_template_ta_and_internal_duplicates():
    with TestClient(app) as c:
        login(c)
        good=['9823050','有效合成员工','CM','热力追踪','','']
        for bad in [good, ['9823051','错误Type','TR','热力追踪','Comp28','2026-12-18'],
                    ['9823051','错误代理','TA_SUPERVISOR','热力追踪','','']]:
            r=c.post('/api/hr/import-employees',files=book([good,bad]))
            assert r.status_code==400 and '第3行' in r.text,r.text
            with SessionLocal() as db:assert not db.query(Employee).filter_by(employee_no='9823050').first()
        r=c.post('/api/hr/import-employees',files=book([],HEADERS+['初始密码']))
        assert r.status_code==400 and 'V3' in r.text


def test_changed_preview_and_partial_write_failure_never_import_half_a_batch(monkeypatch):
    with TestClient(app) as c:
        login(c)
        files=book([['9823070','合成事务一','CM','热力追踪','',''],['9823071','合成事务二','TR','热力追踪','','']])
        preview=c.post('/api/hr/import-employees',files=files,data={'preview':'true'}).json()
        assert c.post('/api/hr/import-employees',files=files,data={'expected_digest':'0'*64}).status_code==409
        from app.routers import employees as E
        original=E.hash_password
        def failing(password):
            if password=='3071':raise RuntimeError('synthetic import write failure')
            return original(password)
        with monkeypatch.context() as patch:
            patch.setattr(E,'hash_password',failing)
            with pytest.raises(RuntimeError,match='synthetic import write failure'):
                c.post('/api/hr/import-employees',files=files,data={'expected_digest':preview['digest']})
        with SessionLocal() as db:
            assert not db.query(Employee).filter(Employee.employee_no.in_(['9823070','9823071'])).count()
            assert not db.query(UserAccount).filter(UserAccount.login_account.in_(['9823070','9823071'])).count()


def test_non_string_type_edit_is_rejected_without_corrupting_existing_profile():
    with TestClient(app) as c:
        login(c)
        e=create(c,'9823080',employee_type='Comp28').json()['employee']
        assert c.put('/api/hr/employees/'+str(e['id']),json={'employee_type':123}).status_code==400
        with SessionLocal() as db:assert db.get(Employee,e['id']).employee_type=='Comp28'


def test_replaced_employee_list_requires_new_import_confirmation():
    with TestClient(app) as c:
        login(c)
        rows=[['9823090','合成预检员工','CM','热力追踪','Intern','']];files=book(rows)
        preview=c.post('/api/hr/import-employees',files=files,data={'preview':'true'}).json()
        assert create(c,'9823090',employee_type='Comp40').status_code==200
        r=c.post('/api/hr/import-employees',files=files,data={'expected_digest':preview['digest'],'skip_existing':'true'})
        assert r.status_code==409
        with SessionLocal() as db:assert db.query(Employee).filter_by(employee_no='9823090').one().employee_type=='Comp40'


def test_due_sweep_preserves_manual_disable_and_catches_up_after_offline():
    with TestClient(app) as c:
        login(c)
        e=create(c,'9823091',employee_type='Intern').json()['employee']
        with SessionLocal() as db:
            person=db.get(Employee,e['id']);person.internship_ends_on=(T.business_today()-timedelta(days=5)).isoformat()
            db.flush()
            account=db.query(UserAccount).filter_by(employee_id=e['id']).one()
            # Represents service downtime: no earlier automatic disable occurred.
            before=datetime.now();assert T.expire_accounts(db,e['id'])==1;db.commit()
            assert account.disabled_at>=before and not account.enabled
            actual=account.disabled_at
            assert T.expire_accounts(db,e['id'])==0 and account.disabled_at==actual
            # Explicitly disabled earlier remains earlier, even with an expired date.
            account.disabled_at=before-timedelta(days=2);db.commit()
            assert T.expire_accounts(db,e['id'])==0 and account.disabled_at==before-timedelta(days=2)


@pytest.mark.parametrize('hr_no',['HR-HEAT','HR-DWARF','HR-BEAR'])
def test_circle_hr_scope_applies_to_type_edit_and_import(hr_no):
    with TestClient(app) as c:
        login(c,hr_no)
        own=c.get('/api/me').json()['attraction_id']
        with SessionLocal() as db:
            other=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.id!=own).first()
            no='982306'+str(['HR-HEAT','HR-DWARF','HR-BEAR'].index(hr_no))
            row=[no,'合成跨圈员工','CM',other.name,'Intern','']
        assert c.post('/api/hr/import-employees',files=book([row])).status_code==400
        result=c.post('/api/hr/employees',json={'employee_no':no,'name':'本圈合成实习生','role_code':'TR',
            'attraction_id':own,'employee_type':'intern'})
        assert result.status_code==200,result.text
        pid=result.json()['employee']['id']
        assert c.put(f'/api/hr/employees/{pid}',json={'internship_ends_on':'2099-12-18'}).status_code==200
        with SessionLocal() as db:
            foreign=Employee(employee_no='982316'+str(['HR-HEAT','HR-DWARF','HR-BEAR'].index(hr_no)),
                name='外圈合成身份',attraction_id=other.id,is_active=True,employee_type='Comp28')
            db.add(foreign);db.commit();foreign_id=foreign.id
        assert c.put(f'/api/hr/employees/{foreign_id}',json={'employee_type':'Intern'}).status_code==403
        with SessionLocal() as db:assert db.get(Employee,foreign_id).employee_type=='Comp28'
        assert c.put('/api/hr/employees/99999999',json={'employee_type':'Intern'}).status_code==404


def test_migration_is_idempotent_and_preserves_old_employee():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import Session
    from app.database.migrations.schema import ensure_employee_type_columns
    engine=create_engine('sqlite:///:memory:')
    with Session(engine) as db:
        db.execute(text('CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT)'))
        db.execute(text("INSERT INTO employees VALUES (1,'legacy')"))
        ensure_employee_type_columns(db);ensure_employee_type_columns(db)
        assert db.execute(text('SELECT name,employee_type,internship_ends_on FROM employees')).one()==('legacy',None,None)
