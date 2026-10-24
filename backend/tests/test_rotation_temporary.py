"""Isolated rotation-only account import, takeover, scope and real confirmation tests."""
import os
import tempfile
from pathlib import Path
from datetime import datetime,date,timedelta
from io import BytesIO
from copy import deepcopy
import pytest
from openpyxl import Workbook
from fastapi.testclient import TestClient

os.environ['RECOGNITION_V2_DATA_DIR']=str(Path(tempfile.mkdtemp(prefix='fz-rotation-temp-')))
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='rotation-temp-fixture'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='rotation-temp-admin'
from app.main import app
from app.v2_database import SessionLocal
from app.v2_models import Employee,UserAccount,Attraction,Role,EmployeeRoleAssignment
from app.v2_crypto import hash_password
from app.rotation import service as S
from app.rotation.temporary import TemporaryMember as T,TemporarySession,COOKIE
from app.rotation.namespace import FORMAL_TABLES as F

def ok(r):
    assert r.status_code==200,r.text
    return r.json()

def login(c,no='9730020',password='rotation-temp-fixture'):
    return c.post('/api/login',json={'employee_no':no,'password':password})

@pytest.fixture(scope='module',autouse=True)
def setup():
    with TestClient(app):
        with SessionLocal() as db:
            for no,code,circle in [('9730020','SUPERVISOR','热力追踪'),('9730040','GSM','热力追踪'),
                                   ('9730000','CM','热力追踪'),('9730001','CM','矮人迷宫')]:
                aid=db.query(Attraction).filter_by(name=circle).one().id
                e=Employee(employee_no=no,name='合成员工'+no,attraction_id=aid,is_active=True,hired_on='2026-01-01')
                db.add(e);db.flush()
                role=db.query(Role).filter_by(code=code).one()
                db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=role.id,starts_on='2026-01-01',status='active',assignment_type='permanent'))
                db.add(UserAccount(employee_id=e.id,login_account=no,password_hash=hash_password('rotation-temp-fixture'),enabled=True,must_change_password=False,credential_initialized=True))
            db.commit()
        yield

def book(rows):
    wb=Workbook();wb.active.append(['工号','姓名','日期','班次','备注'])
    for row in rows: wb.active.append(row)
    out=BytesIO();wb.save(out)
    return out.getvalue()

def upload(c,rows,**extra):
    return c.post('/api/rotation-board/roster/upload',files={'file':('synthetic.xlsx',book(rows),'application/octet-stream')},data={'scope':'day',**extra})

def register_temp(c,no='9730100',name='合成临时成员',day=None):
    rows=[[no,name,day or date.today().isoformat(),'00:00-23:59','']]
    batch=ok(upload(c,rows,preview='true'))
    result=ok(upload(c,rows,expected_digest=batch['digest']))
    return result

def test_preflight_and_atomic_creation_reuse_do_not_create_employees():
    with TestClient(app) as manager:
        ok(login(manager))
        rows=[['9730100','合成临时成员',date.today().isoformat(),'00:00-23:59','']]
        p=ok(upload(manager,rows,preview='true'));assert p['new'][0]['employee_no']=='9730100'
        with SessionLocal() as db: assert not db.query(T).filter_by(employee_no='9730100').first()
        assert upload(manager,rows).status_code==409
        r=ok(upload(manager,rows,expected_digest=p['digest']));assert r['temporary_created']==1
        ok(upload(manager,rows))
        with SessionLocal() as db:
            assert db.query(T).filter_by(employee_no='9730100').count()==1
            assert not db.query(Employee).filter_by(employee_no='9730100').first()
            assert not db.query(UserAccount).filter_by(login_account='9730100').first()

