"""All-circle managers share Heat Pursuit; screen remains confirmation-only."""
import asyncio
from datetime import date, datetime, timedelta
import json
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from test_rotation_formal import app,seed,formal_daytime_runtime,seed_today,login,ok,SessionLocal
from app.rotation import service as S
from app.rotation.namespace import FORMAL_TABLES as F, runtime_for
from app.rotation.access import ACCOUNT_COOKIE, KIND_SCREEN, RotationActor
from app.rotation.models import RotationAccount, RotationAccountSession
from app.routers import rotation_screen
from app.v2_crypto import new_session_token, token_hash
from app.v2_models import Attraction, Employee, ManagementScope


@pytest.fixture
def today():
    return seed_today(rule_version=2)


@pytest.mark.parametrize('index,other,operate,manage',[
    (0,False,False,False),(0,True,False,False),(1,False,True,False),(1,True,False,False),
    *[(i,other,True,True) for i in range(2,6) for other in (False,True)]])
def test_effective_role_entry_and_target_circle(today,index,other,operate,manage):
    no=f'97100{index}{int(other)}'
    with TestClient(app) as client:
        login(client,no)
        assert ok(client.get('/api/me'))['rotation_entry']
        who=ok(client.get('/api/rotation-board/whoami'))
        assert who['capabilities']['can_operate']==operate and who['capabilities']['can_manage']==manage
        assert who['capabilities']['screen_account_admin']==(index==4 and not other)
        board=ok(client.get('/api/rotation-board/board'))
        assert board['identity']['attraction_name']==('热力追踪' if operate or not other else '矮人迷宫')
        assert who['attraction_name']==board['identity']['attraction_name']
        if operate:assert board['day']['ruleVersion']==2
        else:
            assert client.post('/api/rotation-board/act',json={'action':'pool','pid':'9710000','pool':'rest'}).status_code==403
        assert client.get('/api/rotation-board/config').status_code==(200 if manage else 403)


def test_base_gsm_from_another_circle_can_manage_screen_when_assigned_heat_pursuit():
    with TestClient(app) as client:
        login(client, '9710041')
        with SessionLocal() as db:
            circle = db.query(Attraction).filter_by(name='热力追踪').one()
            manager = db.query(Employee).filter_by(employee_no='9710041').one()
            scope = ManagementScope(employee_id=manager.id, attraction_id=circle.id, starts_on=date.today().isoformat())
            db.add(scope)
            db.flush()
            scope_id = scope.id
            db.commit()
        try:
            who = ok(client.get('/api/rotation-board/whoami'))
            assert who['capabilities']['screen_account_admin']
            assert ok(client.get('/api/rotation-screen/accounts'))['items']
        finally:
            with SessionLocal() as db:
                db.query(ManagementScope).filter_by(id=scope_id).delete()
                db.commit()


@pytest.mark.parametrize('index',range(2,6))
def test_cross_circle_manager_reads_writes_and_receipts_target_only_heat_pursuit(today,index):
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        other_id=db.query(Attraction.id).filter_by(name='矮人迷宫').scalar()
        with S.transaction(db):
            cfg=S.load_config(db,other_id);cfg.settings_json=json.dumps({'breakMin':31})
        other_before=(cfg.lines_json,cfg.settings_json,cfg.clock_json)
        sandbox_before=db.query(S.RotationEvent).count()
        actor_id=db.query(Employee.id).filter_by(employee_no=f'97100{index}1').scalar()
    with TestClient(app) as client:
        login(client,f'97100{index}1')
        ok(client.put('/api/rotation-board/config',json={'settings':{'breakMin':17}}))
        assert ok(client.get('/api/rotation-board/config'))['settings']['breakMin']==17
        day=ok(client.get('/api/rotation-board/board'))['day']
        p=next(p for p in day['persons'] if p['pid']=='9710000')
        token=p['confirmationTokens']['depart']
        result=ok(client.post('/api/rotation-board/act',json={'action':'depart','pid':p['pid'],
                  'confirmation_token':token,'expected_line':p['assign']['line'],'attraction_id':other_id}))
        assert result['executed']
        status=ok(client.get('/api/rotation-board/confirmation',params={'date':day['date'],'token':token}))
        assert status['confirmed']
        logs=ok(client.get('/api/rotation-board/log',params={'date':day['date']}))['items']
        assert any(r['type']=='depart' and r['employee_no']==p['pid'] for r in logs)
        ok(client.get('/api/rotation-board/daily',params={'month':day['date'][:7]}))
        ok(client.get('/api/rotation-board/daily/'+day['date']))
        ok(client.get('/api/rotation-board/roster',params={'date':day['date']}))
        assert client.post('/api/rotation-board/me/act',json={'action':'depart','pid':'9710000','confirmation_token':token}).status_code==403
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        cfg=S.load_config(db,other_id)
        assert (cfg.lines_json,cfg.settings_json,cfg.clock_json)==other_before
        assert not S.day_row(db,other_id,date.today().isoformat())
        assert db.query(F.RotationEvent).filter_by(attraction_id=today,event_type='depart',actor_id=actor_id).count()
        assert db.query(S.RotationEvent).count()==sandbox_before


