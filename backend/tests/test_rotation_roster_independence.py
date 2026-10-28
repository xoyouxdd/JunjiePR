"""Roster-driven cross-circle / abnormal participation, fictional isolated data only."""
from test_rotation_temporary import (app,setup,ok,login,upload,SessionLocal,Employee,UserAccount,
    Attraction,Role,EmployeeRoleAssignment,hash_password,TestClient,date,datetime,S,F)
from app.rotation.temporary import TemporaryMember,TemporarySession
from app.rotation.participation import RosterIdentity,contexts
import pytest
from copy import deepcopy


def ids():
    with SessionLocal() as db:return {a.name:a.id for a in db.query(Attraction)}


def seed_employee(no,name='合成支援员工',circle='热力追踪',enabled=True,account=True,active=True,role='CM'):
    with SessionLocal() as db:
        e=Employee(employee_no=no,name=name,attraction_id=ids()[circle],is_active=active,hired_on='2026-01-01')
        db.add(e);db.flush()
        if role:db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=db.query(Role).filter_by(code=role).one().id,
            starts_on='2026-01-01',status='active',assignment_type='permanent'))
        if account:db.add(UserAccount(employee_id=e.id,login_account=no,password_hash=hash_password('rotation-existing'),
            enabled=enabled,must_change_password=False,credential_initialized=True))
        db.commit();return e.id


def rows(*people):return [[no,name,date.today().isoformat(),'00:00-23:59',''] for no,name in people]


def commit(c,roster,**options):
    p=ok(upload(c,roster,preview='true'))
    return ok(upload(c,roster,expected_digest=p['digest'],**options))


def live(aid,no):
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        with S.transaction(db):
            cfg=S.load_config(db,aid);cfg.lines_json=S._dumps([{'id':'A1','posts':[{'name':'入口'},{'name':'出口'}]}]);cfg.settings_json=S._dumps({'parkClose':'23:59','closeDelay':0})
        S.do_draft_action(db,aid,'合成初始化',{'action':'draft_generate','date':date.today().isoformat(),'force':True})
        S.do_draft_action(db,aid,'合成初始化',{'action':'publish','date':date.today().isoformat()})
        with S.transaction(db):
            row=S.day_row(db,aid,date.today().isoformat());state=S.day_state(row)
            t=datetime.now();now=t.hour*60+t.minute+t.second/60
            state['lastTick']=now;state['plan'].update(crew={},seven={},push7=[])
            for line in state['lines']:
                for post in line['posts']:post['occ']=None
            p=state['persons'][no];p.update(state='ready',role='rotation',start=0,end=1440,readyAt=now-1,
                readySince=now-1,breakKind='rest',absences=[],visited=['A1'],line=None,
                assign={'line':'A1','mode':'push','departAt':now-1})
            # Materialize the same issued task a real live board has before
            # comparing a roster import; otherwise the background clock can
            # legitimately add its taskId between the two snapshots.
            S.advance(db,cfg,row,state,now)
            S.save_day(db,row,state)


def test_mixed_preflight_and_confirm_preserve_hr_and_permission_state():
    seed_employee('9730200','合成停用人员',enabled=False)
    seed_employee('9730201','合成无账号人员',account=False)
    roster=rows(('9730000','本圈'),('9730001','外圈'),('9730200','停用'),('9730201','无账号'),('9730202','合成新人员'))
    with TestClient(app) as c:
        ok(login(c));p=ok(upload(c,roster,preview='true'))
        assert len(p['warnings'])==2 and p['identities']['9730001']['cross_circle']
        assert upload(c,roster,expected_digest=p['digest']).status_code==409
        result=ok(upload(c,roster,expected_digest=p['digest'],confirm_exceptions='true'));assert result['entry_count']==5
        with SessionLocal() as db:
            assert not db.query(Employee).filter_by(employee_no='9730202').first()
            assert not db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730200').one().enabled
            assert not db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730201').first()
            assert db.query(Employee).filter_by(employee_no='9730001').one().attraction_id==ids()['矮人迷宫']
            assert db.query(RosterIdentity).count()>=5


