"""Real isolated APIs for employee sessions, expiry, delegation and edit conflicts."""
from datetime import datetime,timedelta
from fastapi.testclient import TestClient
from test_rotation_flow import app,login,enter,ok,clock,roster_file,LINES,DAY
from app.v2_database import SessionLocal
from app.rotation.models import RotationAssistGrant,RotationAdjustSession
from app.rotation.adjustment import qualified,grant_code
from app.v2_models import Employee
from test_rotation_engine import live_day,person,two_lines,hm,seat
import pytest

def controller(client):
    login(client,'GSMTEST01');enter(client,'7777777')

def prepare(client):
    controller(client);ok(client.put('/api/rotation/config',json={'lines':LINES,'settings':{}}))
    ok(client.post('/api/rotation/roster/upload',files={'file':('roster.xlsx',roster_file(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')},data={'scope':'day','date':DAY}))
    ok(clock(client,action='set',date=DAY,time='08:00'))
    ok(client.post('/api/rotation/draft',json={'action':'draft_generate','date':DAY,'force':True}))
    ok(client.post('/api/rotation/draft',json={'action':'publish','date':DAY}))

def test_bound_code_session_expiry_revoke_and_no_escalation():
    with TestClient(app) as manager,TestClient(app) as trainer,TestClient(app) as other:
        controller(manager)
        with SessionLocal() as db:tr=db.query(Employee).filter_by(employee_no='TRTEST01').one();tr_id=tr.id
        response=ok(manager.post('/api/rotation/adjust/grants',json={'minutes':10,'employee_ids':[tr_id]}));grant_id=response['items'][0]['id']
        login(trainer,'TRTEST01');notice=ok(trainer.get('/api/rotation/adjust/mine'))['items'][0]
        assert len(notice['code'])==12 and notice['issuer']
        assert trainer.post('/api/rotation/enter',json={'account':'7777777'}).status_code==403
        enter(trainer,'6666666')
        ok(trainer.post('/api/rotation/adjust/login',json={'account':'TRTEST01','mode':'tr','credential':notice['code']}))
        assert trainer.get('/api/rotation/board').status_code==200
        for url,method in [('/api/rotation/adjust/grants','get'),('/api/rotation/accounts','get'),('/api/rotation/draft','post'),('/api/rotation/test-clock','post'),('/api/rotation/config','put')]:
            response=getattr(trainer,method)(url,**({'json':{}} if method!='get' else {}));assert response.status_code==403,(url,response.text)
        login(other,'GSMTEST01');enter(other,'6666666')
        assert other.post('/api/rotation/adjust/login',json={'account':'CMTEST01','mode':'tr','credential':notice['code']}).status_code==401
        ok(manager.post(f'/api/rotation/adjust/grants/{grant_id}/revoke',json={}))
        assert trainer.get('/api/rotation/board').status_code==401
        assert ok(trainer.get('/api/rotation/adjust/status'))['active'] is False
        ok(trainer.post('/api/rotation/adjust/logout',json={}))
        assert trainer.get('/api/rotation/screen/state').status_code==200
        assert trainer.get('/api/rotation/board').status_code==403

def test_manager_password_session_keeps_screen_and_pr_and_expires():
    with TestClient(app) as screen:
        login(screen,'GSMTEST01');enter(screen,'6666666')
        ok(screen.post('/api/rotation/adjust/login',json={'account':'SUPTEST01','mode':'manager','credential':'1234'}))
        assert ok(screen.get('/api/rotation/whoami'))['adjustment'] is True
        assert ok(screen.get('/api/me'))['employee_no']=='GSMTEST01'
        assert screen.get('/api/rotation/board').status_code==200
        with SessionLocal() as db:db.query(RotationAdjustSession).update({'expires_at':datetime.now()-timedelta(seconds=1)});db.commit()
        assert screen.get('/api/rotation/board').status_code==401
        assert screen.get('/api/rotation/screen/state').status_code==200
        ok(screen.post('/api/rotation/adjust/logout',json={}))
        assert screen.get('/api/rotation/screen/state').status_code==200

def test_preview_does_not_write_and_stale_edit_is_rejected():
    with TestClient(app) as client:
        prepare(client);board=ok(client.get('/api/rotation/board'));L=board['day']['lines'][0]
        body={'action':'post','line':L['id'],'i':0,'open':False,'closeMode':'push'}
        before=ok(client.get('/api/rotation/log',params={'date':DAY}))['items']
        preview=ok(client.post('/api/rotation/adjust/preview',json=body))
        assert ok(client.get('/api/rotation/log',params={'date':DAY}))['items']==before
        ok(client.post('/api/rotation/act',json={'action':'line','line':L['id'],'active':False}))
        assert client.post('/api/rotation/adjust/act',json={**body,**preview}).status_code==409
        ok(client.post('/api/rotation/act',json={'action':'line','line':L['id'],'active':True}))
        preview=ok(client.post('/api/rotation/adjust/preview',json=body))
        ok(client.post('/api/rotation/adjust/act',json={**body,'expected_snapshot':preview['expected_snapshot']}))
        assert ok(client.get('/api/rotation/board'))['day']['lines'][0]['posts'][0]['open'] is False

@pytest.mark.parametrize('mode',['push','direct'])
def test_close_modes_move_only_expected_people(mode):
    eng,hooks=live_day([person(str(i),'07:00','21:00') for i in range(3)],now='10:00')
    for i in range(3):seat(eng,str(i),'A',i,'09:30')
    eng.act_post('A',0,False,hm('10:00'),'test',close_mode=mode)
    slots=eng.line('A')['posts']
    assert slots[0]['occ'] is None and not slots[0]['open']
    assert [x['occ'] for x in slots[1:]]==(['0','1'] if mode=='push' else ['1','2'])
    assert eng.P['2' if mode=='push' else '0']['state']=='walkback'

def test_scheduled_close_swap_and_post_order():
    eng,_=live_day([person(str(i),'07:00','21:00') for i in range(3)],now='10:00')
    for i in range(3):seat(eng,str(i),'A',i,'09:30')
    eng.act_move('0','A',1,hm('10:00'),'test');assert [x['occ'] for x in eng.line('A')['posts']]==['1','0','2']
    assert eng.P['0']['lineStart']==hm('09:30')
    eng.act_post_order('A',1,0,hm('10:00'),'test');assert eng.line('A')['posts'][0]['occ']=='0'
    eng.act_post('A',0,False,hm('10:00'),'test',close_at=hm('10:05'),close_mode='push')
    assert eng.line('A')['posts'][0]['open']
    eng.tick(hm('10:05'));assert eng.line('A')['posts'][0]['open'] is False

def test_fifteen_minute_boundary_choice_and_force_remain():
    eng,_=live_day([person(str(i),'07:00','21:00') for i in range(4)],now='10:00')
    for p in eng.P.values():p.update(state='walkback',arriveAnchor=None)
    eng.P['1'].update(state='rest',readyAt=hm('10:10'))
    eng.P['2'].update(state='meal',readyAt=hm('10:15')+.1)
    assert not eng.meal_options(eng.P['0'],hm('10:00'))['canChoose']
    eng.P['2']['readyAt']=hm('10:15');assert eng.meal_options(eng.P['0'],hm('10:00'))['canChoose']
    eng.act_arrive('0',hm('10:00'),'test',meal_choice='rest');assert eng.P['0']['state']=='rest' and not eng.P['0'].get('ate')
    eng.act_arrive('3',hm('10:00'),'test',meal_choice='meal');assert eng.P['3']['state']=='meal' and eng.P['3']['ate']
    eng.P['0'].update(state='walkback',arriveAnchor=None,ate=False)
    eng.act_arrive('0',hm('12:00'),'test',meal_choice='rest');assert eng.P['0']['state']=='meal'


def test_reissue_revokes_old_grant_and_real_expiry_stops_write():
    with TestClient(app) as manager,TestClient(app) as trainer:
        prepare(manager)
        with SessionLocal() as db:tr_id=db.query(Employee).filter_by(employee_no='TRTEST01').one().id
        for minutes in (True,0,46,'10',[]):
            assert manager.post('/api/rotation/adjust/grants',json={'minutes':minutes,'employee_ids':[tr_id]}).status_code==400
        first=ok(manager.post('/api/rotation/adjust/grants',json={'minutes':5,'all':True}))['items']
        assert first
        login(trainer,'TRTEST01');old=ok(trainer.get('/api/rotation/adjust/mine'))['items'][0]
        second=ok(manager.post('/api/rotation/adjust/grants',json={'minutes':45,'employee_ids':[tr_id]}))['items'][0]
        new=ok(trainer.get('/api/rotation/adjust/mine'))['items'][0]
        assert old['code']!=new['code'] and new['id']==second['id']
        enter(trainer,'6666666')
        assert trainer.post('/api/rotation/adjust/login',json={'account':'TRTEST01','mode':'tr','credential':old['code']}).status_code==401
        ok(trainer.post('/api/rotation/adjust/login',json={'account':'TRTEST01','mode':'tr','credential':new['code']}))
        with SessionLocal() as db:
            g=db.get(RotationAssistGrant,new['id']);assert 2695<(g.expires_at-g.issued_at).total_seconds()<2705
            g.expires_at=datetime.now()-timedelta(seconds=1);db.commit()
        assert trainer.post('/api/rotation/act',json={'action':'line','line':'A','active':False}).status_code==401


def test_cancel_schedule_and_meal_rest_boundary():
    eng,_=live_day([person(str(i),'07:00','21:00') for i in range(3)],now='10:00')
    seat(eng,'0','A',0,'09:30')
    eng.act_post('A',0,False,hm('10:00'),'test',close_at=hm('10:05'),close_mode='push')
    eng.act_post('A',0,False,hm('10:00'),'test',cancel_close=True)
    assert eng.line('A')['posts'][0]['closeAt'] is None
    eng.P['1'].update(state='rest',readyAt=hm('10:10'),breakKind='rest')
    eng.P['2'].update(state='rest',readyAt=hm('10:16'),breakKind='meal_rest')
    assert not eng.meal_options(eng.P['0'],hm('10:00'))['canChoose']
    eng.P['2']['readyAt']=hm('10:15')
    assert eng.meal_options(eng.P['0'],hm('10:00'))['canChoose']


def test_multiple_trainers_have_independent_codes_and_revocation():
    from datetime import date
    from app.v2_models import UserAccount,Role,EmployeeRoleAssignment
    from app.v2_crypto import hash_password
    with TestClient(app) as manager,TestClient(app) as first,TestClient(app) as second:
        controller(manager)
        with SessionLocal() as db:
            source=db.query(Employee).filter_by(employee_no='TRTEST01').one()
            e=Employee(employee_no='TRSYNC02',name='并行培训师',attraction_id=source.attraction_id,is_active=True,hired_on=date.today().isoformat())
            db.add(e);db.flush()
            db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=db.query(Role).filter_by(code='TR').one().id,starts_on=date.today().isoformat(),assignment_type='permanent',status='active',reason='isolated test'))
            db.add(UserAccount(employee_id=e.id,login_account='TRSYNC02',password_hash=hash_password('1234'),enabled=True,must_change_password=False));db.commit()
        ok(manager.post('/api/rotation/adjust/grants',json={'minutes':15,'all':True}))
        login(first,'TRTEST01');login(second,'TRSYNC02')
        one=ok(first.get('/api/rotation/adjust/mine'))['items'][0];two=ok(second.get('/api/rotation/adjust/mine'))['items'][0]
        assert one['code']!=two['code']
        for client,account,notice in ((first,'TRTEST01',one),(second,'TRSYNC02',two)):
            enter(client,'6666666');ok(client.post('/api/rotation/adjust/login',json={'account':account,'mode':'tr','credential':notice['code']}))
        ok(manager.post(f"/api/rotation/adjust/grants/{one['id']}/revoke",json={}))
        assert not ok(first.get('/api/rotation/adjust/status'))['active']
        assert ok(second.get('/api/rotation/adjust/status'))['active']


def test_lead_self_entry_is_scoped_field_session():
    with TestClient(app) as lead:
        login(lead,'SUPTEST01')
        assert lead.post('/api/rotation/enter',json={'account':'7777777'}).status_code==403
        ok(lead.post('/api/rotation/enter',json={'account':'self'}))
        assert ok(lead.get('/api/rotation/whoami'))['adjustment']
        assert lead.get('/api/rotation/accounts').status_code==403
        ok(lead.post('/api/rotation/logout',json={}))
        assert lead.get('/api/rotation/adjust/status').status_code==401
