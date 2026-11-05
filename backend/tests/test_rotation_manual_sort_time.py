"""Manual drags opt into sort-time changes; normal confirmation keeps its rules."""
import json

import pytest

from app.rotation import forecast as F, service as S
from app.rotation.models import RotationEvent
from test_rotation_forecast import fixture, occupants
from test_rotation_forecast_api import rotation, current, board, act, person, DAY


@pytest.mark.parametrize('mode', ['push', 'swap'])
@pytest.mark.parametrize('requested', [None, False, True])
def test_pool_to_head_updates_real_post_time_but_sort_time_only_when_requested(mode, requested):
    e = fixture(); e.line('B')['sortAt'] = 430
    kwargs = {} if requested is None else {'update_sort_time': requested}
    F.manual_place(e, 'new0', 'A', 0, mode, 600, 'test', **kwargs)
    assert e.line('A')['sortAt'] == (600 if requested else 435)
    assert e.line('B')['sortAt'] == 430
    assert occupants(e)[0] == 'new0' and e.line('A')['posts'][0]['since'] == 600
    assert e.P['new0']['state'] == 'onpost' and e.P['new0']['lineStart'] == 600


@pytest.mark.parametrize('requested', [False, True])
@pytest.mark.parametrize('mode', ['push', 'swap'])
def test_cross_line_head_drag_preserves_clocks_by_default_and_updates_only_new_heads(requested, mode):
    e = fixture(); e.line('B')['sortAt'] = 440
    F.manual_place(e, 'A0', 'B', 0, mode, 600, 'test', update_sort_time=requested)
    assert e.line('B')['sortAt'] == (600 if requested else 440)
    assert e.line('A')['sortAt'] == (600 if requested and mode == 'swap' else 435)
    assert occupants(e, 'B')[0] == 'A0'
    assert occupants(e)[0] == ('B0' if mode == 'swap' else None)


@pytest.mark.parametrize('target', ['A', 'B'])
@pytest.mark.parametrize('requested', [False, True])
def test_source_head_replaced_by_swap_can_opt_in_even_when_target_is_middle(target, requested):
    e = fixture(); e.line('B')['sortAt'] = 440
    F.manual_place(e, 'A0', target, 1, 'swap', 600, 'test', update_sort_time=requested)
    assert e.line('A')['sortAt'] == (600 if requested else 435)
    assert e.line('B')['sortAt'] == 440


def test_checked_option_does_not_update_a_middle_post_or_an_empty_head():
    e = fixture()
    F.manual_place(e, 'new0', 'A', 1, 'push', 600, 'test', update_sort_time=True)
    assert e.line('A')['sortAt'] == 435
    F.manual_place(e, 'A0', 'B', 1, 'push', 601, 'test', update_sort_time=True)
    assert occupants(e)[0] is None
    assert e.line('A')['sortAt'] == e.line('B')['sortAt'] == 435


def test_first_participating_post_can_be_after_a_closed_post():
    e = fixture(); e.line('A')['posts'][0]['open'] = False
    F.manual_place(e, 'new0', 'A', 1, 'swap', 600, 'test', update_sort_time=True)
    assert e.line('A')['sortAt'] == 600


def test_manual_sort_opt_in_keeps_cross_midnight_minutes():
    e = fixture()
    for p in e.P.values(): p['end'] = 1560
    F.manual_place(e, 'new0', 'A', 0, 'push', 1441.25, 'test', update_sort_time=True)
    assert e.line('A')['sortAt'] == 1441.25


def place(r, pid='5', **extra):
    return act(r, {'action': 'place', 'pid': pid, 'line': 'A', 'i': 0, 'place_mode': 'swap',
                   'expected_snapshot': board(r)['forecastSnapshot'], **extra})


@pytest.mark.parametrize('requested', [None, False, True])
def test_api_manual_default_and_explicit_choice_are_recorded(current, requested):
    r = current; kwargs = {} if requested is None else {'update_sort_time': requested}
    place(r, **kwargs)
    day = board(r)
    assert day['lines'][0]['sortAt'] == (600 if requested else 435)
    assert day['lines'][0]['posts'][0]['since'] == 600
    assert person(r)['lineStart'] == 600
    with r.factory() as db:
        event = db.query(RotationEvent).filter_by(event_type='manual_forecast_complete').one()
        assert json.loads(event.detail_json)['update_sort_time'] is bool(requested)
        changed = db.query(RotationEvent).filter_by(event_type='sort_time').all()
        assert len(changed) == (1 if requested else 0)


@pytest.mark.parametrize('value', [None, 0, 1, 'false', 'on'])
def test_api_rejects_non_boolean_option_without_moving_anyone(current, value):
    r = current; before = board(r)['lines']
    with pytest.raises(S.ActionError): place(r, update_sort_time=value)
    assert board(r)['lines'] == before and person(r)['state'] == 'rest'


@pytest.mark.parametrize('requested', [False, True])
def test_normal_confirm_ignores_manual_drag_option_and_updates_header(current, requested):
    r = current; p = person(r)
    result = act(r, {'action': 'depart', 'pid': '5', 'confirmation_token': p['confirmationTokens']['depart'],
                     'expected_line': 'A', 'update_sort_time': requested})
    assert result['executed'] and board(r)['lines'][0]['sortAt'] == 600


@pytest.mark.parametrize('requested', [False, True])
def test_exception_manual_placement_keeps_timeout_fact_and_respects_sort_option(current, requested):
    r = current
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, 603, paused=True)
    S.tick_attraction(r.attraction_id)
    assert person(r)['state'] == 'exception'
    place(r, update_sort_time=requested)
    assert board(r)['lines'][0]['sortAt'] == (603 if requested else 435)
    with r.factory() as db:
        detail = json.loads(db.query(RotationEvent).filter_by(event_type='manual_forecast_complete').one().detail_json)
        assert detail['timeout'] and detail['update_sort_time'] is requested


@pytest.mark.parametrize('requested', [False, True])
@pytest.mark.parametrize('mode', ['posts', 'with_people'])
def test_post_drag_with_people_offers_same_explicit_policy(current, requested, mode):
    r = current
    for line in board(r)['lines']:
        for post in line['posts']:act(r, {'action': 'rename_post', 'post_key': post['key'], 'name': line['id']+'-'+post['name']})
    day = board(r)
    act(r, {'action': 'post_order', 'line': 'A', 'i': 0, 'target_line': 'B', 'target_i': 0,
            'swapMode': mode, 'expected_snapshot': day['forecastSnapshot'], 'update_sort_time': requested})
    assert [L['sortAt'] for L in board(r)['lines']] == ([600, 600] if requested and mode == 'with_people' else [435, 435])