def test_scoped_login_and_every_non_rotation_function_is_denied():
    with TestClient(app) as c:
        ok(login(c,'9730100','0100'));me=ok(c.get('/api/me'))
        assert me['rotation_temporary'] and me['permissions']==[] and me['name']=='合成临时成员'
        ok(c.get('/api/rotation-board/me'))
        for method,url in [('GET','/api/options'),('GET','/api/action-center'),('GET','/api/hr/employees'),
            ('GET','/api/rotation-board/board'),('GET','/api/rotation-board/whoami'),
            ('GET','/api/rotation-board/log?date='+date.today().isoformat()),('GET','/api/rotation-board/config'),
            ('POST','/api/recognitions'),('POST','/api/password'),('POST','/api/rotation-board/act')]:
            assert c.request(method,url,json={} if method=='POST' else None).status_code==403,url
        assert c.post('/api/rotation-board/me/act',json={'action':'depart','pid':'9730000'}).status_code==403
        assert c.post('/api/rotation-board/me/act',json={'action':'move'}).status_code==403
        assert c.post('/api/rotation-board/me/act',json={'action':'depart'}).status_code==400
        ok(c.post('/api/logout'));assert c.get('/api/me').status_code==401

def test_cross_circle_is_accepted_and_anomalies_need_explicit_choice():
    with TestClient(app) as c:
        ok(login(c))
        cross=ok(upload(c,[['9730001','合成员工9730001',date.today().isoformat(),'07:15-16:15','']],preview='true'))
        assert cross['identities']['9730001']['cross_circle'] and not cross['warnings']
        conflict=ok(upload(c,[['9730100','错误姓名',date.today().isoformat(),'07:15-16:15','']],preview='true'))
        assert conflict['warnings']
        ok(upload(c,[['9730000','班表别名',date.today().isoformat(),'07:15-16:15','']],preview='true'))
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no='9730000').one();a=db.query(UserAccount).filter_by(employee_id=e.id).one();a.enabled=False;db.commit()
        assert upload(c,[['9730000','合成员工9730000',date.today().isoformat(),'07:15-16:15','']]).status_code==409
        with SessionLocal() as db:
            a=db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730000').one();a.enabled=True;db.commit()

def test_scope_date_only_creates_selected_people_and_stale_preflight_is_rejected():
    with TestClient(app) as c:
        ok(login(c));today=date.today().isoformat();tomorrow=(date.today()+timedelta(days=1)).isoformat()
        rows=[['9730101','合成待选一',today,'07:15-16:15',''],['9730102','合成待选二',tomorrow,'07:15-16:15','']]
        p=ok(upload(c,rows,preview='true',date=today));assert len(p['new'])==1
        changed=deepcopy(rows);changed[0][1]='改动姓名'
        assert upload(c,changed,expected_digest=p['digest'],date=today).status_code==409
        ok(upload(c,rows,expected_digest=p['digest'],date=today))
        with SessionLocal() as db: assert not db.query(T).filter_by(employee_no='9730102').first()

def test_failed_import_rolls_back_temporary_creation(monkeypatch):
    with TestClient(app) as c:
        ok(login(c));rows=[['9730103','合成回滚',date.today().isoformat(),'07:15-16:15','']]
        p=ok(upload(c,rows,preview='true'))
        def fail(*args,**kwargs): raise S.ActionError('模拟保存失败')
        with monkeypatch.context() as m:
            m.setattr(S,'save_roster',fail)
            assert upload(c,rows,expected_digest=p['digest']).status_code==400
        with SessionLocal() as db: assert not db.query(T).filter_by(employee_no='9730103').first()

