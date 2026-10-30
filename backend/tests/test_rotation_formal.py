"""Formal staff identity, scoped storage, real clock and personal confirmation integration."""
import os
from pathlib import Path
import tempfile
from datetime import date, datetime, timedelta
from copy import deepcopy
from io import BytesIO
from openpyxl import Workbook
from fastapi.testclient import TestClient
import pytest

os.environ['RECOGNITION_V2_DATA_DIR']=str(Path(tempfile.mkdtemp(prefix='fz-rotation-formal-')))
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='formal-fixture-2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='formal-admin-2026'
from app.main import app
from app.v2_database import SessionLocal
from app.v2_models import Employee, Role, Attraction, UserAccount, EmployeeRoleAssignment
from app.v2_crypto import hash_password
from app.rotation import service as S
from app.rotation.namespace import FORMAL_TABLES as F, runtime_for
from app.rotation.models import RotationDay, RotationConfig, RotationEvent


def ok(response):
    assert response.status_code==200,response.text
    return response.json()


def login(client,no):
    ok(client.post('/api/login',json={'employee_no':no,'password':'formal-fixture-2026'}))


@pytest.fixture(scope='module',autouse=True)
def seed():
    with TestClient(app):
        with SessionLocal() as db:
            for i,code in enumerate(['CM','TR','SUPERVISOR','TA_SUPERVISOR','GSM','TA_GSM']):
                for circle,suffix in [('热力追踪','0'),('矮人迷宫','1')]:
                    attraction=db.query(Attraction).filter_by(name=circle).one()
                    e=Employee(employee_no=f'97100{i}{suffix}',name='正式模拟'+code+suffix,attraction_id=attraction.id,is_active=True,hired_on='2026-01-01')
                    db.add(e);db.flush()
                    db.add(UserAccount(employee_id=e.id,login_account=e.employee_no,password_hash=hash_password('formal-fixture-2026'),enabled=True,must_change_password=False,credential_initialized=True))
                    role=db.query(Role).filter_by(code=code).one()
                    db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=role.id,starts_on='2026-01-01',status='active',assignment_type='permanent'))
            db.commit()
        yield


@pytest.mark.parametrize('index,operate,manage',[(0,False,False),(1,True,False),(2,True,True),(3,True,True),(4,True,True),(5,True,True)])
def test_formal_entry_and_permissions(index,operate,manage):
    with TestClient(app) as c:
        login(c,f'97100{index}0')
        assert ok(c.get('/api/me'))['rotation_entry']
        who=ok(c.get('/api/rotation-board/whoami'))
        assert who['formal'] and not who['test_mode']
        assert who['capabilities']['can_operate']==operate and who['capabilities']['can_manage']==manage
        assert ok(c.get('/api/rotation-board/board'))['clock']['mode']=='real'
        for endpoint in ['/config','/log?date='+date.today().isoformat(),'/daily?month='+date.today().strftime('%Y-%m'),'/roster?date='+date.today().isoformat()]:
            assert c.get('/api/rotation-board'+endpoint).status_code==(200 if manage else 403)
        assert c.post('/api/rotation-board/test-clock',json={'action':'reset'}).status_code==404
        assert c.post('/api/rotation-board/draft',json={'action':'draft_generate','force':True}).status_code==(403 if manage else 403)
        if not operate:assert c.post('/api/rotation-board/act',json={'action':'line','line':'A1','active':False}).status_code==403


def seed_today():
    """Materialized real-day fixture; never change the formal clock to a simulation."""
    with SessionLocal() as db:
        circle=db.query(Attraction).filter_by(name='热力追踪').one(); aid=circle.id
        # A simultaneous sandbox state with exactly the same date and attraction.
        cfg=S.load_config(db,aid);S.set_sim_clock(cfg,'2026-01-01',240,paused=True);db.commit()
        db.info['rotation_formal']=True
        now=datetime.now();minute=now.hour*60+now.minute+now.second/60
        parsed={'people':{no:{'name':db.query(Employee).filter_by(employee_no=no).one().name,'type':'','mark':''} for no in ['9710000','9710010','9710020']},
                'days':{date.today().isoformat():{no:'00:00-23:59' for no in ['9710000','9710010','9710020']}},'hours':{}}
        S.save_roster(db,aid,parsed,scope='day',file_name='formal-test.xlsx',uploader_id=None,uploader_name='隔离测试')
        with S.transaction(db):
            cfg=S.load_config(db,aid)
            cfg.lines_json=S._dumps([{'id':'A1','posts':[{'name':'入口'},{'name':'出口'}]}]);cfg.settings_json=S._dumps({'parkClose':'23:59','closeDelay':0})
        S.do_draft_action(db,aid,'fixture',{'action':'draft_generate','date':date.today().isoformat()})
        S.do_draft_action(db,aid,'fixture',{'action':'publish','date':date.today().isoformat()})
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            state['lastTick']=minute
            for no in ['9710000','9710010','9710020']:state['persons'][no].update(role='rotation',state='ready',end=1440,readyAt=minute-1,readySince=minute-1,breakKind='rest',absences=[],assign=None)
            for i,no in enumerate(['9710020','9710010']):
                state['persons'][no].update(state='onpost',line='A1',lineStart=minute-30,flags=[])
                state['lines'][0]['posts'][i].update(occ=no,since=minute-30)
            p=state['persons']['9710000'];p['assign']={'line':'A1','mode':'push','departAt':minute-1}
            S.save_day(db,row,state)
        return aid


