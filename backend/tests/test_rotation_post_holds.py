"""Continuous post holds: real permissions/persistence and deterministic engine boundaries."""
from test_rotation_formal import app, seed, seed_today, login, ok, TestClient, SessionLocal, S, F, date, datetime
from test_rotation_engine import person, live_day, seat, occupants, hm, ActionError
import pytest


def engine():
    eng,hooks=live_day([person(p,'08:00','18:00') for p in ['a','b','c','next']],lines=[{'id':'A','group':'A','posts':[{'name':str(i)} for i in range(3)]}])
    for i,p in enumerate(['a','b','c']):seat(eng,p,'A',i,'09:00')
    return eng,hooks


def hold(eng,p='b',on=True,now=hm('09:10')):
    line,index=eng.find_post(p)
    eng.act_post_hold(line['id'],index,p,on,'现场接待',now,'test')


def reset_today():
    with SessionLocal() as db:
        db.info['rotation_formal']=True
        row=db.query(F.RotationDay).filter_by(work_date=date.today().isoformat()).first()
        if row:
            # Recreate this disposable fixture rather than turning a running day
            # back into a draft that is correctly locked after its first shift.
            with S.transaction(db):db.delete(row)
    return seed_today()


def test_ordinary_push_skips_fixed_person_and_resumes_without_resetting_standing_time():
    e,h=engine();hold(e);e.P['next']['assign']={'line':'A','mode':'push','departAt':hm('09:12')}
    e.act_depart('next',hm('09:12'),'test')
    assert occupants(e,'A')==['next','b','a']
    assert e.P['b']['lineStart']==hm('09:00')
    hold(e,on=False,now=hm('09:15'))
    assert not e.held('b') and e.P['b']['lineStart']==hm('09:00')
    assert occupants(e,'A')==['next','b','a']
    assert h.types().count('post_hold')==1 and h.types().count('post_resume')==1


def test_hold_overrides_shift_end_and_closure_until_manual_cancel():
    e,h=engine();hold(e);e.P['b']['end']=hm('09:15');e.d['closeAt']='09:20'
    e.tick(hm('09:30'))
    assert e.d['closed'] and e.P['b']['state']=='onpost' and e.held('b')
    assert e.line('A')['posts'][1]['occ']=='b' and e.line('A')['posts'][1]['open']
    hold(e,on=False,now=hm('09:31'));e.tick(hm('09:31'))
    assert not e.held('b') and e.P['b']['state']=='done'
    assert not e.line('A')['posts'][1]['open']


def test_absence_waits_for_cancel_then_uses_current_time():
    e,h=engine();hold(e);e.P['b']['absences']=[{'start':hm('09:15'),'end':hm('10:15'),'label':'OJT','done':False}]
    e.tick(hm('09:20'));assert e.P['b']['state']=='onpost' and e.held('b')
    assert all(p['pid']!='b' for _,p,_ in e._replace_needs(hm('09:20')))
    hold(e,on=False,now=hm('09:21'))
    assert e.P['b']['state']=='away' and e.P['b']['away']['reason']=='OJT'


def test_scheduled_close_waits_and_zero_ends_hold():
    e,h=engine();hold(e);e.line('A')['posts'][1]['closeAt']='09:15'
    e.tick(hm('09:16'));assert e.line('A')['posts'][1]['open'] and e.held('b')
    e.end_day(1440)
    assert not any(x.get('hold') for x in e.line('A')['posts'])
    assert e.d['status']=='ended' and e.P['b']['state']=='done'
    assert any(event['detail'].get('reason')=='零点定岗自动结束' for event in h.events)


def test_relief_chain_skips_fixed_middle_post():
    e,h=engine();hold(e);e.P['c']['end']=hm('09:30')
    e.P['next']['assign']={'line':'A','mode':'chain','target':'c','why':'推下班','departAt':hm('09:25')}
    e.act_depart('next',hm('09:25'),'test')
    assert occupants(e,'A')==['next','b','a'] and e.held('b')
    assert e.P['c']['state']=='done'