def test_disabled_login_is_rotation_only_and_keeps_original_credentials():
    with TestClient(app) as c:
        assert login(c,'9730200','0200').status_code==401
        ok(login(c,'9730200','rotation-existing'))
        assert ok(c.get('/api/me'))['rotation_temporary']
        for path in ['/api/options','/api/action-center','/api/hr/employees','/api/rotation-board/board','/api/rotation-board/config']:
            assert c.get(path).status_code==403,path
        ok(c.get('/api/rotation-board/me'))
        assert c.post('/api/rotation-board/me/act',json={'pid':'9730000','action':'depart'}).status_code==403
        with SessionLocal() as db:assert not db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730200').one().enabled


def test_missing_login_account_uses_suffix_without_creating_workbench_account():
    with TestClient(app) as c:
        ok(login(c,'9730201','0201'));assert ok(c.get('/api/me'))['name']=='合成无账号人员'
        with SessionLocal() as db:assert not db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730201').first()


def test_cross_circle_member_reads_and_confirms_destination_without_owner_change():
    aid=ids()['热力追踪'];live(aid,'9730001')
    with TestClient(app) as c:
        ok(login(c,'9730001'))
        assert ok(c.get('/api/me'))['attraction_id']==ids()['矮人迷宫']
        own=ok(c.get('/api/rotation-board/me'));assert own['attraction_id']==aid
        p=own['person'];assert p['name']=='合成员工9730001'
        result=ok(c.post('/api/rotation-board/me/act',json={'action':'depart','attraction_id':aid,'confirmation_token':p['confirmationTokens']['depart']}))
        assert result['result']['line']=='A1'
        assert c.get('/api/rotation-board/me',params={'attraction_id':ids()['小熊罐子']}).status_code==403
        assert c.get('/api/rotation-board/config').status_code==403
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            log=S.day_log(db,aid,date.today().isoformat())
            entry=next(e for e in log if e['type']=='depart' and e['employee_no']=='9730001')
            assert entry['identity_labels']==['跨圈支援·原属矮人迷宫'] and not entry['temporary']


def test_multiple_circles_have_independent_receipts_and_actions():
    seed_employee('9730220','合成异圈主管',circle='矮人迷宫',role='SUPERVISOR')
    with TestClient(app) as c:
        ok(login(c,'9730220','rotation-existing'))
        commit(c,rows(('9730001','外圈支援')))
    aid=ids()['矮人迷宫'];live(aid,'9730001')
    with TestClient(app) as c:
        ok(login(c,'9730001'));own=ok(c.get('/api/rotation-board/me',params={'attraction_id':aid}))
        assert {r['id'] for r in own['rotation_circles']}=={ids()['热力追踪'],aid}
        token=own['person']['confirmationTokens']['depart']
        bad=c.post('/api/rotation-board/me/act',json={'action':'depart','attraction_id':ids()['热力追踪'],'confirmation_token':token})
        assert bad.status_code in (400,409)
        ok(c.post('/api/rotation-board/me/act',json={'action':'depart','attraction_id':aid,'confirmation_token':token}))
        assert ok(c.get('/api/rotation-board/confirmation',params={'token':token,'date':date.today().isoformat(),'attraction_id':aid}))['confirmed']
        assert not ok(c.get('/api/rotation-board/confirmation',params={'token':token,'date':date.today().isoformat(),'attraction_id':ids()['热力追踪']}))['confirmed']


def test_skip_some_and_stale_choice_are_atomic():
    with TestClient(app) as c:
        ok(login(c));roster=rows(('9730200','停用'),('9730201','无账号'),('9730203','合成保留新人员'))
        p=ok(upload(c,roster,preview='true'))
        result=ok(upload(c,roster,expected_digest=p['digest'],confirm_exceptions='true',skip_employee_nos='["9730200"]'))
        assert result['entry_count']==2 and result['skipped']==['9730200']
        assert upload(c,roster,expected_digest=p['digest'],confirm_exceptions='true',skip_employee_nos='["9730203"]').status_code==400
        newp=ok(upload(c,roster,preview='true'))
        with SessionLocal() as db:
            a=db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730200').one();a.enabled=True;db.commit()
        assert upload(c,roster,expected_digest=newp['digest'],confirm_exceptions='true').status_code==409