def test_personal_confirmation_updates_shared_formal_board_without_touching_sandbox():
    aid=seed_today()
    with TestClient(app) as cm,TestClient(app) as tr,TestClient(app) as other:
        login(cm,'9710000');login(tr,'9710010');login(other,'9710001')
        own=ok(cm.get('/api/rotation-board/me'));token=own['person']['confirmationTokens']['depart']
        with SessionLocal() as db:
            test_before=db.query(RotationEvent).count();test_cfg=db.query(RotationConfig).filter_by(attraction_id=aid).one().clock_json
        assert any(row['type']=='rotation_step' for row in ok(cm.get('/api/action-center'))['items'])
        body={'action':'depart','pid':'9710000','confirmation_token':token}
        assert cm.post('/api/rotation-board/me/act',json={**body,'pid':'9710010'}).status_code==403
        result=ok(cm.post('/api/rotation-board/me/act',json=body))
        assert result['result']['line']=='A1' and result['result']['post']=='入口'
        assert ok(cm.post('/api/rotation-board/me/act',json=body))['duplicate']
        b=ok(tr.get('/api/rotation-board/board'));p=next(p for p in b['day']['persons'] if p['pid']=='9710000')
        assert p['state'] in ('heading','onpost')
        assert not any(row['type']=='rotation_step' for row in ok(cm.get('/api/action-center'))['items'])
        personal=ok(cm.get('/api/rotation-board/me'));assert personal['line']['id']=='A1' and len(personal['line']['posts'])==2
        assert ok(other.get('/api/rotation-board/board'))['day'] is None
        assert not ok(other.get('/api/rotation-board/confirmation',params={'date':date.today().isoformat(),'token':token}))['confirmed']
        assert 'draft' not in ok(cm.get('/api/rotation-board/board'))
        with SessionLocal() as db:
            assert db.query(RotationEvent).count()==test_before
            assert db.query(RotationConfig).filter_by(attraction_id=aid).one().clock_json==test_cfg
            assert db.query(F.RotationEvent).filter(F.RotationEvent.actor_id.isnot(None)).count()>=1
            assert not db.query(RotationDay).filter_by(attraction_id=aid,work_date=date.today().isoformat()).count()


def seed_cohort(three=False, mixed=False):
    with SessionLocal() as db:
        aid=db.query(Attraction).filter_by(name='热力追踪').one().id
        exists=db.query(F.RotationDay).filter_by(attraction_id=aid,work_date=date.today().isoformat()).first() is not None
    if not exists: seed_today()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            state['persons']={pid:p for pid,p in state['persons'].items() if pid in {'9710000','9710010','9710020','9710030','9710040','9710050'}}
            t=datetime.now();now=t.hour*60+t.minute+t.second/60
            state['lastTick']=now
            for person in state['persons'].values():
                for field in ('_taskRefresh','entryHold','taskTimeout'):person.pop(field,None)
            state['entryVotes']={};state['confirmations']={};state['rotationTasks']={}
            state['closed']=False;state['hold101Active']=False;state.pop('assignmentBoard',None)
            state['plan'].update(crew={},seven={},push7=[])
            line=state['lines'][0];template=deepcopy(line['posts'][0])
            state['lines']=[line]
            line['posts']=[deepcopy(template) for _ in range(3)]
            line['active']=True
            for post in line['posts']:
                post.update(open=True,opened=True,manual=False,openAt=None,closeAt=None,hold101Role=None)
                post.pop('hold',None);post.pop('hold101',None)
            for i,no in enumerate(['9710020','9710030','9710040']):
                p=deepcopy(state['persons']['9710020']);p.update(pid=no,name='模拟岗位人员'+str(i),role='rotation',state='onpost',line='A1',lineStart=now-30,
                    end=1440,visited=['A1'],assign=None,absences=[],ate=False,readyAt=None,breakKind=None,breakStart=None)
                state['persons'][no]=p;line['posts'][i].update(key='fixture-'+str(i),name='岗位'+str(i+1),occ=no,since=now-30)
                p['continuousStart']=now-30
            for no in ['9710000','9710010']:
                state['persons'][no].update(state='ready',start=int(now)-5,end=1440,readyAt=now-1,visited=[],
                    line=None,lineStart=None,breakKind='rest',assign={'line':'A1','mode':'push','departAt':now-1})
                state['persons'][no].pop('entryHold',None)
            state['persons']['9710000']['assign'].update(mode='chain',target='9710020',why='推下班')
            if three:
                p=deepcopy(state['persons']['9710010']);p.update(pid='9710050',name='正式模拟TA_GSM0')
                state['persons']['9710050']=p
            else:
                state['persons'].pop('9710050',None)
            if mixed:
                state['persons']['9710000']['visited'] = ['A1']
                state['persons']['9710010']['visited'] = ['B1']
                state['persons']['9710010']['start'] -= 30
            S.save_day(db,row,state)
    return aid


def test_formal_personal_routes_survive_reload_and_commit_independently():
    seed_cohort()
    with TestClient(app) as a,TestClient(app) as b:
        login(a,'9710000');login(b,'9710010')
        av=ok(a.get('/api/rotation-board/me'))['person'];bv=ok(b.get('/api/rotation-board/me'))['person']
        assert not av['entryGroup'] and av['task']['batch']==bv['task']['batch']
        assert av['task']['id']!=bv['task']['id']
        ab={'action':'depart','confirmation_token':av['confirmationTokens']['depart']}
        bb={'action':'depart','confirmation_token':bv['confirmationTokens']['depart']}
        assert ok(a.post('/api/rotation-board/me/act',json=ab))['result']['status']=='executed'
        assert ok(a.post('/api/rotation-board/me/act',json=ab))['duplicate']
        view=ok(b.get('/api/rotation-board/me'))['person']
        assert view['state']=='ready' and view['confirmationTokens']['depart']==bb['confirmation_token']
        assert ok(b.post('/api/rotation-board/me/act',json=bb))['result']['status']=='executed'
        assert ok(a.get('/api/rotation-board/me'))['person']['post']=='岗位1'
        assert ok(b.get('/api/rotation-board/me'))['person']['post']=='岗位2'