def seed_live():
    with SessionLocal() as db:
        db.info['rotation_formal']=True;aid=db.query(T).filter_by(employee_no='9730100').one().attraction_id
        S.save_roster(db,aid,{'people':{'9730100':{'name':'合成临时成员'}},
            'days':{date.today().isoformat():{'9730100':'00:00-23:59'}},'hours':{}},scope='day',
            file_name='synthetic-live.xlsx',uploader_id=None,uploader_name='合成测试')
        with S.transaction(db):
            cfg=S.load_config(db,aid);cfg.lines_json=S._dumps([{'id':'A1','posts':[{'name':'入口'},{'name':'出口'}]}])
            cfg.settings_json=S._dumps({'parkClose':'23:59','closeDelay':0})
        S.do_draft_action(db,aid,'fixture',{'action':'draft_generate','date':date.today().isoformat(),'force':True})
        S.do_draft_action(db,aid,'fixture',{'action':'publish','date':date.today().isoformat()})
        now=datetime.now();minute=now.hour*60+now.minute+now.second/60
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row);state['lastTick']=minute
            state['plan'].update(crew={},seven={},push7=[])
            p=state['persons']['9730100'];p.update(state='ready',role='rotation',start=0,end=1440,readyAt=minute-1,readySince=minute-1,
                breakKind='rest',absences=[],visited=['A1'],assign={'line':'A1','mode':'push','departAt':minute-1})
            S.save_day(db,row,state)
        return aid

def test_real_personal_confirmation_board_plain_names_and_backend_markers():
    aid=seed_live()
    with TestClient(app) as c,TestClient(app) as manager:
        ok(login(c,'9730100','0100'));ok(login(manager))
        p=ok(c.get('/api/rotation-board/me'))['person'];assert p['name']=='合成临时成员'
        body={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}
        r=ok(c.post('/api/rotation-board/me/act',json=body));assert r['result']['line']=='A1'
        assert ok(c.post('/api/rotation-board/me/act',json=body))['duplicate']
        board=ok(manager.get('/api/rotation-board/board'));assert next(p for p in board['day']['persons'] if p['pid']=='9730100')['name']=='合成临时成员'
        logs=ok(manager.get('/api/rotation-board/log',params={'date':date.today().isoformat()}))['items']
        assert any(e['employee_no']=='9730100' and e['temporary'] and e['actor_temporary'] for e in logs)

def test_formal_registration_adopts_history_revokes_sessions_and_keeps_historical_marks():
    with TestClient(app) as temp,TestClient(app) as admin:
        ok(login(temp,'9730100','0100'));ok(login(admin,'HR01','rotation-temp-admin'))
        with SessionLocal() as db:
            aid=db.query(T).filter_by(employee_no='9730100').one().attraction_id
            count=db.query(F.RotationEvent).filter_by(employee_no='9730100').count()
        payload={'employee_no':'9730100','name':'合成临时成员','attraction_id':aid,'role_code':'CM'}
        assert admin.post('/api/hr/employees',json={**payload,'name':'不是同一人'}).status_code==409
        ok(admin.post('/api/hr/employees',json=payload))
        assert temp.get('/api/me').status_code==401
        with SessionLocal() as db:
            m=db.query(T).filter_by(employee_no='9730100').one()
            assert m.adopted_at and m.adopted_employee_id and not m.enabled
            assert not db.query(TemporarySession).filter_by(member_id=m.id).first()
            assert db.query(F.RotationEvent).filter_by(employee_no='9730100').count()==count
            db.info['rotation_formal']=True
            assert any(e['temporary'] for e in S.day_log(db,aid,date.today().isoformat()) if e['employee_no']=='9730100')
        ok(login(temp,'9730100','0100'));assert ok(temp.get('/api/me'))['must_change_password']

def test_expiry_reuse_renewal_and_lockout():
    with TestClient(app) as manager,TestClient(app) as c:
        ok(login(manager));register_temp(manager,'9730105','合成续期')
        with SessionLocal() as db:
            m=db.query(T).filter_by(employee_no='9730105').one();m.valid_until=datetime.now()-timedelta(seconds=1);db.commit()
        assert login(c,'9730105','0105').status_code==401
        register_temp(manager,'9730105','合成续期')
        for _ in range(5): assert login(c,'9730105','wrong').status_code==401
        assert login(c,'9730105','0105').status_code==423
        with SessionLocal() as db:
            m=db.query(T).filter_by(employee_no='9730105').one();m.locked_until=None;db.commit()
        ok(login(c,'9730105','0105'))
        with SessionLocal() as db:
            m=db.query(T).filter_by(employee_no='9730105').one();m.valid_until=datetime.now()-timedelta(seconds=1);db.commit()
        assert c.get('/api/me').status_code==401


