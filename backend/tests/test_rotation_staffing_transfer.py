from copy import deepcopy

import pytest

from app.rotation import forecast as F, service as S
from app.rotation.engine import ActionError
from app.rotation.post_transfer import transfer
from app.rotation.staffing import warnings
from test_rotation_forecast import fixture
from test_rotation_forecast_api import current, rotation, board, act, person


def test_five_and_three_become_four_and_four_without_changing_clocks_or_heads():
    eng = fixture((5, 3))
    before = deepcopy(eng.d)
    post = eng.line('A')['posts'][2]
    worker = eng.P[post['occ']]
    worker['entrySortAt'] = 450
    before_worker = deepcopy(worker)
    eng.d['plan']['crew'] = {'A#2': worker['pid'], 'A#4': 'A4', 'B#1': 'B1'}
    transfer(eng, {'line': 'A', 'post_key': post['key'], 'target_line': 'B', 'target_i': 1}, 600, 'test')
    assert [len(line['posts']) for line in eng.d['lines']] == [4, 4]
    assert eng.line('B')['posts'][1] is post
    assert post == before['lines'][0]['posts'][2]
    for field in ('lineStart', 'continuousStart', 'entrySortAt', 'state', 'readyAt'):
        assert worker[field] == before_worker[field]
    assert worker['line'] == 'B'
    assert [line['sortAt'] for line in eng.d['lines']] == [435, 435]
    assert eng.d['plan']['crew'] == {'A#3': 'A4', 'B#1': 'A2', 'B#2': 'B1'}
    assert sorted(p['occ'] for line in eng.d['lines'] for p in line['posts']) == sorted(
        p['occ'] for line in before['lines'] for p in line['posts'])


@pytest.mark.parametrize('position', [0, 3])
def test_insert_at_head_or_tail_and_accounting_does_not_double_count(position):
    eng = fixture((5, 3))
    segments = []
    eng.h.segment = lambda *args: segments.append(args)
    post = eng.line('A')['posts'][0]
    transfer(eng, {'line': 'A', 'post_key': post['key'], 'target_line': 'B', 'target_i': position}, 600, 'test')
    worker = eng.P['A0']
    assert eng.line('B')['posts'][position]['occ'] == 'A0'
    F.remove(eng, worker, 615)
    assert segments == [('A0', 'A', 435, 600), ('A0', 'B', 600, 615)]
    assert 'lineSegmentStart' not in worker


@pytest.mark.parametrize('case', ['same_line', 'inactive', 'bad_index', 'boolean_index', 'held', 'mode101', 'duplicate', 'last_post', 'opening'])
def test_invalid_transfer_is_rejected_without_partial_mutation(case):
    eng = fixture((1 if case == 'last_post' else 5, 3))
    post = eng.line('A')['posts'][0]
    body = {'line': 'A', 'post_key': post['key'], 'target_line': 'B', 'target_i': 1}
    if case == 'same_line': body['target_line'] = 'A'
    if case == 'inactive': eng.line('B')['active'] = False
    if case == 'bad_index': body['target_i'] = 4
    if case == 'boolean_index': body['target_i'] = True
    if case == 'held': post['hold'] = {'pid': post['occ']}
    if case == 'opening': post['openRequest'] = True
    if case == 'mode101': eng.line('B')['posts'][0]['mode101'] = {'phase': 'return'}
    if case == 'duplicate': eng.line('B')['posts'][0]['name'] = post['name']
    before = deepcopy(eng.d)
    with pytest.raises(ActionError): transfer(eng, body, 600, 'test')
    assert eng.d == before


def test_warning_uses_shift_end_and_confirmed_breaks_and_does_not_mutate_state():
    eng = fixture((5, 3), now=1140)
    for p in eng.P.values(): p['end'] = 1300
    eng.P['A0']['end'] = eng.P['B0']['end'] = 1200
    for i in range(4):
        eng.P[f'new{i}'].update(state='pending', readyAt=None)
    before = deepcopy(eng.d)
    result = warnings(eng, 1140)
    point = next(item for item in result if item['at'] == 1200)
    assert point['requiredPosts'] == 8 and point['gap'] == 2
    assert {p['pid'] for p in point['leaving']} == {'A0', 'B0'}
    assert len(point['uncertain']) == 4
    assert eng.d == before
    eng.P['new0'].update(state='rest', readyAt=1199)
    assert next(p for p in warnings(eng, 1140) if p['at'] == 1200)['gap'] == 1


def test_future_unsigned_shift_is_not_guaranteed_supply_and_fixed_workers_are_counted():
    eng = fixture((3,), now=1140)
    for p in eng.P.values(): p['end'] = 1300
    eng.P['A0']['end'] = 1200
    for i in range(4): eng.P[f'new{i}'].update(state='other', manualPaused=True)
    eng.P['new0'].update(state='notyet', manualPaused=False, start=1200)
    eng.line('A')['posts'][1]['hold'] = {'pid': 'A1'}
    point = next(p for p in warnings(eng, 1140) if p['at'] == 1200)
    assert point['gap'] == 1 and point['available'] == 2
    assert point['uncertain'][0]['reason'] == '未签到'
    eng.P['new0']['batchCheckedInFor'] = 1200
    assert next(p for p in warnings(eng, 1140) if p['at'] == 1200)['gap'] == 0
    eng.d['status'] = 'ended'
    assert warnings(eng, 1140) == []