def test_formal_no_group_controls_and_operator_confirm_is_personal_and_idempotent():
    seed_cohort(three=True)
    with TestClient(app) as member,TestClient(app) as operator:
        login(member,'9710000');login(operator,'9710020')
        p=ok(member.get('/api/rotation-board/me'))['person']
        assert set(p['confirmationTokens'])=={'depart'}
        personal={'action':'depart','pid':p['pid'],'confirmation_token':p['confirmationTokens']['depart']}
        assert member.post('/api/rotation-board/me/act',json={**personal,'group_confirm':True}).status_code==403
        assert ok(operator.post('/api/rotation-board/act',json=personal))['result']['status']=='executed'
        assert ok(operator.post('/api/rotation-board/act',json=personal))['duplicate']
        ps=ok(operator.get('/api/rotation-board/board'))['day']['persons']
        assert next(x for x in ps if x['pid']=='9710000')['state']=='onpost'
        assert all(next(x for x in ps if x['pid']==no)['state']=='ready' for no in ['9710010','9710050'])


def test_forced_meal_placement_requires_preview_and_records_interruption():
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            now=state['lastTick']
            state['persons']['9710000'].update(state='meal',breakKind='meal',breakStart=now-5,
                                               readyAt=now+30,ate=True,assign=None)
            S.save_day(db,row,state)
    with TestClient(app) as operator:
        login(operator,'9710020')
        body={'action':'move','pid':'9710000','line':'A1','i':0,'force':True,'reason':'现场应急支援'}
        assert operator.post('/api/rotation-board/act',json=body).status_code==400
        preview=ok(operator.post('/api/rotation-board/adjust/preview',json=body))
        assert preview['summary']=='强制安排上岗'
        ok(operator.post('/api/rotation-board/adjust/act',json={**body,'expected_snapshot':preview['expected_snapshot']}))
        ps=ok(operator.get('/api/rotation-board/board'))['day']['persons']
        entrant=next(p for p in ps if p['pid']=='9710000')
        original=next(p for p in ps if p['pid']=='9710020')
        assert entrant['state']=='onpost' and entrant['interruptedBreak']['kind']=='meal'
        assert original['state']=='walkback' and original['readyAt'] is None


