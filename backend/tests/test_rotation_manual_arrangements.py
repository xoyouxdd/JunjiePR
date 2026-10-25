"""现场调整的计时、预排和强制餐休中断边界。"""
from copy import deepcopy

import pytest

from app.rotation.engine import ActionError
from test_rotation_engine import live_day, person, seat, two_lines, occupants


def prestart():
    eng, hooks = live_day([person(p, '10:00', '21:00') for p in ('a', 'b', 'c')],
                          lines=[two_lines()[0]])
    for i, pid in enumerate(('a', 'b', 'c')):
        eng.line('A')['posts'][i].update(occ=pid, since=None)
        eng.d['plan']['crew'][f'A#{i}'] = pid
    return eng, hooks


def test_preopening_push_moves_plan_without_starting_timers():
    eng, hooks = prestart()
    eng.act_post('A', 0, False, 540, 'operator', close_mode='push')
    assert occupants(eng, 'A') == [None, 'a', 'b']
    assert eng.d['plan']['crew'] == {'A#1': 'a', 'A#2': 'b'}
    assert all(p['state'] == 'notyet' and p.get('lineStart') is None for p in eng.P.values())
    assert all(post['since'] is None for post in eng.line('A')['posts'])
    assert hooks.segments == []
    eng.tick(600)
    assert occupants(eng, 'A') == [None, 'a', 'b']
    assert eng.P['a']['state'] == eng.P['b']['state'] == 'onpost'
    assert eng.P['c']['state'] != 'onpost'


def test_sick_leave_clears_preplanned_post_and_does_not_return_at_start():
    eng, hooks = prestart()
    eng.act_leave('a', '病假', 540, 'operator')
    assert occupants(eng, 'A')[0] is None
    assert 'a' not in eng.d['plan']['crew'].values()
    eng.tick(600)
    assert eng.P['a']['state'] == 'done' and 'a' not in occupants(eng, 'A')
    assert hooks.segments == []


@pytest.mark.parametrize('state', ['op', 'notyet'])
def test_op_can_manually_start_rest_before_opening(state):
    eng, _ = live_day([person('a', '07:00', '21:00')], lines=[two_lines()[0]])
    eng.P['a'].update(role='op', state=state, assign=None, arriveAnchor=None)
    eng.act_arrive('a', 400, 'operator', meal_choice='rest')
    assert eng.P['a']['state'] == 'rest'
    assert eng.P['a']['breakStart'] == 400
    assert eng.P['a']['readyAt'] == 400 + eng.S['breakMin']


@pytest.mark.parametrize('state,kind', [('rest','rest'), ('meal','meal'), ('rest','meal_rest')])
def test_force_interrupts_break_and_replaced_person_waits_for_arrival(state, kind):
    eng, hooks = live_day([person(p, '07:00', '21:00') for p in ('a', 'old')], lines=[two_lines()[0]])
    seat(eng, 'old', 'A', 0, '08:00')
    eng.P['a'].update(state=state, breakKind=kind, breakStart=530, readyAt=570,
                      assign=None, ate=kind != 'rest')
    eng.act_move('a', 'A', 0, 540, 'operator', force=True, reason='现场应急')
    assert occupants(eng, 'A')[0] == 'a' and eng.P['a']['lineStart'] == 540
    assert eng.P['old']['state'] == 'walkback'
    assert eng.P['old'].get('breakStart') is None
    assert eng.P['a']['breakKind'] is None and eng.P['a']['readyAt'] is None
    assert eng.P['a']['interruptedBreak']['remaining'] == 30
    assert eng.P['a']['interruptedBreak']['kind'] == kind
    assert hooks.events[-1]['type'] == 'force_place'
    assert hooks.events[-1]['detail']['replaced'] == 'old'
    assert hooks.segments[-1] == ('old', 'A', 480, 540)


def test_force_requires_reason_and_cannot_bypass_held_target():
    eng, _ = live_day([person(p, '07:00', '21:00') for p in ('a', 'old')], lines=[two_lines()[0]])
    seat(eng, 'old', 'A', 0, '08:00')
    eng.P['a'].update(state='meal', breakKind='meal', readyAt=570)
    before=deepcopy(eng.d)
    with pytest.raises(ActionError, match='原因'):
        eng.act_move('a', 'A', 0, 540, 'operator', force=True)
    assert eng.d == before
    eng.line('A')['posts'][0]['hold']={'pid':'old', 'since':500, 'reason':''}
    before=deepcopy(eng.d)
    with pytest.raises(ActionError, match='定岗'):
        eng.act_move('a', 'A', 0, 540, 'operator', force=True, reason='应急')
    assert eng.d == before


@pytest.mark.parametrize('mode,expected', [('posts',['a','b',None]), ('with_people',['b','a',None])])
def test_post_swap_modes_preserve_person_line_time(mode, expected):
    eng, hooks=live_day([person(p,'07:00','21:00') for p in ('a','b')], lines=[two_lines()[0]])
    seat(eng,'a','A',0,'08:00');seat(eng,'b','A',1,'08:10')
    before=len(hooks.segments)
    eng.act_post_order('A',0,1,540,'operator',swap_mode=mode)
    assert occupants(eng,'A') == expected
    assert [x['name'] for x in eng.line('A')['posts']][:2] == ['A1','A0']
    assert eng.P['a']['lineStart'] == 480 and eng.P['b']['lineStart'] == 490
    assert len(hooks.segments) == before