def test_preopening_prearrangement_is_not_reported_as_an_operating_shortage():
    eng = fixture((5, 3), now=240)
    for p in eng.P.values():
        if p['state'] == 'onpost': p['state'] = 'notyet'
    assert warnings(eng, 240) == []


def test_signed_batch_already_placed_before_shift_start_is_not_counted_as_missing():
    eng = fixture((3,), now=1140)
    for p in eng.P.values(): p['end'] = 1300
    eng.P['A0']['start'] = 1200
    eng.P['A0']['batchOnPost'] = 1200
    eng.P['A1']['end'] = 1180
    for i in range(4): eng.P[f'new{i}'].update(state='other', manualPaused=True)
    point = next(p for p in warnings(eng, 1140) if p['at'] == 1180)
    assert point['available'] == 2 and point['gap'] == 1


def test_a_known_break_end_resolves_the_estimated_gap():
    eng = fixture((3,), now=1140)
    for p in eng.P.values(): p['end'] = 1300
    eng.P['A0']['end'] = 1180
    for i in range(4): eng.P[f'new{i}'].update(state='other', manualPaused=True)
    eng.P['new0'].update(state='rest', manualPaused=False, readyAt=1190)
    points = {p['at']: p for p in warnings(eng, 1140)}
    assert points[1180]['gap'] == 1 and points[1190]['gap'] == 0


def test_released_op_with_a_confirmed_break_counts_as_supply():
    eng = fixture((3,), now=1140)
    for p in eng.P.values(): p['end'] = 1300
    eng.P['A0']['end'] = 1200
    for i in range(4): eng.P[f'new{i}'].update(state='other', manualPaused=True)
    eng.P['new0'].update(role='op', state='rest', manualPaused=False, readyAt=1190)
    point = next(p for p in warnings(eng, 1140) if p['at'] == 1200)
    assert point['gap'] == 0 and point['available'] == 3


def test_persisted_transfer_is_atomic_and_stale_duplicate_cannot_move_again(current):
    initial = board(current)
    act(current, {'action': 'rename_post', 'post_key': initial['lines'][0]['posts'][0]['key'], 'name': 'A线塔台'})
    day = board(current)
    post = day['lines'][0]['posts'][0]
    source_worker = person(current, post['occ'])
    body = {'action': 'post_transfer', 'line': 'A', 'post_key': post['key'],
            'target_line': 'B', 'target_i': 1, 'expected_snapshot': day['forecastSnapshot']}
    assert act(current, body)['executed']
    changed = board(current)
    assert [len(line['posts']) for line in changed['lines']] == [1, 3]
    moved = person(current, post['occ'])
    assert moved['line'] == 'B'
    for field in ('continuousStart', 'lineStart', 'entrySortAt'):
        assert moved.get(field) == source_worker.get(field)
    with pytest.raises(S.ConfirmationConflict): act(current, body)
    assert board(current)['lines'] == changed['lines']
    for line, original in zip(changed['lines'], day['lines']):
        assert line['sortAt'] == original['sortAt']


def test_transfer_requires_snapshot_and_refuses_screen_actor(current):
    from types import SimpleNamespace
    day = board(current)
    body = {'action': 'post_transfer', 'line': 'A', 'post_key': day['lines'][0]['posts'][0]['key'],
            'target_line': 'B', 'target_i': 1}
    with pytest.raises(S.ConfirmationConflict): act(current, body)
    with pytest.raises(ActionError): act(current, {**body, 'expected_snapshot': day['forecastSnapshot']}, actor=SimpleNamespace(kind='screen'))


def test_employee_must_recheck_a_route_that_changed_after_a_post_transfer(current):
    initial = board(current)
    act(current, {'action': 'rename_post', 'post_key': initial['lines'][0]['posts'][0]['key'], 'name': 'A线入口'})
    before = board(current)
    employee = person(current, '5')
    task = employee['task']
    assert task
    act(current, {'action': 'post_transfer', 'line': 'A', 'post_key': before['lines'][0]['posts'][0]['key'],
                  'target_line': 'B', 'target_i': 1, 'expected_snapshot': before['forecastSnapshot']})
    stand = board(current)['lines']
    result = act(current, {'action': 'depart', 'pid': '5', 'confirmation_token': employee['confirmationTokens']['depart'],
                           'expected_line': task['line'], 'expected_route': task['routeSignature'],
                           'expected_kind': task['kind'], 'expected_push7': task.get('push7Route'),
                           'expected_opening': task.get('openingRoute')})
    assert result['line_changed']
    assert board(current)['lines'] == stand
    assert person(current, '5')['state'] == employee['state']