def test_formal_legacy_missing_flag_cannot_cancel_other_person_task():
    seed_cohort()
    with TestClient(app) as a,TestClient(app) as b:
        login(a,'9710000');login(b,'9710010')
        av=ok(a.get('/api/rotation-board/me'))['person'];bv=ok(b.get('/api/rotation-board/me'))['person']
        body={'action':'depart','confirmation_token':bv['confirmationTokens']['depart'],'single':True,'missing':['9710000']}
        assert ok(b.post('/api/rotation-board/me/act',json=body))['result']['status']=='executed'
        current=ok(a.get('/api/rotation-board/me'))['person']
        assert not current['entryHold'] and current['confirmationTokens']['depart']==av['confirmationTokens']['depart']
        assert ok(a.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':av['confirmationTokens']['depart']}))['result']['status']=='executed'


def test_personal_task_commit_failure_never_publishes_partial_posts(monkeypatch):
    aid=seed_cohort()
    with TestClient(app) as a,TestClient(app) as b:
        login(a,'9710000');login(b,'9710010')
        first=ok(a.get('/api/rotation-board/me'))['person']['confirmationTokens']['depart']
        ok(a.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':first}))
        last=ok(b.get('/api/rotation-board/me'))['person']['confirmationTokens']['depart']
        with runtime_for(formal=True).lock:
            with SessionLocal() as db:
                db.info['rotation_formal']=True
                before=S.day_row(db,aid,date.today().isoformat()).state_json
                from app.rotation.access import RotationActor
                employee=db.query(Employee).filter_by(employee_no='9710010').one()
                who=RotationActor('member',employee.name,member_employee_no=employee.employee_no,entered_by=employee,formal=True)
                def fail(): raise RuntimeError('cohort commit failed')
                monkeypatch.setattr(db,'commit',fail)
                with pytest.raises(RuntimeError,match='cohort commit failed'):
                    S.do_live_action(db,aid,who,{'action':'depart','pid':employee.employee_no,'confirmation_token':last})
            with SessionLocal() as db:
                db.info['rotation_formal']=True
                row=S.day_row(db,aid,date.today().isoformat())
                assert row.state_json==before
                state=S.day_state(row)
                assert state['persons']['9710000']['state']=='onpost'
                assert state['persons']['9710010']['state']=='ready'
        assert ok(b.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':last}))['result']['status']=='executed'


def test_formal_upcoming_counts_do_not_move_posts_or_skip_opening_rest():
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        row=S.day_row(db,aid,date.today().isoformat());cfg=S.load_config(db,aid)
        state=S.day_state(row);eng,_=S.engine_for(db,cfg,row,state)
        p=state['persons']['9710000'];p.update(state='notyet',start=600,end=1000,readyAt=None,assign=None)
        assert not S.person_view(eng,p,569,{})['upcomingShift']
        assert S.person_view(eng,p,570,{})['upcomingShift']
        assert p['state']=='notyet' and p.get('line') is None
        p.update(start=435)
        assert S.person_view(eng,p,430,{})['scheduledRestStart']==435
        assert not S.person_view(eng,p,405,{})['upcomingShift']
        assert not S.person_view(eng,p,434,{})['upcomingShift']
        p.update(start=420)
        assert not S.person_view(eng,p,390,{})['upcomingShift']
        p.update(start=450)
        assert not S.person_view(eng,p,419.99,{})['upcomingShift']
        assert S.person_view(eng,p,420,{})['upcomingShift']
        assert not S.person_view(eng,p,450,{})['upcomingShift']


def test_three_member_routes_preserve_special_relief_and_no_regroup_step():
    seed_cohort(three=True)
    with TestClient(app) as a,TestClient(app) as b,TestClient(app) as c:
        login(a,'9710000');login(b,'9710010');login(c,'9710050')
        av=ok(a.get('/api/rotation-board/me'))['person']
        assert av['task']['mode']=='chain' and av['task']['entryPost']=='岗位1'
        bv=ok(b.get('/api/rotation-board/me'))['person'];cv=ok(c.get('/api/rotation-board/me'))['person']
        assert 'entry_adjust' not in bv['confirmationTokens']
        assert bv['task']['batch']==cv['task']['batch']==av['task']['batch']
        for client,p in [(c,cv),(b,bv),(a,av)]:
            assert ok(client.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}))['result']['status']=='executed'
        ps=ok(b.get('/api/rotation-board/board'))['day']['persons']
        assert next(p for p in ps if p['pid']=='9710020')['state']=='done'
        assert all(next(p for p in ps if p['pid']==no)['state']=='onpost' for no in ['9710000','9710010','9710050'])


def test_upload_accepts_cross_circle_staff_and_formal_configuration_is_editable():
    with TestClient(app) as c:
        login(c,'9710020')
        ok(c.put('/api/rotation-board/config',json={'settings':{'breakMin':15}}))
        wb=Workbook();wb.active.append(['工号','姓名','日期','班次','备注'])
        wb.active.append(['9710001','另一圈员工',date.today().isoformat(),'07:15-16:15',''])
        out=BytesIO();wb.save(out)
        response=c.post('/api/rotation-board/roster/upload',files={'file':('foreign.xlsx',out.getvalue(),'application/octet-stream')},data={'scope':'day'})
        assert response.status_code==200,response.text
        with SessionLocal() as db:
            assert db.query(Employee).filter_by(employee_no='9710001').one().attraction_id!=db.query(Employee).filter_by(employee_no='9710020').one().attraction_id


def test_formal_manual_preview_is_required_and_stale_preview_is_rejected():
    with TestClient(app) as cm,TestClient(app) as tr:
        login(cm,'9710000');login(tr,'9710010')
        body={'action':'post_order','line':'A1','i':0,'target_i':1}
        assert cm.post('/api/rotation-board/adjust/preview',json=body).status_code==403
        assert tr.post('/api/rotation-board/adjust/act',json=body).status_code==400
        preview=ok(tr.post('/api/rotation-board/adjust/preview',json=body))
        ok(tr.post('/api/rotation-board/adjust/act',json={**body,'expected_snapshot':preview['expected_snapshot'],'notified':bool(preview['recent'])}))
        assert tr.post('/api/rotation-board/adjust/act',json={**body,'expected_snapshot':preview['expected_snapshot']}).status_code==409


def test_formal_configuration_reuses_only_static_lines_and_settings():
    with SessionLocal() as db:
        attraction=Attraction(name='隔离配置验证',active=True,employee_circle=False);db.add(attraction);db.commit()
        aid=attraction.id
        with S.transaction(db):
            cfg=S.load_config(db,aid);cfg.lines_json=S._dumps([{'id':'Q','posts':[{'name':'已配置岗位'}]}]);cfg.settings_json=S._dumps({'breakMin':18})
            S.set_sim_clock(cfg,'2025-01-01',240,paused=True)
        db.info['rotation_formal']=True
        with S.transaction(db):
            cfg=S.load_config(db,aid)
            assert S.lines_of(cfg)[0]['id']=='Q' and S.settings_of(cfg)['breakMin']==18
            assert S.clock_state(cfg)['mode']=='real'
            assert S.clock_now(cfg)[0]==date.today().isoformat()
        assert db.query(F.RotationDay).filter_by(attraction_id=aid).count()==0


def test_disabled_employee_session_loses_formal_access():
    with TestClient(app) as c:
        login(c,'9710000')
        with SessionLocal() as db:
            account=db.query(UserAccount).filter_by(login_account='9710000').one();account.enabled=False;db.commit()
        assert c.get('/api/rotation-board/board').status_code==401
        assert c.post('/api/rotation-board/me/act',json={'action':'arrive','pid':'9710000','confirmation_token':'x'*64}).status_code==401
        with SessionLocal() as db:
            db.query(UserAccount).filter_by(login_account='9710000').one().enabled=True;db.commit()


def test_valid_formal_upload_and_future_plan_use_only_formal_tables():
    future=(date.today()+timedelta(days=1)).isoformat()
    with TestClient(app) as c:
        login(c,'9710020')
        wb=Workbook();wb.active.append(['工号','姓名','日期','班次','备注'])
        for no in ['9710000','9710010']:wb.active.append([no,'模拟在职员工',future,'07:15-16:15',''])
        out=BytesIO();wb.save(out)
        ok(c.post('/api/rotation-board/roster/upload',files={'file':('formal.xlsx',out.getvalue(),'application/octet-stream')},data={'scope':'day'}))
        payload=ok(c.get('/api/rotation-board/daily/'+future));assert payload['can_generate']
        ok(c.post('/api/rotation-board/daily/'+future,json={'action':'draft_generate','revision':payload['revision']}))
        payload=ok(c.get('/api/rotation-board/daily/'+future));assert payload['draft'] and payload['can_edit']
        from app.rotation.models import RotationRosterEntry
        with SessionLocal() as db:
            assert db.query(F.RotationRosterEntry).filter_by(work_date=future).count()==2
            assert db.query(RotationRosterEntry).filter_by(work_date=future).count()==0
            assert db.query(F.RotationDay).filter_by(work_date=future).one().status=='draft'


def test_formal_commit_failure_preserves_its_cache_and_sandbox(monkeypatch):
    runtime=runtime_for(formal=True)
    with runtime.lock:
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            aid=db.query(Attraction).filter_by(name='热力追踪').one().id
            row=S.day_row(db,aid,date.today().isoformat());before=row.state_json
            count=db.query(F.RotationSnapshot).count()
            formal_cache=deepcopy(runtime.cache);sandbox_cache=deepcopy(S.RUNTIME.cache)
            def fail(): raise RuntimeError('formal commit failure')
            monkeypatch.setattr(db,'commit',fail)
            with pytest.raises(RuntimeError,match='formal commit failure'):
                with S.transaction(db):
                    state=S.day_state(row);state['lines'][0]['posts'][0]['name']='必须回滚'
                    S.save_day(db,row,state)
        assert runtime.cache==formal_cache and S.RUNTIME.cache==sandbox_cache
        with SessionLocal() as db:
            assert db.query(F.RotationDay).filter_by(attraction_id=aid,work_date=date.today().isoformat()).one().state_json==before
            assert db.query(F.RotationSnapshot).count()==count


def test_own_arrival_preserves_forced_meal_and_duplicate_protection():
    # A forced meal needs enough time before closing; the shared wall-clock
    # fixture preserves formal clock mode and the engine's eligibility rules.
    minute=720.0
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        aid=db.query(Attraction).filter_by(name='热力追踪').one().id
        with S.transaction(db):
            # Keep the fixture away from the real machine's closing-time boundary.
            cfg=S.load_config(db,aid);settings=S.settings_of(cfg);settings['mealEarliest']='00:00';cfg.settings_json=S._dumps(settings)
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            for line in state['lines']:
                for post in line['posts']:
                    if post.get('occ')=='9710000':post['occ']=None
            p=state['persons']['9710000']
            state['lastTick']=minute
            p.update(state='walkback',line=None,assign=None,ate=False,start=minute-310,end=1440,walkbackSince=minute-1,arriveAnchor=None,after=None)
            S.save_day(db,row,state)
    with TestClient(app) as c:
        login(c,'9710000')
        own=ok(c.get('/api/rotation-board/me'))
        assert own['person']['mealOptions']['forced']
        body={'action':'arrive','pid':'9710000','meal_choice':'rest','confirmation_token':own['person']['confirmationTokens']['arrive']}
        first=ok(c.post('/api/rotation-board/me/act',json=body))
        assert first['result']['state']=='meal'
        again=ok(c.post('/api/rotation-board/me/act',json=body))
        assert again['duplicate'] and first['result']['readyAt']==again['result']['readyAt']


def test_mixed_returning_routes_and_receipts_are_consistent():
    seed_cohort(mixed=True)
    with TestClient(app) as a, TestClient(app) as b:
        login(a,'9710000');login(b,'9710010')
        av=ok(a.get('/api/rotation-board/me'))['person'];bv=ok(b.get('/api/rotation-board/me'))['person']
        assert av['task']['batch']==bv['task']['batch']
        assert av['assign']['entryPost']=='岗位1' and bv['assign']['entryPost']=='岗位2'
        assert any(r['to']=='下班' for r in av['departureRoutes'])
        assert any('回休息室' in r['to'] for r in bv['departureRoutes'])
        for client,p in [(a,av),(b,bv)]:
            receipt=ok(client.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}))['result']
            assert receipt['status']=='executed' and receipt['post']==p['task']['entryPost']


