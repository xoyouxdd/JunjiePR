"""First-entry preview, independent consent, and single-person relief priority."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.rotation import cooperation as C
from app.rotation.engine import ActionError
from test_rotation_engine import live_day, person, seat, two_lines, occupants


def fixture(posts=3):
    people = [person(p, '07:00', '21:00') for p in ('old1', 'old2', 'old3')]
    people += [person(p, '09:00', '21:00') for p in ('a', 'b')]
    eng, hooks = live_day(people, lines=[two_lines(posts)[0]])
    for i, pid in enumerate(('old1', 'old2', 'old3')[:posts]):
        seat(eng, pid, 'A', i, '08:00')
    eng.d['cooperativeEntry'] = True
    eng.P['a']['assign'] = {'line': 'A', 'mode': 'chain', 'target': 'old1', 'why': '推下班', 'departAt': 540}
    eng.P['b']['assign'] = {'line': 'A', 'mode': 'push', 'departAt': 540}
    return eng, hooks


def actor(pid):
    return SimpleNamespace(formal=True, entered_by=SimpleNamespace(employee_no=pid))


def test_projection_is_pure_and_shows_final_positions():
    eng, hooks = fixture()
    before = deepcopy(eng.d); events = len(hooks.events)
    view = C.plan(eng, eng.P['b'], 540)
    assert eng.d == before and len(hooks.events) == events
    routes = {r['pid']: r['to'] for r in view['routes']}
    assert routes['old1'] == '下班'
    assert routes['a'] == 'A1' and routes['b'] == 'A0'
    assert {m['pid']:m['entryPost'] for m in view['members']} == {'a':'A1','b':'A0'}
    assert {r['pid']: r['to'] for r in view['soloRoutes']}['old1'] == '下班'


def test_future_opening_projection_opens_only_the_clone_at_execution_time():
    eng,hooks=live_day([person('a','07:00','21:00')],lines=[two_lines(1)[0]])
    eng.act_post('A',0,False,540,'manager')
    eng.act_post('A',0,True,540,'manager',open_at=550)
    eng.P['a'].update(state='ready',readyAt=540,assign={'line':'A','mode':'opening','post':0,'departAt':550,'notBefore':550})
    before=deepcopy(eng.d);events=len(hooks.events)
    assert C.simulate(eng,[eng.P['a']],549)[0]['to']=='A0'
    assert eng.d==before and len(hooks.events)==events
    with pytest.raises(ActionError,match='加岗时间'):
        eng.act_depart('a',549,'screen')
    assert eng.d==before


def test_rescheduled_fixed_opening_is_blocked_without_changing_original_task():
    eng,_=live_day([person('a','07:00','21:00')],lines=[two_lines(1)[0]])
    eng.act_post('A',0,False,540,'manager')
    eng.act_post('A',0,True,540,'manager',open_at=543)
    eng.P['a'].update(state='ready',assign={'line':'A','mode':'opening','post':0,'departAt':543,'notBefore':543})
    eng.act_post('A',0,True,540,'manager',open_at=550)
    eng.tick(540)
    assert eng.P['a']['assign']['departAt']==543
    assert '加岗时间已变化' in eng.P['a']['assign']['blocked']
    before=deepcopy(eng.d)
    with pytest.raises(ActionError,match='加岗时间已变化'):
        eng.act_depart('a',550,'screen')
    assert eng.d==before


def test_first_vote_does_not_move_or_start_timers_and_second_executes_all():
    eng, _ = fixture(); before = occupants(eng, 'A')[:]
    out = C.confirm(eng, 'a', 540, actor('a'), 'a-token')
    assert out['status'] == 'waiting' and occupants(eng, 'A') == before
    assert eng.P['a']['state'] == 'ready' and not eng.P['a'].get('lineStart')
    assert C.plan(eng, eng.P['b'], 540)['confirmed'] == 1
    out = C.confirm(eng, 'b', 540, actor('b'), 'b-token')
    assert out['status'] == 'executed' and out['participants'] == ['a', 'b']
    assert eng.P['old1']['state'] == 'done'
    assert occupants(eng, 'A')[:2] == ['b', 'a']


def test_single_prioritizes_downshift_and_holds_missing_without_marking_absent():
    eng, _ = fixture()
    out = C.confirm(eng, 'b', 540, actor('b'), 'token', True, ['a'])
    assert out['single'] and eng.P['old1']['state'] == 'done'
    assert eng.P['b']['state'] == 'onpost'
    assert eng.P['a']['entryHold'] and eng.P['a']['state'] == 'ready'
    assert not eng.P['a']['absences'] and eng.P['a']['assign'] is None
    eng._assign_all(541)
    assert eng.P['a']['assign'] is None
    with pytest.raises(ActionError, match='已到场'):
        eng.act_depart('a', 541, actor('a'))


def test_peer_cannot_confirm_another_person():
    eng, _ = fixture()
    with pytest.raises(ActionError, match='本人'):
        C.confirm(eng, 'a', 540, actor('b'), 'token')


def test_confirmed_peer_cannot_be_reported_missing():
    eng, _ = fixture()
    C.confirm(eng, 'a', 540, actor('a'), 'token')
    with pytest.raises(ActionError, match='已确认到位'):
        C.confirm(eng, 'b', 540, actor('b'), 'token2', True, ['a'])


def test_changed_line_invalidates_previous_consents():
    eng, _ = fixture()
    C.confirm(eng, 'a', 540, actor('a'), 'token')
    eng.line('A')['posts'][1]['name'] = '新岗位'
    assert C.plan(eng, eng.P['b'], 540)['confirmed'] == 0
    assert C.confirm(eng, 'b', 540, actor('b'), 'token2')['status'] == 'waiting'


def test_rest_and_time_must_finish_before_confirming():
    eng, _ = fixture()
    eng.P['a'].update(state='rest', readyAt=550)
    with pytest.raises(ActionError, match='允许上岗'):
        C.confirm(eng, 'a', 540, actor('a'), 'token')


def test_no_fresh_entrant_is_immediately_pushed_out():
    eng, _ = fixture(posts=1)
    view = C.plan(eng, eng.P['b'], 540)
    assert view['blocked'] and not view['soloBlocked']
    with pytest.raises(ActionError, match='岗位不足'):
        C.confirm(eng, 'a', 540, actor('a'), 'token')
    assert C.confirm(eng, 'b', 540, actor('b'), 'token', True, ['a'])['single']


def test_other_lines_or_times_not_grouped():
    eng, _ = fixture()
    eng.P['b']['assign']['departAt'] += 1
    assert C.plan(eng, eng.P['a'], 540) is None


def test_three_person_cohort_requires_all_votes():
    eng, _ = fixture()
    eng.P['c'] = deepcopy(eng.P['b']);eng.P['c'].update(pid='c', name='c')
    assert len(C.plan(eng, eng.P['a'], 540)['members']) == 3
    assert C.confirm(eng, 'a', 540, actor('a'), 'a')['status'] == 'waiting'
    assert C.confirm(eng, 'b', 540, actor('b'), 'b')['status'] == 'waiting'
    assert C.confirm(eng, 'c', 540, actor('c'), 'c')['status'] == 'executed'
    assert set(occupants(eng, 'A')) == {'a', 'b', 'c'}


def test_board_group_confirmation_executes_all_and_cannot_be_used_by_member():
    eng, _=fixture()
    view=C.plan(eng,eng.P['a'],540)
    before=deepcopy(eng.d)
    with pytest.raises(ActionError,match='个人待办'):
        C.confirm_group(eng,'a',540,actor('a'),view['id'])
    assert eng.d==before
    out=C.confirm_group(eng,'a',540,SimpleNamespace(kind='supervisor'),view['id'])
    assert out['status']=='executed' and out['participants']==['a','b']
    assert occupants(eng,'A')[:2]==['b','a']


def test_board_group_rejects_early_rest_without_partial_movement():
    eng, _=fixture()
    eng.P['b'].update(state='rest',readyAt=550)
    view=C.plan(eng,eng.P['a'],540)
    before=deepcopy(eng.d)
    with pytest.raises(ActionError):
        C.confirm_group(eng,'a',540,SimpleNamespace(kind='supervisor'),view['id'])
    assert eng.d==before


def test_route_change_cancels_old_waiting_receipt():
    eng, _ = fixture()
    out=C.confirm(eng, 'a', 540, actor('a'), 'token')
    eng.d['confirmations']={'token':{'pid':'a','status':'waiting','group':out['group']}}
    eng.line('A')['posts'][0]['name']='换岗'
    assert C.reconcile(eng, 541)
    assert eng.d['confirmations']['token']['status']=='superseded'


def test_missing_one_of_three_recombines_without_moving_posts():
    eng, _ = fixture()
    eng.P['c']=deepcopy(eng.P['b']);eng.P['c'].update(pid='c',name='c')
    before=deepcopy(eng.d)
    preview=C.adjustment(eng,'b',540,['a'])
    assert eng.d==before and preview['jobs']['b']['mode']=='chain'
    assert C.apply_adjustment(eng,'b',540,actor('b'),['a'],preview['id'])['status']=='revised'
    assert occupants(eng,'A')==['old1','old2','old3']
    assert eng.P['a']['entryHold'] and C.plan(eng,eng.P['b'],540)['confirmed']==0
    assert C.confirm(eng,'b',540,actor('b'),'b')['status']=='waiting'
    assert C.confirm(eng,'c',540,actor('c'),'c')['status']=='executed'
    assert eng.P['old1']['state']=='done'


def test_upcoming_only_projects_last_ten_minutes_without_live_writes():
    eng, hooks = fixture()
    for pid in ['a', 'b']:
        eng.P[pid].update(state='notyet', readyAt=None, assign=None)
    before=deepcopy(eng.d); count=len(hooks.events)
    assert C.upcoming(eng, 529) == {}
    assert set(C.upcoming(eng, 530)) == {'a', 'b'}
    assert eng.d == before and len(hooks.events) == count


@pytest.mark.parametrize('start,now,expected', [
    (420,390,False),(420,419,False),(435,405,False),(435,434,False),
    (450,419.99,False),(450,420,True),(450,449.99,True),(450,450,False),
    (480,449.99,False),(480,450,True),(480,480,False),
])
def test_only_later_shifts_appear_within_thirty_minutes(start,now,expected):
    assert C.upcoming_shift({'state':'notyet','start':start},now) is expected
    assert not C.upcoming_shift({'state':'onpost','start':start},now)


@pytest.mark.parametrize('start', [420,435])
def test_opening_crews_never_receive_forecast_routes(start):
    eng,_=fixture()
    for pid in ('a','b'):
        eng.P[pid].update(state='notyet',start=start,readyAt=None,assign=None)
    before=deepcopy(eng.d)
    assert C.upcoming(eng,start-5)=={}
    assert eng.d==before


def test_excluded_and_opening_rest_keep_existing_rules():
    eng, _ = fixture()
    eng.P['a'].update(state='notyet', role='excluded', assign=None)
    assert 'a' not in C.upcoming(eng, 530)
    eng.P['b'].update(state='notyet', start=435, readyAt=None, assign=None)
    assert 'b' not in C.upcoming(eng, 430)


def test_returning_mixed_shift_relief_and_rotation_share_one_plan():
    eng, _ = fixture()
    eng.P['a']['visited'] = ['A']
    eng.P['b'].update(start=480, visited=['B'])
    view = C.plan(eng, eng.P['b'], 540)
    assert [m['pid'] for m in view['members']] == ['a', 'b']
    assert [m['task'] for m in view['members']] == ['推下班', '正常轮岗']
    before = deepcopy(eng.d)
    assert C.confirm(eng, 'a', 540, actor('a'), 'a')['status'] == 'waiting'
    assert occupants(eng, 'A') == [x['occ'] for x in before['lines'][0]['posts']]
    assert C.plan(eng, eng.P['b'], 541)['confirmed'] == 1
    assert C.confirm(eng, 'b', 541, actor('b'), 'b')['status'] == 'executed'
    assert eng.P['a']['lineStart'] == eng.P['b']['lineStart'] == 541
    assert eng.P['old1']['state'] == 'done'
    assert occupants(eng, 'A')[:2] == ['b', 'a']


def test_meal_member_stays_in_group_but_cannot_confirm_before_ready():
    eng, _ = fixture()
    eng.P['b'].update(state='meal', readyAt=550, visited=['A'])
    view = C.plan(eng, eng.P['a'], 540)
    assert len(view['members']) == 2
    assert C.confirm(eng, 'a', 540, actor('a'), 'a')['status'] == 'waiting'
    with pytest.raises(ActionError, match='允许上岗'):
        C.confirm(eng, 'b', 540, actor('b'), 'b')
    assert eng.P['old1']['state'] == 'onpost'


def test_op_and_returning_rotation_group_without_walk_delay():
    eng, _ = fixture()
    eng.S['walkMin'] = 99
    eng.line('A')['walk'] = 99
    eng.P['a'].update(role='op', visited=['A'])
    assert len(C.plan(eng, eng.P['b'], 540)['members']) == 2
    C.confirm(eng, 'a', 540, actor('a'), 'a')
    C.confirm(eng, 'b', 540, actor('b'), 'b')
    assert eng.P['a']['lineStart'] == eng.P['b']['lineStart'] == 540
    assert eng.P['a']['arriveAt'] is None


def test_route_explains_return_to_rest_room_without_starting_rest():
    eng, _ = fixture()
    view = C.plan(eng, eng.P['a'], 540)
    assert next(r for r in view['routes'] if r['pid'] == 'old3')['to'] == '下线，回休息室确认休息／吃饭'
    C.confirm(eng, 'a', 540, actor('a'), 'a')
    C.confirm(eng, 'b', 540, actor('b'), 'b')
    assert eng.P['old3']['state'] == 'walkback'
    assert eng.P['old3']['breakStart'] is None