def test_screen_rejects_every_non_confirmation_action_without_writes(today):
    with TestClient(app) as screen:
        ok(screen.post('/api/login',json={'employee_no':'6666666','password':'1243'}))
        state=ok(screen.get('/api/rotation-screen/state'))
        assert state['formal'] and state['day']['date']==date.today().isoformat()
        before=state['day']
        with SessionLocal() as db:count=db.query(F.RotationEvent).count()
        for action in S.LIVE_ACTIONS-S.SCREEN_ACTIONS:
            response=screen.post('/api/rotation-screen/act',json={'action':action,'pid':'9710000',
                               'line':'A1','i':0,'pool':'rest','confirmation_token':'x'*64})
            assert response.status_code==403,(action,response.text)
        after=ok(screen.get('/api/rotation-screen/state'))['day']
        assert after['lines']==before['lines']
        with SessionLocal() as db:assert db.query(F.RotationEvent).count()==count


def test_screen_can_confirm_depart_and_rest_but_not_resolve_exception(today):
    with TestClient(app) as manager,TestClient(app) as screen:
        login(manager,'9710021')
        ok(manager.put('/api/rotation-board/config',json={'settings':{'mealForceAfterStart':2000}}))
        ok(screen.post('/api/login',json={'employee_no':'6666666','password':'1243'}))
        day=ok(screen.get('/api/rotation-screen/state'))['day']
        p=next(p for p in day['persons'] if p['pid']=='9710000')
        token=p['confirmationTokens']['depart']
        assert ok(screen.post('/api/rotation-screen/act',json={'action':'depart','pid':p['pid'],
                  'confirmation_token':token,'expected_line':p['assign']['line']}))['executed']
        status=ok(screen.get('/api/rotation-screen/confirmation',params={'date':day['date'],'token':token}))
        assert status['confirmed']
        ok(manager.post('/api/rotation-board/act',json={'action':'pool','pid':p['pid'],'pool':'pending'}))
        p=next(q for q in ok(screen.get('/api/rotation-screen/state'))['day']['persons'] if q['pid']==p['pid'])
        assert ok(screen.post('/api/rotation-screen/act',json={'action':'arrive','pid':p['pid'],
                  'confirmation_token':p['confirmationTokens']['arrive'],'meal_choice':'rest'}))['executed']
        q=next(q for q in ok(screen.get('/api/rotation-screen/state'))['day']['persons'] if q['pid']==p['pid'])
        assert q['state']=='rest'


def test_screen_rejects_employee_and_retired_passwordless_screen_session():
    with TestClient(app) as client:
        login(client,'9710040')
        for endpoint in ('me','state','stream'):
            assert client.get(f'/api/rotation-screen/{endpoint}').status_code==401

        token=new_session_token()
        with SessionLocal() as db:
            account=db.query(RotationAccount).filter_by(kind=KIND_SCREEN).one()
            employee=db.query(Employee).filter_by(employee_no='9710040').one()
            # Persist the session shape produced by the former sandbox entry.
            db.add(RotationAccountSession(account_id=account.id,entered_by_id=employee.id,
                                          token_hash=token_hash(token),expires_at=datetime.now()+timedelta(hours=1)))
            db.commit()
        client.cookies.set(ACCOUNT_COOKIE,token)
        for endpoint in ('me','state','stream'):
            assert client.get(f'/api/rotation-screen/{endpoint}').status_code==403


def test_screen_stream_uses_formal_clock_and_closes_after_device_logout(today,monkeypatch):
    with TestClient(app) as screen:
        login_result=ok(screen.post('/api/login',json={'employee_no':'6666666','password':'1243'}))
        assert login_result['redirect']=='/rotation/screen'
        assert ok(screen.get('/api/rotation-screen/me'))['attraction_name']=='热力追踪'

        now=0
        monkeypatch.setattr(rotation_screen,'monotonic',lambda: now)

        async def connected():
            return False

        async def consume():
            nonlocal now
            request=SimpleNamespace(cookies=dict(screen.cookies),is_disconnected=connected)
            response=await rotation_screen.stream(request)
            events=response.body_iterator
            assert await anext(events)==f'data: {runtime_for(formal=True).version}\n\n'
            ok(screen.post('/api/rotation-screen/logout'))
            now=16
            with pytest.raises(StopAsyncIteration):
                await anext(events)

        asyncio.run(consume())
        assert screen.get('/api/rotation-screen/me').status_code==401


def test_service_layer_also_rejects_screen_placement(today):
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with pytest.raises(S.ActionError,match='大屏只可'):
            S.do_live_action(db,today,RotationActor('screen','合成大屏'),{'action':'pool','pid':'9710000','pool':'rest'})
