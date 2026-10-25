"""Early previews and the exact three-minute assignment stability boundary."""
from copy import deepcopy
import pytest
from app.rotation.engine import ActionError
from test_rotation_engine import live_day, person, seat, two_lines, occupants

NOW=540


def fixture(remaining=10,state='rest',posts=1):
    eng,hooks=live_day([person(pid,'07:00','21:00') for pid in ('a','b','p','q')],lines=two_lines(posts))
    seat(eng,'a','A',0,'08:30');seat(eng,'b','B',0,'08:00')
    eng.P['q'].update(state='excluded',role='excluded')
    p=eng.P['p'];p.update(state=state,readyAt=NOW+remaining,breakStart=NOW-5,breakKind='meal' if state=='meal' else 'rest',
        assign={'line':'A','mode':'push','departAt':NOW+remaining})
    return eng,hooks


@pytest.mark.parametrize('state',['rest','meal'])
@pytest.mark.parametrize('remaining,locked',[(3.0001,False),(3,True),(2.9999,True),(0,True)])
def test_exact_window_protects_existing_assignment_but_long_break_can_replan(state,remaining,locked):
    eng,_=fixture(remaining,state);original=deepcopy(eng.P['p']['assign'])
    assert eng.assignment_locked(eng.P['p'],NOW)==locked
    eng._assign_all(NOW)
    if locked:assert eng.P['p']['assign']==original
    else:assert eng.P['p']['assign']['line']=='B'
    assert eng.P['p']['readyAt']==NOW+remaining and eng.P['p']['state']==state


def test_ready_assignment_is_not_stolen_by_new_relief_need():
    eng,_=fixture(0,'ready');original=deepcopy(eng.P['p']['assign'])
    eng.P['b']['end']=NOW+5
    eng.P['q'].update(state='rest',role='rotation',readyAt=NOW+8,breakKind='rest',assign=None)
    eng._assign_all(NOW)
    assert eng.P['p']['assign']==original
    assert eng.P['q']['assign']['mode']=='chain' and eng.P['q']['assign']['target']=='b'


def test_fixed_cohort_does_not_split_when_teammate_has_longer_break():
    eng,_=fixture(3);eng.d['cooperativeEntry']=True
    eng.P['p']['assign']['departAt']=550
    eng.P['q'].update(state='rest',role='rotation',readyAt=550,assign={'line':'A','mode':'push','departAt':550})
    before={pid:deepcopy(eng.P[pid]['assign']) for pid in ('p','q')}
    eng._assign_all(NOW)
    assert all(eng.P[pid]['assign']==before[pid] for pid in before)
    assert eng.assignment_locked(eng.P['q'],NOW)


def test_meal_preview_does_not_cut_meal_and_survives_rest_transition():
    eng,_=fixture(60,'meal');eng.P['p']['assign']=None
    eng.tick(NOW)
    a=deepcopy(eng.P['p']['assign']);assert a and a['departAt']==600
    assert eng.P['p']['state']=='meal' and eng.P['p']['readyAt']==600
    eng.tick(580)
    assert eng.P['p']['state']=='rest' and eng.P['p']['breakKind']=='meal_rest'
    assert eng.P['p']['assign']==a
    with pytest.raises(ActionError):eng.act_depart('p',599,'self')
    eng.tick(600);eng.act_depart('p',600,'self')
    assert eng.P['p']['state']=='onpost' and eng.P['p']['lineStart']==600


def test_future_meal_route_does_not_reserve_post_ahead_of_current_ready_person():
    eng,_=fixture(60,'meal')
    eng.P['p']['assign']={'line':'B','mode':'push','departAt':600,'manual':True}
    eng.P['q'].update(state='ready',role='rotation',readyAt=NOW,assign=None)
    eng._assign_all(NOW)
    assert eng.P['q']['assign']['line']=='B'
    assert eng.P['p']['assign']['departAt']==600


def test_new_opening_uses_mutable_long_break_not_ready_assignment():
    eng,_=fixture(0,'ready',posts=2);original=deepcopy(eng.P['p']['assign'])
    eng.P['q'].update(state='rest',role='rotation',readyAt=550,assign={'line':'B','mode':'push','departAt':550})
    eng.act_post('A',1,False,NOW,'manager');eng.act_post('A',1,True,NOW,'manager',open_at=543)
    eng.tick(NOW)
    assert eng.P['p']['assign']==original
    assert eng.P['q']['assign']['mode']=='opening' and eng.P['q']['assign']['departAt']==550


def test_invalid_fixed_route_pauses_without_silent_retarget_and_can_be_repaired_explicitly():
    eng,hooks=fixture(0,'ready');original=deepcopy(eng.P['p']['assign'])
    eng.act_line('A',False,NOW,'manager');eng.tick(NOW)
    a=eng.P['p']['assign'];assert all(a[k]==v for k,v in original.items()) and a['blocked']
    assert 'assign_blocked' in hooks.types('p')
    before=deepcopy(eng.d)
    with pytest.raises(ActionError):eng.act_depart('p',NOW,'self')
    assert eng.d==before
    eng.act_move('p','B',0,NOW,'manager',force=True,reason='原线路停用，现场核对后改派')
    assert eng.P['p']['state']=='onpost' and eng.P['p']['line']=='B'


@pytest.mark.parametrize('action',['reassign','move','fix'])
def test_valid_fixed_plan_cannot_be_redistributed_through_manual_entry(action):
    eng,_=fixture(3);before=deepcopy(eng.d)
    with pytest.raises(ActionError):
        if action=='reassign':eng.act_reassign('p','B',NOW,'manager')
        elif action=='move':eng.act_move('p','B',0,NOW,'manager',force=True,reason='普通调配')
        else:eng.act_fix_place('p','B',0,'普通调配',NOW,'manager')
    assert eng.d==before


def test_target_invalidated_during_window_is_not_replaced_by_ordinary_push():
    eng,_=fixture(2);eng.P['p']['assign'].update(mode='chain',target='a',why='推下班')
    eng.act_away('a','现场暂离',NOW,'manager');eng.tick(NOW)
    assert eng.P['p']['assign']['mode']=='chain' and eng.P['p']['assign']['target']=='a'
    assert eng.P['p']['assign']['blocked']


def test_blocked_cohort_can_be_repaired_by_reasoned_forced_preview_path():
    eng,_=fixture(0,'ready');eng.d['cooperativeEntry']=True
    eng.P['q'].update(state='ready',role='rotation',readyAt=NOW,assign={'line':'A','mode':'push','departAt':NOW})
    from app.rotation.cooperation import plan
    assert plan(eng,eng.P['p'],NOW)['blocked']
    eng.act_move('p','B',0,NOW,'manager',force=True,reason='原组岗位不足，现场核对分线')
    assert eng.P['p']['line']=='B' and eng.P['q']['assign']['line']=='A'