def seed_cross_line():
    aid = seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        with S.transaction(db):
            row = S.day_row(db, aid, date.today().isoformat()); state = S.day_state(row)
            source = state['lines'][0]
            target = deepcopy(source); target.update(id='B1', posts=[deepcopy(source['posts'][2])])
            target['posts'][0].update(name='B线岗位',key='fixture-b')
            source['posts'][2].update(occ=None, since=None)
            state['persons']['9710040']['line'] = 'B1'
            state['lines'] = [source, target]
            S.save_day(db, row, state)
    return aid


def test_formal_cross_line_swap_preview_execution_and_permissions():
    seed_cross_line()
    with TestClient(app) as operator, TestClient(app) as cm:
        login(operator, '9710010'); login(cm, '9710000')
        body = {'action':'post_order', 'line':'A1', 'i':0, 'target_line':'B1', 'target_i':0, 'swapMode':'with_people'}
        assert cm.post('/api/rotation-board/adjust/preview', json=body).status_code == 403
        before = ok(operator.get('/api/rotation-board/board'))['day']
        preview = ok(operator.post('/api/rotation-board/adjust/preview', json=body))
        assert any('A1' in change and 'B1' in change for change in preview['changes'])
        unchanged = ok(operator.get('/api/rotation-board/board'))['day']
        assert before['lines'] == unchanged['lines']
        result = operator.post('/api/rotation-board/adjust/act', json={**body, 'expected_snapshot':preview['expected_snapshot']})
        ok(result)
        after = ok(operator.get('/api/rotation-board/board'))['day']
        lines = {line['id']:line for line in after['lines']}
        assert lines['A1']['posts'][0]['occ'] == '9710040'
        assert lines['B1']['posts'][0]['occ'] == '9710020'
        assert lines['A1']['posts'][0]['name'] == 'B线岗位'
        people = {p['pid']:p for p in after['persons']}
        assert people['9710040']['line'] == 'A1' and people['9710020']['line'] == 'B1'
        assert operator.post('/api/rotation-board/adjust/act', json={**body, 'expected_snapshot':preview['expected_snapshot']}).status_code == 409


