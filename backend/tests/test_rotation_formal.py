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


def seed_cohort(three=False):
    with SessionLocal() as db:
        aid=db.query(Attraction).filter_by(name='热力追踪').one().id
        exists=db.query(F.RotationDay).filter_by(attraction_id=aid,work_date=date.today().isoformat()).first() is not None
    if not exists: seed_today()
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            t=datetime.now();now=t.hour*60+t.minute+t.second/60
            state['entryVotes']={};state['confirmations']={}
            state['plan'].update(crew={},seven={},push7=[])
            line=state['lines'][0];template=deepcopy(line['posts'][0])
            line['posts']=[deepcopy(template) for _ in range(3)]
            for i,no in enumerate(['9710020','9710030','9710040']):
                p=deepcopy(state['persons']['9710020']);p.update(pid=no,name='模拟岗位人员'+str(i),state='onpost',line='A1',lineStart=now-30,
                    end=1440,visited=['A1'],assign=None,absences=[])
                state['persons'][no]=p;line['posts'][i].update(name='岗位'+str(i+1),occ=no,since=now-30)
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
            S.save_day(db,row,state)
    return aid


def test_formal_cohort_votes_survive_reload_tokens_remain_stable_and_commit_together():
    seed_cohort()
    with TestClient(app) as a,TestClient(app) as b:
        login(a,'9710000');login(b,'9710010')
        av=ok(a.get('/api/rotation-board/me'))['person'];bv=ok(b.get('/api/rotation-board/me'))['person']
        assert len(av['entryGroup']['members'])==2 and av['entryGroup']['confirmed']==0
        ab={'action':'depart','confirmation_token':av['confirmationTokens']['depart']}
        bb={'action':'depart','confirmation_token':bv['confirmationTokens']['depart']}
        # A TR/operator cannot impersonate another cohort member through /act either.
        assert b.post('/api/rotation-board/act',json={**ab,'pid':'9710000'}).status_code==400
        assert ok(a.post('/api/rotation-board/me/act',json=ab))['result']['status']=='waiting'
        assert ok(a.post('/api/rotation-board/me/act',json=ab))['duplicate']
        view=ok(b.get('/api/rotation-board/me'))['person']
        assert view['entryGroup']['confirmed']==1 and view['confirmationTokens']['depart']==bb['confirmation_token']
        assert view['state']=='ready'
        assert ok(b.post('/api/rotation-board/me/act',json=bb))['result']['status']=='executed'
        recovered=ok(a.get('/api/rotation-board/confirmation',params={'date':date.today().isoformat(),'token':ab['confirmation_token']}))
        assert recovered['result']['status']=='executed' and recovered['result']['post']=='岗位2'
        assert ok(a.post('/api/rotation-board/me/act',json=ab))['duplicate']
        ps=ok(b.get('/api/rotation-board/board'))['day']['persons']
        assert next(p for p in ps if p['pid']=='9710020')['state']=='done'
        assert next(p for p in ps if p['pid']=='9710000')['state']=='onpost'
        assert next(p for p in ps if p['pid']=='9710010')['state']=='onpost'


def test_operator_group_depart_after_one_person_voted_is_atomic_and_idempotent():
    seed_cohort(three=True)
    with TestClient(app) as member,TestClient(app) as operator:
        login(member,'9710000');login(operator,'9710020')
        p=ok(member.get('/api/rotation-board/me'))['person']
        personal={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}
        assert ok(member.post('/api/rotation-board/me/act',json=personal))['result']['status']=='waiting'
        board=ok(operator.get('/api/rotation-board/board'))
        p=next(p for p in board['day']['persons'] if p['pid']=='9710000')
        assert p['assign']['entryPost']=='岗位3'
        body={'action':'depart','pid':p['pid'],'confirmation_token':p['confirmationTokens']['group_depart'],
              'group_confirm':True,'expected_plan':p['entryGroup']['id']}
        assert body['confirmation_token']!=personal['confirmation_token']
        assert member.post('/api/rotation-board/me/act',json=body).status_code==403
        result=ok(operator.post('/api/rotation-board/act',json=body))
        assert result['result']['status']=='executed'
        assert ok(operator.post('/api/rotation-board/act',json=body))['duplicate']
        receipt=ok(member.get('/api/rotation-board/confirmation',params={'date':date.today().isoformat(),'token':personal['confirmation_token']}))
        assert receipt['result']['status']=='executed' and receipt['result']['post']=='岗位3'
        ps=ok(operator.get('/api/rotation-board/board'))['day']['persons']
        assert all(next(p for p in ps if p['pid']==no)['state']=='onpost' for no in ['9710000','9710010','9710050'])


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