def test_all_fixed_posts_leave_newcomer_ready_and_cannot_replace_fixed_target():
    e,h=engine()
    for p in ['a','b','c']:hold(e,p)
    assert e._best_line(e.P['next'],hm('09:12'),{}) is None
    e.P['next']['assign']={'line':'A','mode':'push','departAt':hm('09:12')}
    with pytest.raises(ActionError,match='可轮换岗位'):
        e.act_depart('next',hm('09:12'),'test')
    assert occupants(e,'A')==['a','b','c'] and e.P['next']['state']=='ready'
    hold(e,'a',False,hm('09:13'))
    e.P['next'].update(state='heading',arriveAt=hm('09:14'),departedAt=hm('09:14'),
        assign={'line':'A','mode':'chain','target':'c','departAt':hm('09:14')})
    e._arrive_line(e.P['next'],hm('09:14'))
    assert occupants(e,'A')==['a','b','c'] and e.P['next']['state']=='ready'


def test_cancel_after_scheduled_close_and_after_absence_window_does_not_replay_old_actions():
    e,h=engine();hold(e);e.line('A')['posts'][1]['closeAt']='09:15'
    e.tick(hm('09:16'));hold(e,on=False,now=hm('09:17'));e.tick(hm('09:17'))
    assert not e.line('A')['posts'][1]['open']
    e,h=engine();hold(e);e.P['b']['absences']=[{'start':hm('09:15'),'end':hm('09:20'),'label':'OJT','done':False}]
    e.tick(hm('09:30'));hold(e,on=False,now=hm('09:31'))
    assert e.P['b']['state']=='onpost' and not e.held('b')


@pytest.mark.parametrize('action',['move','close','line','remove','away','leave'])
def test_manual_changes_require_explicit_cancel(action):
    e,h=engine();hold(e)
    with pytest.raises(ActionError,match='定岗'):
        if action=='move':e.act_move('b','A',0,hm('09:12'),'test')
        elif action=='close':e.act_post('A',1,False,hm('09:12'),'test')
        elif action=='line':e.act_line('A',False,hm('09:12'),'test')
        elif action=='remove':e.act_fix_remove('b','修正',hm('09:12'),'test')
        elif action=='away':e.act_away('b','任务',hm('09:12'),'test')
        else:e.act_leave('b','离岗',hm('09:12'),'test')
    assert occupants(e,'A')[1]=='b'


@pytest.mark.parametrize('role_index',[1,2,3,4,5])
def test_tr_lead_and_manager_can_set_cancel_and_share_state(role_index):
    reset_today()
    body={'action':'post_hold','line':'A1','i':0,'pid':'9710020','on':True,'reason':'现场接待'}
    with TestClient(app) as operator,TestClient(app) as cm:
        login(operator,f'97100{role_index}0');login(cm,'9710000')
        ok(operator.post('/api/rotation-board/act',json=body))
        board=ok(cm.get('/api/rotation-board/board'))
        assert board['day']['lines'][0]['posts'][0]['hold']['reason']=='现场接待'
        assert cm.post('/api/rotation-board/act',json={**body,'on':False}).status_code==403
        ok(operator.post('/api/rotation-board/act',json={**body,'on':False}))
        assert not ok(cm.get('/api/rotation-board/board'))['day']['lines'][0]['posts'][0].get('hold')


def test_stale_person_and_confirmation_cannot_modify_revised_route():
    reset_today()
    with TestClient(app) as tr,TestClient(app) as cm:
        login(tr,'9710010');login(cm,'9710000')
        token=ok(cm.get('/api/rotation-board/me'))['person']['confirmationTokens']['depart']
        body={'action':'post_hold','line':'A1','i':1,'pid':'9710010','on':True}
        assert tr.post('/api/rotation-board/act',json={**body,'pid':'9710020'}).status_code==400
        assert tr.post('/api/rotation-board/act',json={**body,'on':'true'}).status_code==400
        ok(tr.post('/api/rotation-board/act',json=body))
        assert cm.post('/api/rotation-board/me/act',json={'action':'depart','pid':'9710000','confirmation_token':token}).status_code==409
        with SessionLocal() as db:
            db.info['rotation_formal']=True
            aid=db.query(F.RotationDay).filter_by(work_date=date.today().isoformat()).one().attraction_id
            with pytest.raises(ActionError,match='定岗'):
                S.do_draft_action(db,aid,'test',{'action':'draft_generate','date':date.today().isoformat(),'force':True})