def test_formal_daily_prestart_sync_and_opening_freeze_keep_manager_permission(monkeypatch):
    aid=seed_cross_line()
    clock={'minute':240}
    monkeypatch.setattr(S,'clock_now',lambda cfg:(date.today().isoformat(),clock['minute']))
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            state.pop('dailyOpening',None);state['lastTick']=240
            state['plan'].update(crew={'A1#0':'9710020','A1#1':'9710030','B1#0':'9710040'},seven={},push7=[])
            for p in state['persons'].values():p.update(state='notyet',start=435,line=None,lineStart=None,assign=None,visited=[])
            for line in state['lines']:
                for i,post in enumerate(line['posts']):post.update(occ=state['plan']['crew'].get(f"{line['id']}#{i}"),since=None)
            S.save_day(db,row,state)
    path='/api/rotation-board/daily/'+date.today().isoformat()
    with TestClient(app) as manager,TestClient(app) as tr,TestClient(app) as cm:
        login(manager,'9710020');login(tr,'9710010');login(cm,'9710000')
        assert tr.get(path).status_code==cm.get(path).status_code==403
        before=ok(manager.get(path));assert before['can_edit'] and before['prestart_live']
        body={'action':'draft_swap_people','source':'A1#0','target':'B1#0','revision':before['revision'],'plan_revision':before['plan_revision']}
        assert tr.post(path,json=body).status_code==cm.post(path,json=body).status_code==403
        ok(manager.post(path,json=body))
        after=ok(manager.get(path));board=ok(manager.get('/api/rotation-board/board'))['day']
        assert after['day']['lines']==board['lines']
        clock['minute']=435
        frozen=ok(manager.get(path))
        assert frozen['frozen'] and frozen['opening_source']=='opening' and not frozen['can_edit']
        assert frozen['day']['lines']==after['day']['lines']
        body.update(revision=frozen['revision'],plan_revision=frozen['plan_revision'])
        assert manager.post(path,json=body).status_code==400
        clock['minute']=720
        ok(manager.get('/api/rotation-board/board'))
        assert ok(manager.get(path))['day']==frozen['day']


def test_committed_live_view_cache_copies_results_and_invalidates_after_edit(monkeypatch):
    from app.rotation import workflow as W
    aid = seed_cohort()
    calls = []
    original = W.task_view
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(W, 'task_view', counted)
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        with S.transaction(db):
            cfg = S.load_config(db, aid)
            row = S.day_row(db, aid, date.today().isoformat())
            now = S.day_state(row)['lastTick']
            first = S.live_view(db, cfg, row, now)
            assert calls
            count = len(calls)
            second = S.live_view(db, cfg, row, now)
            assert second == first and len(calls) == count
            second['persons'].clear()
            assert S.live_view(db, cfg, row, now) == first
            state = S.day_state(row)
            state['persons']['9710000']['name'] = '修改后的模拟名称'
            S.save_day(db, row, state)
            changed = S.live_view(db, cfg, row, now)
            assert len(calls) > count
            assert next(p for p in changed['persons'] if p['pid'] == '9710000')['name'] == '修改后的模拟名称'


def seed_assignment_window(remaining=2):
    aid=seed_cross_line()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row);now=state['lastTick']
            state.pop('assignmentBoard',None);state['closed']=False
            for line in state['lines']:
                line['active']=True
                for post in line['posts']:
                    post.update(open=True,opened=True,manual=False,openAt=None,closeAt=None)
                    post.pop('hold',None)
            for pid,minutes,line in [('9710000',remaining,'A1'),('9710010',10,'B1')]:
                p=state['persons'][pid]
                p.update(state='ready' if minutes<=0 else 'rest' if pid=='9710000' else 'meal',
                         readyAt=now+minutes,breakStart=now-5,breakKind='rest' if pid=='9710000' else 'meal',
                         assign={'line':line,'mode':'push','departAt':now+max(0,minutes),'manual':True})
            S.save_day(db,row,state)
    return aid


def test_formal_issued_task_is_locked_but_closed_line_refreshes_destination():
    seed_assignment_window()
    with TestClient(app) as operator,TestClient(app) as cm:
        login(operator,'9710020');login(cm,'9710000')
        own=ok(cm.get('/api/rotation-board/me'))['person']
        assert own['assignmentLocked'] and not own['canDepart']
        assert cm.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':own['confirmationTokens']['depart']}).status_code==400
        protected={'action':'reassign','pid':'9710000','line':'B1'}
        assert operator.post('/api/rotation-board/act',json=protected).status_code==400
        assert operator.post('/api/rotation-board/act',json={**protected,'pid':'9710010','line':'A1'}).status_code==200
        board=ok(operator.get('/api/rotation-board/board'));people={p['pid']:p for p in board['day']['persons']}
        assert people['9710000']['assign']['line']=='A1' and people['9710010']['assign']['line']=='A1'
        assert not people['9710010']['canDepart']
        ok(operator.post('/api/rotation-board/act',json={'action':'line','line':'A1','active':False}))
        blocked=ok(cm.get('/api/rotation-board/me'))['person']
        assert blocked['assign']['line']=='B1' and not blocked['assign'].get('blocked') and not blocked['canDepart']
        assert blocked['readyAt']==own['readyAt'], 'refresh must not shorten rest'
        assert blocked['task']['allowedAt']==own['task']['allowedAt']
        assert blocked['task']['deadlineAt']==own['task']['deadlineAt']
        body={'action':'move','pid':'9710000','line':'B1','i':0,'force':True,'reason':'原线路停用，现场核对后安排'}
        # Refreshed valid tasks remain protected; a force flag cannot bypass rest.
        assert operator.post('/api/rotation-board/adjust/preview',json=body).status_code==400
        assert ok(cm.get('/api/rotation-board/me'))['person']['state']=='rest'


