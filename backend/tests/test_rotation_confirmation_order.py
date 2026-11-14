"""Actual confirmation uses actual headers, independent of provisional reservations."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.rotation import forecast as F
from app.rotation import service as S
from test_rotation_forecast import fixture, occupants
from test_rotation_forecast_api import rotation, current, board, act, person, DAY


def test_later_candidate_confirming_first_uses_actual_oldest_header():
    e = fixture()
    e.line('B')['sortAt'] = 450
    F.plan(e, 600)
    assert e.P['new0']['assign']['line'] == 'A'
    assert e.P['new1']['assign']['line'] == 'B'
    previews = {pid: deepcopy(p.get('assign')) for pid, p in e.P.items()}
    posts = deepcopy(e.d['lines'])
    result = F.execute(e, 'new1', 600, 'test', 'B')
    assert result.get('line_changed') and result['line'] == 'A'
    assert result['waiting_predecessors'] == [{'pid': 'new0', 'name': 'new0', 'line': 'A'}]
    assert e.d['lines'] == posts
    assert {pid: p.get('assign') for pid, p in e.P.items()} == previews
    assert e.P['new1']['readyAt'] == 600
    assert F.execute(e, 'new1', 600, 'test', 'A')['executed']
    assert occupants(e)[0] == 'new1'
    assert e.line('A')['sortAt'] == 600 and e.line('B')['sortAt'] == 450
    assert e.P['new0']['assign']['line'] == 'B'


def test_header_edit_reroute_does_not_blame_unconfirmed_people():
    e = fixture()
    F.plan(e, 600)
    assert e.P['new0']['assign']['line'] == 'A'
    e.line('B')['sortAt'] = 430
    result = F.execute(e, 'new0', 600, 'test', 'A')
    assert result['line_changed'] and result['line'] == 'B'
    assert result['waiting_predecessors'] == []
    assert occupants(e, 'B')[0] == 'B0'


def test_exception_person_is_never_named_as_waiting_predecessor():
    e = fixture()
    e.P['new0']['state'] = 'exception'
    F.plan(e, 600)
    assert e.P['new1']['assign']['line'] == 'A'
    assert e.P['new2']['assign']['line'] == 'B'
    result = F.execute(e, 'new2', 600, 'test', 'B')
    assert [p['pid'] for p in result['waiting_predecessors']] == ['new1']
    assert F.execute(e, 'new2', 600, 'test', 'A')['executed']
    assert e.P['new0']['state'] == 'exception'


def test_waiting_predecessors_follow_second_and_tie_order():
    e = fixture()
    for p in e.P.values():
        if p['pid'].startswith('new'):
            p['readyAt'] = 600 + int(p['pid'][-1])/60
    F.plan(e, 600 + 2/60)
    result = F.execute(e, 'new1', 600 + 2/60, 'test', 'B')
    assert result['line_changed']
    assert [p['pid'] for p in result['waiting_predecessors']] == ['new0']
    assert e.P['new1']['readyAt'] == 600 + 1/60


@pytest.mark.parametrize('counts,headers,winner', [
    ((4, 5), (435, 435), 'B'),
    ((4, 5), (435, 435 + 1/60), 'A'),
    ((5, 4), (435 + 1/60, 435), 'B'),
])
def test_confirmation_time_then_participating_post_count(counts, headers, winner):
    e = fixture(counts)
    for line, header in zip(e.d['lines'], headers): line['sortAt'] = header
    F.plan(e, 600)
    other = 'A' if winner == 'B' else 'B'
    result = F.execute(e, 'new3', 600, 'test', other)
    assert result.get('line_changed') and result['line'] == winner
    assert F.execute(e, 'new3', 600, 'test', winner)['executed']
    assert occupants(e, winner)[0] == 'new3'


@pytest.mark.parametrize('kind,outcome', [('sick', 'sick'), ('off', 'done'), ('out', 'away')])
def test_unconfirmed_forecasts_do_not_reserve_special_relief(kind, outcome):
    e = fixture()
    e.line('B')['sortAt'] = 590
    if kind == 'off': e.P['B1'].update(start=430, end=610)
    else: e.P['B1']['requestedExit'] = 'sick' if kind == 'sick' else 'out'
    F.plan(e, 600)
    assert e.P['new0']['assign']['target'] == 'B1'
    assert e.P['new1']['assign']['line'] == 'A'
    result = F.execute(e, 'new1', 600, 'test', 'A')
    assert result.get('line_changed') and result['line'] == 'B'
    assert e.P['B1']['state'] == 'onpost'
    assert F.execute(e, 'new1', 600, 'test', 'B')['executed']
    assert e.P['B1']['state'] == outcome
    assert e.P['new0']['state'] == 'rest'


def test_closed_lines_and_nonparticipating_posts_do_not_win_confirmation():
    e = fixture((6, 3))
    e.line('A')['sortAt'] = 0
    e.line('A')['active'] = False
    F.plan(e, 600)
    assert F.execute(e, 'new2', 600, 'test', 'B')['executed']
    assert occupants(e, 'B')[0] == 'new2'


def _second_person(r):
    act(r, {'action': 'add_person', 'temporary': True, 'name': '后确认伙伴', 'start': '00:00', 'end': '20:00'})
    pid = next(p['pid'] for p in board(r)['persons'] if p['name'] == '后确认伙伴')
    act(r, {'action': 'pool', 'pid': pid, 'pool': 'rest'})
    act(r, {'action': 'break_time', 'pid': pid, 'remaining': 0})
    with r.factory() as db, S.transaction(db):
        row = S.day_row(db, r.attraction_id, DAY)
        state = S.day_state(row)
        state['persons'][pid]['forecastOrder'] = 'zz-last'
        eng, _ = S.engine_for(db, S.load_config(db, r.attraction_id), row, state)
        F.plan(eng, 600)
        S.save_day(db, row, state)
    return pid


def test_api_reroutes_later_person_without_moving_until_reconfirmed(current):
    r = current
    pid = _second_person(r)
    act(r, {'action': 'sort_time', 'line': 'B', 'time': '07:30:00'})
    assert person(r, '5')['assign']['line'] == 'A'
    p = person(r, pid)
    assert p['assign']['line'] == 'B'
    token = p['confirmationTokens']['depart']
    before = board(r)['lines']
    changed = act(r, {'action': 'depart', 'pid': pid, 'expected_line': 'B', 'confirmation_token': token})
    assert changed.get('line_changed') and changed['line'] == 'A'
    assert changed['waiting_predecessors'] == [{'pid': '5', 'name': person(r, '5')['name'], 'line': 'A'}]
    assert board(r)['lines'] == before and person(r, pid)['state'] == 'rest'
    assert person(r, pid)['confirmationTokens']['depart'] == token
    body = {'action': 'depart', 'pid': pid, 'expected_line': 'A', 'confirmation_token': token}
    assert act(r, body)['executed']
    assert act(r, body)['duplicate']
    assert person(r, '5')['assign']['line'] == 'B'


def test_simultaneous_people_compare_headers_after_each_committed_execution(current):
    r = current
    extra = _second_person(r)
    bodies = []
    for pid in ('5', extra):
        p = person(r, pid)
        bodies.append({'action': 'depart', 'pid': pid, 'expected_line': 'A',
                       'confirmation_token': p['confirmationTokens']['depart']})
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda body: act(r, body), bodies))
    assert sum(bool(out.get('executed')) for out in results) == 1
    pending = next((body, out) for body, out in zip(bodies, results) if out.get('line_changed'))
    body, out = pending
    assert out['line'] == 'B'
    body = {**body, 'expected_line': 'B'}
    assert act(r, body)['executed']
    assert act(r, body)['duplicate']
    day = board(r)
    assert {line['posts'][0]['occ'] for line in day['lines']} == {'5', extra}
