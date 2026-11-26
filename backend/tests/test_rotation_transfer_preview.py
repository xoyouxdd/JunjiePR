from copy import deepcopy
import json

import pytest

from app.rotation import service as S
from app.rotation.manual import fingerprint
from app.rotation.models import RotationEvent, RotationSegment, RotationSnapshot
from app.rotation.transfer_preview import preview, payload, reference_positions, project
from test_rotation_forecast import fixture
from test_rotation_forecast_api import current, rotation, board, act


def setup():
    eng = fixture((5, 3))
    eng.line('A')['sortAt'] = 540
    eng.line('B')['sortAt'] = 545
    for p in eng.P.values():
        if p['state'] == 'onpost': p['continuousStart'] = 570
    eng.P['A4']['continuousStart'] = 420
    return eng, dict(line='A', post_key='A4', target_line='B')


def test_old_tail_moved_to_head_reports_delay_and_recommends_tail():
    eng, body = setup()
    before = deepcopy(eng.d)
    result = preview(eng, {**body, 'target_i': 0}, 600)
    moved = next(r for r in result['impacts'] if r['pid'] == 'A4')
    assert (moved['minutes'], moved['beforePushes'], moved['afterPushes'], moved['delta']) == (180, 1, 4, 3)
    assert result['recommendedIndex'] == 3
    assert result['selectedIndex'] == 0  # Advice never overrides a manual choice.
    assert result['before']['rest'][0]['pid'] == 'A4'
    assert result['after']['rest'][0]['pid'] == 'A3'
    assert len(result['impacts']) == 8  # Both lines, not only the moved worker.
    assert eng.d == before and eng.h.events == []


def test_recommendation_also_accounts_for_receiving_line_and_young_head():
    eng, body = setup()
    eng.P['B0']['continuousStart'] = 300
    result = preview(eng, {**body, 'post_key': 'A0'}, 600)
    assert result['recommendedIndex'] == result['selectedIndex'] == 0
    assert not any(r['delta'] > 0 for r in result['impacts'])


def test_reference_pushes_include_vacancies_without_resetting_any_clock():
    eng, _ = setup()
    eng.line('A')['posts'][0]['occ'] = None
    rows = reference_positions(eng, {'A'}, 600)
    assert rows['A4']['pushes'] == 2
    assert rows['A1']['pushes'] == 5


def test_unknown_continuous_time_falls_back_or_displays_unverified():
    eng, body = setup()
    eng.P['A4'].pop('continuousStart')
    result = preview(eng, body, 600)
    assert next(r for r in result['impacts'] if r['pid'] == 'A4')['minutes'] == 165
    eng.P['A4'].pop('lineStart')
    result = preview(eng, body, 600)
    assert next(r for r in result['impacts'] if r['pid'] == 'A4')['minutes'] is None
    json.dumps(result, allow_nan=False)


def test_no_invented_arrivals_and_special_exit_is_not_reported_as_rest():
    eng, body = setup()
    for i in range(4): eng.P[f'new{i}']['manualPaused'] = True
    result = preview(eng, body, 600)
    assert result['before']['crewCount'] == 0 and result['after']['rest'] == []
    eng.P['new0']['manualPaused'] = False
    eng.P['A4']['end'] = 600
    result = preview(eng, {**body, 'target_i': 0}, 600)
    assert result['before']['departures'][0]['outcome'] == 'done'
    assert result['before']['rest'] == []


def test_projection_is_bounded_and_never_moves_backwards(monkeypatch):
    from app.rotation import transfer_preview as T
    eng, _ = setup()
    monkeypatch.setattr(T, 'SIMULATION_LIMIT', 2)
    result = project(eng, ['new0', 'new1', 'new2'], 600)
    assert result['crewCount'] == 2 and result['limited']
    assert len(result['departures']) == 2


def test_preview_endpoint_builder_is_read_only_and_stale_snapshot_refused(current):
    initial = board(current)
    act(current, {'action': 'rename_post', 'post_key': initial['lines'][0]['posts'][0]['key'], 'name': 'A入口'})
    day = board(current)
    body = dict(line='A', post_key=day['lines'][0]['posts'][0]['key'], target_line='B', expected_snapshot=day['forecastSnapshot'])
    with current.factory() as db:
        def counts(): return [db.query(model).count() for model in (RotationEvent, RotationSegment, RotationSnapshot)]
        before = counts()
        result = payload(db, current.attraction_id, body)
        assert counts() == before and not db.dirty and not db.new
    assert board(current)['forecastSnapshot'] == day['forecastSnapshot']
    assert board(current)['lines'] == day['lines']
    # A preview token cannot authorize a different insertion position.
    with pytest.raises(S.ConfirmationConflict):
        act(current, {**body, 'action': 'post_transfer', 'target_i': (result['selectedIndex'] + 1) % 3, 'expected_preview': result['digest']})
    assert board(current)['lines'] == day['lines']
    act(current, {**body, 'action': 'post_transfer', 'target_i': result['selectedIndex'], 'expected_preview': result['digest']})
    with current.factory() as db, pytest.raises(S.ConfirmationConflict):
        payload(db, current.attraction_id, body)