@pytest.mark.parametrize('transferred',[False,True])
@pytest.mark.parametrize('board_account',[None,'9710010','9710020'])
def test_timeout_screen_ack_is_immediately_ready_and_reclaims_only_unowned_task(monkeypatch,transferred,board_account):
    from app.rotation import workflow as W
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row);now=state['lastTick']
            p=state['persons']['9710000'];p['readyAt']=now-4;p['assign']['departAt']=now-4
            state['persons']['9710010'].update(state='rest',readyAt=now+(2 if transferred else 10),assign=None)
            eng,_=S.engine_for(db,S.load_config(db,aid),row,state)
            W.plan(eng,now-4);original=W.task_for(eng,p);old_id=original['id'];W.tick(eng,now)
            assert original['status']==('assigned' if transferred else 'waiting_transfer')
            S.save_day(db,row,state)
    monkeypatch.setattr(S,'clock_now',lambda cfg:(date.today().isoformat(),now))
    with TestClient(app) as screen,TestClient(app) as cm,TestClient(app) as receiver:
        if board_account:
            login(screen,board_account)
            endpoint='/api/rotation-board/act'
        else:
            ok(screen.post('/api/login',json={'employee_no':'6666666','password':'1243'}))
            endpoint='/api/rotation/screen/act'
        login(cm,'9710000');login(receiver,'9710010')
        before=ok(cm.get('/api/rotation-board/me'))['person'];assert before['entryHold']
        before_receiver=ok(receiver.get('/api/rotation-board/me'))['person']
        body={'action':'entry_return','pid':'9710000','confirmation_token':before['confirmationTokens']['entry_return']}
        assert cm.post('/api/rotation-board/me/act',json={**body,'pid':'9710010'}).status_code==403
        if board_account:
            assert screen.post(endpoint,json={**body,'confirmation_token':None}).status_code==400
            assert screen.post(endpoint,json={**body,'confirmation_token':'0'*64}).status_code==409
        ok(screen.post(endpoint,json=body))
        after=ok(cm.get('/api/rotation-board/me'))['person']
        assert not after['entryHold'] and after['state']=='ready' and after['readyAt']==now
        assert after['canDepart'] and after['task']['allowedAt']==now and after['task']['deadlineAt']==now+3
        assert (after['task']['id']!=old_id) is transferred
        if transferred:
            peer=ok(receiver.get('/api/rotation-board/me'))['person']
            assert peer['task']==before_receiver['task']
            assert peer['confirmationTokens']==before_receiver['confirmationTokens']
        else:
            assert after['task']['transferredFromName'] is None
        assert ok(screen.post(endpoint,json=body))['duplicate']
        again=ok(cm.get('/api/rotation-board/me'))['person']
        assert again['task']==after['task'] and again['readyAt']==now
        ok(cm.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':again['confirmationTokens']['depart']}))
        assert ok(cm.get('/api/rotation-board/me'))['person']['state']=='onpost'


def test_screen_666_uses_formal_board_and_has_only_rotation_confirmation_permission():
    seed_cohort()
    with TestClient(app) as screen,TestClient(app) as cm:
        login(screen,'9710020')
        assert screen.get('/api/me').status_code==200
        ok(screen.post('/api/login',json={'employee_no':'6666666','password':'1243'}))
        login(cm,'9710000')
        who=ok(screen.get('/api/rotation/whoami'));assert who['kind']=='screen' and not who['test_mode']
        own=ok(cm.get('/api/rotation-board/me'))['person']
        shared=ok(screen.get('/api/rotation/screen/state'))
        p=next(p for p in shared['day']['persons'] if p['pid']==own['pid'])
        assert shared['formal'] and p['task']['id']==own['task']['id']
        for endpoint in ['/api/me','/api/rotation-board/board','/api/rotation/config','/api/rotation/accounts']:
            assert screen.get(endpoint).status_code in (401,403)
        for action in ['move','rename_post','hold101','restore_line','set_close']:
            assert screen.post('/api/rotation/screen/act',json={'action':action}).status_code==403
            assert screen.post('/api/rotation/act',json={'action':action}).status_code==403
        assert screen.post('/api/rotation/test-clock',json={'action':'reset'}).status_code in (403,404)
        body={'action':'depart','pid':p['pid'],'confirmation_token':p['confirmationTokens']['depart']}
        assert ok(screen.post('/api/rotation/screen/act',json=body))['result']['status']=='executed'
        assert ok(cm.get('/api/rotation-board/me'))['person']['state']=='onpost'
        assert ok(cm.post('/api/rotation-board/me/act',json=body))['duplicate']


def test_formal_undo_rest_and_cross_line_dependency_with_stale_and_receipt_protection(monkeypatch):
    from app.rotation import workflow as W
    aid=seed_cross_line()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        state=S.day_state(S.day_row(db,aid,date.today().isoformat()));minute=state['lastTick']
    monkeypatch.setattr(S,'clock_now',lambda cfg:(date.today().isoformat(),minute))
    with TestClient(app) as operator,TestClient(app) as cm:
        login(operator,'9710020');login(cm,'9710000')
        board=ok(operator.get('/api/rotation-board/board'))
        own=ok(cm.get('/api/rotation-board/me'))['person']
        token=own['confirmationTokens']['depart']
        ok(cm.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':token}))
        # Move this entrant cross-line after departure. Undo of A1 must include B1 too.
        body={'action':'move','pid':'9710000','line':'B1','i':0}
        preview=ok(operator.post('/api/rotation-board/adjust/preview',json=body))
        assert preview['recent'] and preview['recent'][0]['pid']=='9710000'
        assert operator.post('/api/rotation-board/adjust/act',json={**body,'expected_snapshot':preview['expected_snapshot']}).status_code==400
        ok(operator.post('/api/rotation-board/adjust/act',json={**body,'expected_snapshot':preview['expected_snapshot'],'notified':True}))
        undo=ok(operator.post('/api/rotation-board/undo/preview',json={'line':'A1'}))
        assert set(undo['related_lines'])=={'A1','B1'} and undo['before_lines']!=undo['after_lines']
        restore={'action':'restore_line','line':'A1','checkpoint':undo['checkpoint'],'expected_snapshot':undo['expected_snapshot'],'notified':True}
        assert cm.post('/api/rotation-board/act',json=restore).status_code==403
        ok(operator.post('/api/rotation-board/act',json=restore))
        assert operator.post('/api/rotation-board/act',json=restore).status_code in (400,409)
        recovered=ok(cm.get('/api/rotation-board/confirmation',params={'date':date.today().isoformat(),'token':token}))
        # First undo is the cross-line drag; departure remains confirmed. Next restores departure.
        undo=ok(operator.post('/api/rotation-board/undo/preview',json={'line':'A1'}))
        ok(operator.post('/api/rotation-board/act',json={**restore,'checkpoint':undo['checkpoint'],'expected_snapshot':undo['expected_snapshot']}))
        assert not ok(cm.get('/api/rotation-board/confirmation',params={'date':date.today().isoformat(),'token':token}))['confirmed']
        assert cm.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':token}).status_code==409


def test_post_rename_keeps_task_token_and_101_binding_and_cancel_does_not_refresh():
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            state['lines'][0]['posts'][0]['hold101Role']='tower'
            S.save_day(db,row,state)
    with TestClient(app) as operator,TestClient(app) as cm:
        login(operator,'9710020');login(cm,'9710000')
        p=ok(cm.get('/api/rotation-board/me'))['person'];token=p['confirmationTokens']['depart']
        key=p['task']['beforePosts'][0]['key']
        ok(operator.post('/api/rotation-board/act',json={'action':'rename_post','post_key':key,'name':'新塔台'}))
        current=ok(cm.get('/api/rotation-board/me'))['person']
        assert current['task']['entryPost']=='新塔台' and current['confirmationTokens']['depart']==token
        ok(operator.post('/api/rotation-board/act',json={'action':'hold101','on':True}))
        board=ok(operator.get('/api/rotation-board/board'))
        assert board['day']['lines'][0]['posts'][0]['hold101']
        before={p['pid']:p['task'] for p in board['day']['persons'] if p.get('task')}
        ok(operator.post('/api/rotation-board/act',json={'action':'hold101','on':False}))
        after=ok(operator.get('/api/rotation-board/board'))
        after_tasks={p['pid']:p['task'] for p in after['day']['persons'] if p.get('task')}
        for pid,task in before.items():
            assert after_tasks[pid]['id']==task['id'] and after_tasks[pid]['revision']==task['revision']
            assert after_tasks[pid]['deadlineAt']==task['deadlineAt']


@pytest.mark.parametrize('choice',['rest','meal'])
def test_line_restore_cancels_confirmed_rest_or_meal_and_restores_continuous_time(choice):
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            cfg=S.load_config(db,aid);settings=S.settings_of(cfg)
            settings.update(mealEarliest='00:00',mealAfterStart=0,mealForceAfterStart=1000)
            cfg.settings_json=S._dumps(settings)
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            for p in state['persons'].values():p['start']=max(0,state['lastTick']-180)
            S.save_day(db,row,state)
    with TestClient(app) as tr,TestClient(app) as manager:
        login(tr,'9710010');login(manager,'9710020')
        p=ok(tr.get('/api/rotation-board/me'))['person']
        outgoing=next(r['pid'] for r in p['task']['routes'] if '下岗' in r['to'])
        before=ok(manager.get('/api/rotation-board/board'))['day']
        original=next(q for q in before['persons'] if q['pid']==outgoing)
        ok(tr.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}))
        state=ok(manager.get('/api/rotation-board/board'))['day']
        q=next(q for q in state['persons'] if q['pid']==outgoing)
        room={'action':'arrive','pid':outgoing,'confirmation_token':q['confirmationTokens']['arrive'],'meal_choice':choice}
        ok(manager.post('/api/rotation-board/act',json=room))
        state=ok(manager.get('/api/rotation-board/board'))['day']
        assert next(q for q in state['persons'] if q['pid']==outgoing)['state']==choice
        preview=ok(manager.post('/api/rotation-board/undo/preview',json={'line':'A1'}))
        ok(manager.post('/api/rotation-board/act',json={'action':'restore_line','line':'A1','checkpoint':preview['checkpoint'],'expected_snapshot':preview['expected_snapshot'],'notified':True}))
        restored=ok(manager.get('/api/rotation-board/board'))['day']
        q=next(q for q in restored['persons'] if q['pid']==outgoing)
        assert q['state']=='onpost' and q['continuousStart']==original['continuousStart']
        assert q['readyAt']==original['readyAt'] and q['ate']==original['ate']
        assert manager.post('/api/rotation-board/act',json=room).status_code==409