def test_excel_formal_registration_takes_over_without_overwriting_rotation_data():
    from app.services.employee_import import HEADERS
    with TestClient(app) as manager,TestClient(app) as admin,TestClient(app) as member:
        ok(login(manager));register_temp(manager,'9730106','合成导入接管')
        ok(login(member,'9730106','0106'));ok(login(admin,'HR01','rotation-temp-admin'))
        wb=Workbook();wb.active.append(HEADERS)
        wb.active.append(['9730106','合成导入接管','TR','热力追踪','Comp40',''])
        out=BytesIO();wb.save(out)
        files={'workbook':('synthetic.xlsx',out.getvalue(),'application/octet-stream')}
        p=ok(admin.post('/api/hr/import-employees',files=files,data={'preview':'true'}))
        assert p['would_create']==1 and not p['duplicates']
        ok(admin.post('/api/hr/import-employees',files=files,data={'expected_digest':p['digest']}))
        assert member.get('/api/me').status_code==401
        with SessionLocal() as db:
            m=db.query(T).filter_by(employee_no='9730106').one()
            assert m.adopted_employee_id==db.query(Employee).filter_by(employee_no='9730106').one().id


def test_adoption_between_authentication_and_action_is_rejected():
    from app.rotation.temporary import current,adopt
    from app.routers.rotation_board import me_act
    from starlette.requests import Request
    from fastapi import HTTPException
    with TestClient(app) as manager,TestClient(app) as c:
        ok(login(manager));register_temp(manager,'9730107','合成并发接管')
        ok(login(c,'9730107','0107'))
        request=Request({'type':'http','method':'POST','path':'/api/rotation-board/me/act',
            'headers':[(b'cookie',(COOKIE+'='+c.cookies.get(COOKIE)).encode())]})
        with SessionLocal() as db:
            stale=current(db,request)
        with SessionLocal() as db:
            m=db.query(T).filter_by(employee_no='9730107').one()
            e=Employee(employee_no=m.employee_no,name=m.name,attraction_id=m.attraction_id,is_active=True)
            db.add(e);db.flush();adopt(db,e);db.commit()
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            with pytest.raises(HTTPException) as exc:
                me_act({'action':'depart','confirmation_token':'0'*64},request,db,stale)
            assert exc.value.status_code==401


def test_password_derivation_holds_neither_rotation_nor_database_write_lock(monkeypatch):
    from app import v2_crypto
    from app.rotation.namespace import runtime_for
    from sqlalchemy import text
    original=v2_crypto.hash_password;calls=[]
    def checked(password):
        assert not runtime_for(formal=True).lock._is_owned()
        with SessionLocal() as db:
            db.execute(text('BEGIN IMMEDIATE'));db.rollback()
        calls.append(password)
        return original(password)
    with TestClient(app) as manager:
        ok(login(manager))
        monkeypatch.setattr(v2_crypto,'hash_password',checked)
        register_temp(manager,'9730108','合成短事务')
    assert calls==['0108']


def test_equal_password_suffixes_do_not_merge_login_identities():
    with TestClient(app) as manager,TestClient(app) as a,TestClient(app) as b:
        ok(login(manager))
        roster=[['9731109','合成同尾号甲',date.today().isoformat(),'00:00-23:59',''],['9741109','合成同尾号乙',date.today().isoformat(),'00:00-23:59','']]
        p=ok(upload(manager,roster,preview='true'));ok(upload(manager,roster,expected_digest=p['digest']))
        ok(login(a,'9731109','1109'));ok(login(b,'9741109','1109'))
        assert ok(a.get('/api/me'))['name']=='合成同尾号甲'
        assert ok(b.get('/api/me'))['name']=='合成同尾号乙'
        assert login(a,'1109','1109').status_code==401
