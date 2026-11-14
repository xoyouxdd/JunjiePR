"""Version 2 semantics; no application startup, config edits or service restart."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.rotation import engine as E, forecast as F, forecast_service as API, workflow as W
from test_rotation_engine import Hooks


def fixture(counts=(3, 3), now=600):
    people = {}
    lines = []
    for n, count in enumerate(counts):
        lid = chr(65+n)
        posts = []
        for i in range(count):
            pid = lid + str(i)
            people[pid] = dict(pid=pid, name=pid, start=435, end=1200, state='onpost', role='rotation',
                               flags=[], visited=[lid], line=lid, lineStart=435, continuousStart=420,
                               ate=False, absences=[], assign=None, readyAt=None, breakKind=None)
            posts.append(dict(name=pid, occ=pid, key=pid, open=True, opened=True, manual=False, since=435))
        lines.append(dict(id=lid, group=lid, active=True, standby=False, posts=posts, sortAt=435))
    for i in range(4):
        pid = 'new' + str(i)
        people[pid] = dict(pid=pid, name=pid, start=435, end=1200, state='rest', role='rotation', flags=[],
                           visited=[], line=None, lineStart=None, continuousStart=None, ate=False,
                           absences=[], assign=None, readyAt=now, breakKind='rest', breakStart=now-15,
                           forecastOrder=str(i), arriveAnchor=now-15)
    day = dict(date='2026-11-05', status='live', ruleVersion=2, persons=people, lines=lines,
               plan=dict(crew={}, seven={}, push7=[], lost=[]), confirmationEpoch='test', lastTick=now)
    eng = E.Engine(day, deepcopy(E.DEFAULT_SETTINGS), Hooks())
    W.initialize(eng)
    return eng


def occupants(e, lid='A'):
    return [x.get('occ') for x in e.line(lid)['posts']]


def test_sequential_forecasts_use_head_time_then_active_count_without_mutating_positions():
    e = fixture((4, 3))
    before = deepcopy(e.d['lines'])
    F.plan(e, 600)
    assert [e.P['new'+str(i)]['assign']['line'] for i in range(2)] == ['A', 'B']
    assert e.d['lines'] == before
    e.line('B')['sortAt'] = 430
    F.plan(e, 600)
    assert e.P['new0']['assign']['line'] == 'B'
    assert e.P['new0']['readyAt'] == 600


def test_ordinary_push_uses_position_not_longest_and_records_real_head_time():
    e = fixture()
    e.P['A0']['continuousStart'] = 0
    F.plan(e, 600)
    assert F.execute(e, 'new0', 600, 'test', 'A')['executed']
    assert occupants(e) == ['new0', 'A0', 'A1']
    assert e.P['A2']['state'] == 'pending'
    assert e.line('A')['sortAt'] == 600
    assert e.P['new1']['state'] == 'rest'


def test_changed_line_does_not_move_anyone_until_second_confirmation():
    e = fixture()
    F.plan(e, 600)
    old = e.P['new0']['assign']['line']
    e.line('B')['sortAt'] = 1
    before = occupants(e)
    result = F.execute(e, 'new0', 600, 'test', old)
    assert result['line_changed'] and result['line'] == 'B'
    assert occupants(e) == before and e.P['new0']['state'] == 'rest'
    F.execute(e, 'new0', 600, 'test', 'B')
    assert e.P['new0']['state'] == 'onpost'


def test_vacancy_stops_push_without_ejecting_last_person():
    e = fixture()
    e.line('A')['posts'][1]['occ'] = None
    F.pool(e, 'A1', 'other', 600, 'test')
    F.execute(e, 'new0', 600, 'test', 'A')
    assert occupants(e) == ['new0', 'A0', 'A2']
    assert e.P['A2']['state'] == 'onpost'


@pytest.mark.parametrize('kind', ['sick', 'off', 'out'])
@pytest.mark.parametrize('index', [1, 2])
def test_targeted_prefix_push_updates_head_only_when_target_is_last(kind, index):
    e = fixture()
    p = e.P['A'+str(index)]
    if kind == 'off':
        p.update(start=430, end=620)
    else:
        p['requestedExit'] = kind
    F.plan(e, 600)
    a = e.P['new0']['assign']
    assert a['kind'] == kind and a['target'] == p['pid']
    F.execute(e, 'new0', 600, 'test', 'A')
    assert p['state'] == {'sick': 'sick', 'off': 'done', 'out': 'away'}[kind]
    assert e.line('A')['sortAt'] == (600 if index == 2 else 435)


@pytest.mark.parametrize('index,expected,sort_time', [
    (1, ['new0', 'A0', 'A2'], 435),
    (2, ['new0', 'A0', 'A1'], 600),
])
def test_push7_chains_to_actual_worker_and_updates_sort_time_only_at_tail(index, expected, sort_time):
    e = fixture()
    target = e.P[f'A{index}']
    target.update(start=420, flags=['7点岗', '送失物'])
    F.plan(e, 600)
    task = F.task_view(e, e.P['new0'], 600)
    assert task['mode'] == 'chain' and task['push7Route']
    assert F.execute(e, 'new0', 600, 'test', 'A', task['push7Route'])['executed']
    assert occupants(e) == expected
    assert target['state'] == 'pending' and target['flags'] == ['送失物'] and target['sevenReplaced']
    assert e.line('A')['sortAt'] == sort_time
    assert F.push7_history(e)['new0'] == (1, e.d['date'], 600)


def test_priority_sick_then_push7_then_earliest_shift_and_later_post():
    e = fixture((4, 4))
    e.P['A0']['requestedExit'] = 'sick'
    e.P['A1'].update(start=420, flags=['7点岗'])
    e.P['A2'].update(start=415, end=620)
    e.P['B3'].update(start=415, end=620)
    F.plan(e, 600)
    assert [e.P['new'+str(i)]['assign']['kind'] for i in range(4)] == ['sick', 'push7', 'off', 'off']
    assert e.P['new2']['assign']['target'] == 'B3'


def test_timeout_removes_only_unconfirmed_person_and_refreshes_forecasts():
    e = fixture()
    e.P['new1']['readyAt'] = 601
    e.P['new2']['readyAt'] = e.P['new3']['readyAt'] = 610
    F.tick(e, 603)
    assert e.P['new0']['state'] == 'exception' and e.P['new0']['assign'] is None
    assert e.P['new1']['assign'] and e.P['new1']['state'] == 'rest'
    with pytest.raises(E.ActionError):
        F.execute(e, 'new0', 603, 'test', 'A')
    F.manual_place(e, 'new0', 'A', 0, 'push', 604, 'screen')
    assert e.P['new0']['state'] == 'onpost' and not e.P['new0'].get('exception')


def test_pool_return_and_remaining_time_and_repeat_meal_confirmation():
    e = fixture()
    e.S['mealReserveMinPeople'] = 1
    F.pool(e, 'new0', 'away', 600, 'test')
    assert e.P['new0']['assign'] is None
    F.pool(e, 'new0', 'rest', 601, 'test', remaining=15)
    assert e.P['new0']['readyAt'] == 616
    F.pool(e, 'new0', 'meal', 602, 'test')
    assert e.P['new0']['readyAt'] == 601 + e.S['mealMin']
    with pytest.raises(E.ActionError, match='已进过'):
        F.pool(e, 'new0', 'meal', 603, 'test')
    F.pool(e, 'new0', 'meal', 603, 'test', repeat_meal=True)


def test_all_closed_pauses_timeouts_and_manual_other_does_not_auto_restore():
    e = fixture()
    F.pool(e, 'new1', 'other', 600, 'test')
    for L in e.d['lines']:
        L['active'] = False
    F.tick(e, 600)
    F.tick(e, 620)
    assert not e.P['new0'].get('exception')
    e.line('A')['active'] = True
    F.tick(e, 621)
    assert e.P['new0']['timeoutAt'] == 624
    assert e.P['new1']['state'] == 'other' and e.P['new1']['assign'] is None


def test_version2_cross_midnight_does_not_end_at_24_and_legacy_still_does():
    e = fixture()
    for p in e.P.values():
        p['end'] = 1500
    assert E.rotation_end(e.d) == 1500
    F.tick(e, 1440)
    assert e.d['status'] == 'live'
    F.tick(e, 1500)
    assert e.d['status'] == 'ended'
    old = deepcopy(e.d)
    old.pop('ruleVersion')
    assert E.rotation_end(old) == 1440


def test_tie_order_is_stable_and_seconds_are_not_rounded_to_minutes():
    e = fixture()
    e.P['new0']['readyAt'] = 600 + 20/60
    e.P['new1']['readyAt'] = 600 + 10/60
    F.plan(e, 600)
    assert e.P['new1']['assign']['line'] == 'A'
    first = deepcopy([p.get('assign') for p in e.P.values()])
    F.plan(e, 600)
    assert first == [p.get('assign') for p in e.P.values()]


def test_multiple_same_line_forecasts_are_independent_and_replan_after_first_execution():
    e = fixture((3,))
    F.plan(e, 600)
    assert e.P['new0']['assign']['batchSize'] == 4
    F.execute(e, 'new1', 600, 'test', 'A')
    assert occupants(e) == ['new1', 'A0', 'A1']
    assert e.P['new0']['state'] == 'rest' and e.P['new0']['assign']['batchSize'] == 3
    F.execute(e, 'new0', 600, 'test', 'A')
    assert occupants(e) == ['new0', 'new1', 'A0']


@pytest.mark.parametrize('sick', [False, True])
def test_each_added_post_preserves_header_even_after_higher_priority_work(sick):
    e = fixture((4,))
    line = e.line('A')
    for i in (2, 3):
        line['posts'][i].update(occ=None, openRequest=True)
        e.P[f'A{i}'].update(state='other', line=None, manualPaused=True)
    if sick:
        e.P['A0']['requestedExit'] = 'sick'
    F.plan(e, 600)
    assert [e.P[f'new{i}']['assign']['kind'] for i in range(2 + sick)] == ['sick'] * sick + ['opening', 'opening']
    if sick:
        F.execute(e, 'new0', 600, 'test', 'A')
        assert sum(bool(p.get('openRequest')) for p in line['posts']) == 2
    for i in range(2):
        pid = f'new{i + sick}'
        assert e.P[pid]['assign']['kind'] == 'opening'
        assert F.execute(e, pid, 600, 'test', 'A', expected_kind='opening')['executed']
        assert line['sortAt'] == 435
        assert sum(bool(p.get('openRequest')) for p in line['posts']) == 1 - i
    assert all(occupants(e))


def test_relief_moves_the_pending_addition_if_it_relocates_its_vacancy():
    e = fixture((3,))
    line = e.line('A')
    line['posts'][1].update(occ=None, openRequest=True)
    e.P['A1'].update(state='other', line=None, manualPaused=True)
    e.P['A2']['requestedExit'] = 'sick'
    F.plan(e, 600)
    assert e.P['new1']['assign']['kind'] == 'opening'
    F.execute(e, 'new0', 600, 'test', 'A')
    assert occupants(e) == ['new0', 'A0', None]
    assert [p['key'] for p in line['posts'] if p.get('openRequest')] == ['A2']
    assert line['sortAt'] == 600  # The last-post relief has its own clock rule.
    assert F.execute(e, 'new1', 601, 'test', 'A', expected_kind='opening')['executed']
    assert line['sortAt'] == 600 and all(occupants(e))


def test_manual_push_filling_an_addition_ignores_header_opt_in():
    e = fixture((3,))
    line = e.line('A')
    line['posts'][2].update(occ=None, openRequest=True)
    e.P['A2'].update(state='other', line=None, manualPaused=True)
    F.manual_place(e, 'new0', 'A', 0, 'push', 600, 'test', update_sort_time=True)
    assert occupants(e) == ['new0', 'A0', 'A1']
    assert line['sortAt'] == 435 and not line['posts'][2]['openRequest']


def test_same_shift_end_time_uses_later_post_but_earlier_shift_beats_position():
    e = fixture((4, 4))
    e.P['A1'].update(start=430, end=610)
    e.P['A3'].update(start=430, end=615)
    e.P['B3'].update(start=430, end=615)
    F.plan(e, 600)
    assert e.P['new0']['assign']['target'] == 'A1'
    assert e.P['new1']['assign']['target'] in {'A3', 'B3'}


def test_manual_middle_placement_keeps_head_clock_and_crossline_swap_updates_correct_lines():
    e = fixture()
    F.manual_place(e, 'new0', 'A', 1, 'swap', 600, 'screen')
    assert occupants(e) == ['A0', 'new0', 'A2']
    assert e.line('A')['sortAt'] == 435 and e.P['A1']['state'] == 'pending'
    F.manual_place(e, 'A0', 'B', 1, 'swap', 601, 'screen', update_sort_time=True)
    assert occupants(e)[0] == 'B1' and occupants(e, 'B')[1] == 'A0'
    assert e.P['B1']['line'] == 'A' and e.P['A0']['line'] == 'B'
    assert e.P['B1']['continuousStart'] == 420 and e.P['A0']['continuousStart'] == 420
    assert e.line('A')['sortAt'] == 601 and e.line('B')['sortAt'] == 435


def test_same_line_swapping_head_into_middle_updates_clock_when_requested():
    e = fixture()
    F.manual_place(e, 'A0', 'A', 1, 'swap', 600, 'screen', update_sort_time=True)
    assert occupants(e) == ['A1', 'A0', 'A2']
    assert e.line('A')['sortAt'] == 600


def test_manual_push7_exchange_clears_badges_but_is_not_counted_as_a_chain():
    e = fixture()
    e.P['A1'].update(start=420, flags=['7点岗', '送失物'])
    e.P['new0']['flags'] = ['推7点']
    e.P['new0']['exception'] = {'at': 603, 'assignment': {'kind': 'push7', 'target': 'A1'}}
    e.P['new0']['state'] = 'exception'
    F.manual_place(e, 'new0', 'A', 1, 'swap', 604, 'screen')
    assert e.P['A1']['flags'] == ['送失物'] and e.P['new0']['flags'] == []
    assert not e.P['new0'].get('exception')
    assert 'new0' not in F.push7_history(e)


def test_manual_recovery_from_head_chains_to_the_original_middle_target_once():
    e = fixture()
    e.P['A1'].update(start=420, flags=['7点岗'])
    e.P['new0'].update(state='exception', exception={'at': 603, 'assignment': {
        'kind': 'push7', 'line': 'A', 'target': 'A1'}})
    F.manual_place(e, 'new0', 'A', 0, 'push', 604, 'manager', update_sort_time=True)
    assert occupants(e) == ['new0', 'A0', 'A2']
    assert e.P['A1']['state'] == 'pending'
    assert e.line('A')['sortAt'] == 435
    assert F.push7_history(e)['new0'] == (1, e.d['date'], 604)


def test_push7_history_ranks_only_currently_ready_people_and_protects_the_assigned_target():
    e = fixture()
    e.P['A2'].update(start=420, flags=['7点岗'])
    e.P['new0']['readyAt'] = 599
    e.P['new3']['readyAt'] = 601
    e.d['push7History'] = {'new0': [4, '2026-11-01', 435],
                          'new1': [2, '2026-11-02', 435],
                          'new2': [2, '2026-11-01', 435]}
    F.plan(e, 600)
    assert e.P['new2']['assign']['kind'] == 'push7'
    assert e.P['new0']['assign']['kind'] != 'push7'
    a, _ = F.confirmation_plan(e, 'new0', 600)
    assert a['line'] == 'B' and a['kind'] == 'push'
    assert F.execute(e, 'new2', 600, 'test', 'A')['executed']
    assert F.push7_history(e)['new2'] == (3, e.d['date'], 600)


def test_push7_updates_same_line_route_before_any_execution():
    e = fixture((4,))
    e.P['A1'].update(start=420, flags=['7点岗'])
    F.plan(e, 600)
    original = F.task_view(e, e.P['new0'], 600)
    e.line('A')['posts'][1]['occ'], e.line('A')['posts'][2]['occ'] = 'A2', 'A1'
    result = F.execute(e, 'new0', 600, 'test', 'A', original['push7Route'])
    assert result['line_changed'] and result['task']['line'] == 'A'
    assert occupants(e) == ['A0', 'A2', 'A1', 'A3']
    assert F.push7_history(e) == {}
    assert F.execute(e, 'new0', 600, 'test', 'A', result['task']['push7Route'])['executed']
    assert occupants(e) == ['new0', 'A0', 'A2', 'A3']


def test_push7_timeout_gives_next_ready_candidate_the_route_without_recording_a_completion():
    e = fixture((3,))
    e.P['A2'].update(start=420, flags=['7点岗'])
    e.P['new1']['readyAt'] = 602
    e.P['new2']['state'] = e.P['new3']['state'] = 'pending'
    F.tick(e, 600)
    assert e.P['new0']['assign']['kind'] == 'push7'
    F.tick(e, 603)
    assert e.P['new0']['state'] == 'exception'
    assert e.P['new1']['assign']['kind'] == 'push7'
    assert occupants(e) == ['A0', 'A1', 'A2'] and F.push7_history(e) == {}
    assert F.execute(e, 'new1', 603, 'test', 'A')['executed']
    assert F.push7_history(e) == {'new1': (1, e.d['date'], 603)}


def test_first_prearranged_person_is_never_shifted_before_their_start():
    e = fixture()
    e.P['A0'].update(state='notyet', start=700, line=None)
    F.plan(e, 600)
    F.execute(e, 'new0', 600, 'test', 'A')
    assert e.P['A0']['state'] == 'notyet' and e.P['A0']['line'] is None
    assert occupants(e) == ['new0', 'A1', 'A2']


def test_holds_are_skipped_and_cannot_be_dragged_until_released():
    e = fixture()
    e.line('A')['posts'][1]['hold'] = {'pid': 'A1', 'reason': '现场任务', 'since': 500}
    e.line('B')['active'] = False
    F.plan(e, 600)
    F.execute(e, 'new0', 600, 'test', 'A')
    assert occupants(e) == ['new0', 'A1', 'A0']
    with pytest.raises(E.ActionError, match='解除定岗'):
        F.manual_place(e, 'A1', 'A', 0, 'swap', 601, 'screen')


def test_restored_future_and_sick_person_can_be_manually_placed_without_changing_shift():
    e = fixture()
    e.P['new0'].update(state='notyet', start=700)
    F.manual_place(e, 'new0', 'A', 0, 'swap', 600, 'screen')
    assert e.P['new0']['state'] == 'onpost' and e.P['new0']['start'] == 700
    e.P['new1']['state'] = 'sick'
    F.pool(e, 'new1', 'rest', 600, 'test')
    assert e.P['new1']['state'] == 'rest' and e.P['new1']['assign']


def test_scheduled_opening_is_forecast_but_cannot_execute_early_or_timeout_while_closed():
    e = fixture((1,))
    e.line('A')['posts'][0].update(open=False, opened=False, openAt='10:30', manual=False, occ=None)
    F.tick(e, 600)
    assert e.P['new0']['assign']['notBefore'] == 630
    assert F.task_view(e, e.P['new0'], 600)['allowedAt'] == 630
    with pytest.raises(E.ActionError, match='计划加岗'):
        F.execute(e, 'new0', 600, 'test', 'A')
    F.tick(e, 620)
    assert e.P['new0']['state'] == 'rest'
    F.tick(e, 630)
    assert e.P['new0']['timeoutAt'] == 633
    F.execute(e, 'new0', 630, 'test', 'A')
    assert occupants(e) == ['new0'] and not e.line('A').get('adding')
    assert e.line('A')['sortAt'] == 435 and not e.line('A')['posts'][0]['openRequest']