def test_formal_single_relief_then_missing_person_returns_with_new_task():
    seed_cohort()
    with TestClient(app) as a,TestClient(app) as b:
        login(a,'9710000');login(b,'9710010')
        av=ok(a.get('/api/rotation-board/me'))['person'];bv=ok(b.get('/api/rotation-board/me'))['person']
        body={'action':'depart','confirmation_token':bv['confirmationTokens']['depart'],'single':True,'missing':['9710000']}
        assert ok(b.post('/api/rotation-board/me/act',json=body))['result']['status']=='executed'
        assert ok(b.post('/api/rotation-board/me/act',json=body))['duplicate']
        assert a.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':av['confirmationTokens']['depart']}).status_code==409
        held=ok(a.get('/api/rotation-board/me'))['person']
        assert held['entryHold'] and held['assign'] is None
        token=held['confirmationTokens']['entry_return']
        assert b.post('/api/rotation-board/act',json={'action':'entry_return','pid':'9710000','confirmation_token':token}).status_code==400
        assert ok(a.post('/api/rotation-board/me/act',json={'action':'entry_return','confirmation_token':token}))['ok']
        returned=ok(a.get('/api/rotation-board/me'))['person']
        assert not returned['entryHold'] and returned['assign'] and returned['state']=='ready'


def test_cohort_last_vote_commit_failure_never_publishes_partial_posts(monkeypatch):
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
                assert state['persons']['9710000']['state']=='ready'
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


def test_three_member_missing_one_recombines_with_relief_priority_and_new_consents():
    seed_cohort(three=True)
    with TestClient(app) as b,TestClient(app) as c:
        login(b,'9710010');login(c,'9710050')
        before=ok(b.get('/api/rotation-board/me'))['person']
        token=before['confirmationTokens']['entry_adjust']
        preview=ok(b.post('/api/rotation-board/me/entry-preview',json={'confirmation_token':token,'missing':['9710000']}))
        assert len(preview['members'])==2
        assert preview['jobs']['9710010']['mode']=='chain'
        assert ok(b.get('/api/rotation-board/me'))['person']['state']=='ready'
        body={'action':'entry_adjust','confirmation_token':token,'missing':['9710000'],'expected_plan':preview['id']}
        assert ok(b.post('/api/rotation-board/me/act',json=body))['result']['status']=='revised'
        assert ok(b.post('/api/rotation-board/me/act',json=body))['duplicate']
        bv=ok(b.get('/api/rotation-board/me'))['person'];cv=ok(c.get('/api/rotation-board/me'))['person']
        assert bv['entryGroup']['confirmed']==0 and len(bv['entryGroup']['members'])==2
        assert bv['state']=='ready' and cv['state']=='ready'
        assert ok(b.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':bv['confirmationTokens']['depart']}))['result']['status']=='waiting'
        assert ok(c.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':cv['confirmationTokens']['depart']}))['result']['status']=='executed'
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            state=S.day_state(S.day_row(db,db.query(Attraction).filter_by(name='热力追踪').one().id,date.today().isoformat()))
            assert state['persons']['9710020']['state']=='done'
            assert state['persons']['9710000']['entryHold']


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
        ok(tr.post('/api/rotation-board/adjust/act',json={**body,'expected_snapshot':preview['expected_snapshot']}))
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