def test_restore_scheduled_closure_after_it_executed_restores_occupant_state(monkeypatch):
    aid=seed_cohort()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        state=S.day_state(S.day_row(db,aid,date.today().isoformat()));minute=int(state['lastTick'])
    time={'now':float(minute)}
    monkeypatch.setattr(S,'clock_now',lambda cfg:(date.today().isoformat(),time['now']))
    with TestClient(app) as operator:
        login(operator,'9710020')
        close=minute+1;body={'action':'post','line':'A1','i':0,'open':False,'closeAt':f'{close//60:02d}:{close%60:02d}','closeMode':'direct'}
        p=ok(operator.post('/api/rotation-board/adjust/preview',json=body))
        ok(operator.post('/api/rotation-board/adjust/act',json={**body,'expected_snapshot':p['expected_snapshot']}))
        time['now']=close+.1
        board=ok(operator.get('/api/rotation-board/board'))['day']
        assert not board['lines'][0]['posts'][0]['open']
        assert next(p for p in board['persons'] if p['pid']=='9710020')['state']=='walkback'
        undo=ok(operator.post('/api/rotation-board/undo/preview',json={'line':'A1'}))
        ok(operator.post('/api/rotation-board/act',json={'action':'restore_line','line':'A1','checkpoint':undo['checkpoint'],'expected_snapshot':undo['expected_snapshot'],'notified':True}))
        board=ok(operator.get('/api/rotation-board/board'))['day']
        assert board['lines'][0]['posts'][0]['open'] and board['lines'][0]['posts'][0]['occ']=='9710020'
        assert next(p for p in board['persons'] if p['pid']=='9710020')['state']=='onpost'