def test_recovery_revokes_abnormal_session_but_does_not_rewrite_history():
    with SessionLocal() as db:
        a=db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730200').one();a.enabled=False;db.commit()
    with TestClient(app) as manager,TestClient(app) as c:
        ok(login(manager));commit(manager,rows(('9730200','停用人员')),confirm_exceptions='true')
        live(ids()['热力追踪'],'9730200');ok(login(c,'9730200','rotation-existing'))
        p=ok(c.get('/api/rotation-board/me'))['person']
        ok(c.post('/api/rotation-board/me/act',json={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}))
        with SessionLocal() as db:
            a=db.query(UserAccount).join(Employee).filter(Employee.employee_no=='9730200').one();a.enabled=True;db.commit()
        assert c.get('/api/me').status_code==401
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            entry=next(e for e in S.day_log(db,ids()['热力追踪'],date.today().isoformat()) if e['type']=='depart' and e['employee_no']=='9730200')
            assert '账号停用' in entry['identity_labels'] and not entry['temporary']


@pytest.mark.parametrize('no,kind,expected',[
    ('9730230','inactive','人员非在职'),('9730231','archived','已删除留档'),
    ('9730232','roleless','未配置有效角色'),('9730233','intern','实习已到期')])
def test_other_account_exceptions_can_join_without_reenabling(no,kind,expected):
    from datetime import timedelta
    seed_employee(no,'合成异常'+no,active=kind not in ('inactive','archived'),
                  account=kind!='archived',role=None if kind=='roleless' else 'CM',enabled=kind!='intern')
    with SessionLocal() as db:
        e=db.query(Employee).filter_by(employee_no=no).one()
        if kind=='archived':e.account_deleted_at=datetime.now()
        if kind=='intern':e.employee_type='Intern';e.internship_ends_on=(date.today()-timedelta(days=1)).isoformat()
        db.commit()
    with TestClient(app) as manager,TestClient(app) as member:
        ok(login(manager));roster=rows((no,'合成异常'))
        preview=ok(upload(manager,roster,preview='true'))
        assert expected in preview['warnings'][0]['reasons']
        ok(upload(manager,roster,expected_digest=preview['digest'],confirm_exceptions='true'))
        ok(login(member,no,no[-4:] if kind=='archived' else 'rotation-existing'))
        assert ok(member.get('/api/me'))['rotation_temporary']
        assert member.get('/api/options').status_code==403
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no=no).one()
            if kind in ('inactive','archived'):assert not e.is_active
            if kind=='archived':assert e.account_deleted_at and not db.query(UserAccount).filter_by(employee_id=e.id).first()


def test_roster_only_roles_do_not_gain_shared_board_or_manager_permissions():
    seed_employee('9730234','合成只参与人员',role='AM')
    with TestClient(app) as manager,TestClient(app) as c:
        ok(login(manager));commit(manager,rows(('9730234','合成只参与人员')))
        ok(login(c,'9730234','rotation-existing'))
        assert ok(c.get('/api/me'))['rotation_entry']
        ok(c.get('/api/rotation-board/me'))
        for path in ['/board','/whoami','/config','/log?date='+date.today().isoformat()]:
            assert c.get('/api/rotation-board'+path).status_code==403


def test_live_reimport_refreshes_only_identity_facts_and_keeps_old_events():
    no='9730240';seed_employee(no,'合成运行中状态变更')
    aid=ids()['热力追踪'];roster=rows((no,'合成运行中状态变更'))
    with TestClient(app) as manager:
        ok(login(manager));commit(manager,roster);live(aid,no)
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            with S.transaction(db):
                state=S.day_state(S.day_row(db,aid,date.today().isoformat()))
                assert state['persons'][no]['assign']['taskId']
                before=deepcopy({'person':state['persons'][no],'lines':state['lines'],
                                 'tasks':state['rotationTasks']})
                S.DbHooks(db,aid,date.today().isoformat()).log('before_identity_change','合成检查',no,None,None,{})
            a=db.query(UserAccount).join(Employee).filter(Employee.employee_no==no).one();a.enabled=False;db.commit()
        commit(manager,roster,confirm_exceptions='true')
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            with S.transaction(db):
                state=S.day_state(S.day_row(db,aid,date.today().isoformat()))
                assert state['persons'][no]==before['person'] and state['lines']==before['lines']
                assert state['rotationTasks']==before['tasks']
                S.DbHooks(db,aid,date.today().isoformat()).log('after_identity_change','合成检查',no,None,None,{})
            events=S.day_log(db,aid,date.today().isoformat())
            assert next(e for e in events if e['type']=='before_identity_change')['identity_labels']==[]
            assert '账号停用' in next(e for e in events if e['type']=='after_identity_change')['identity_labels']
