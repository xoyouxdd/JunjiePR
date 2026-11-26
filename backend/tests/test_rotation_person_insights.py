from copy import deepcopy
import json

import pytest

from app.rotation.person_insights import day_history, summary, bounds, payload, search
from app.rotation.engine import ActionError
from app.rotation import service as S
from app.rotation.namespace import tables_for
from test_rotation_forecast import fixture
from test_rotation_forecast_api import current, rotation, board


def state(kind, line='A', slot=0, start=420, continuous=420):
    s = fixture((3, 2)).d
    for L in s['lines']:
        for post in L['posts']: post['occ'] = None
    p = s['persons']['new0']
    p.update(state=kind, continuousStart=continuous, lineStart=start, breakStart=None)
    if kind == 'onpost':
        s['lines'][ord(line) - 65]['posts'][slot].update(occ='new0', since=start)
    if kind in ('rest', 'ready', 'meal'):
        p.update(breakStart=start, readyAt=start + 15, breakKind='meal' if kind == 'meal' else 'rest')
    return s


def test_each_post_rest_waiting_and_cross_line_continuous_work():
    observations = [(400, state('notyet')), (420, state('onpost')), (440, state('onpost', slot=1, start=440)),
                    (460, state('onpost', line='B', start=460)), (480, state('pending')),
                    (482, state('rest', start=482)), (497, state('ready', start=482)),
                    (502, state('onpost', start=502, continuous=502))]
    original = deepcopy(observations)
    result = day_history(observations, 'new0', 512, True)
    assert [r['minutes'] for r in result['rows']] == [20, 20, 20, 20, 10]
    rest = result['rows'][3]
    assert rest['beforeWork'] == 60  # Cross-line move does not reset continuous work.
    assert rest['end'] == 502  # Ready at 497 does NOT mean rest ended at 497.
    assert [(v['line'], v['minutes']) for v in result['visits']] == [('A', 40), ('B', 20), ('A', 10)]
    assert result['rows'][-1]['ongoing']
    assert observations == original


def test_swap_preserves_old_slot_clock_but_history_uses_occupancy_transition():
    observations = [(400, state('notyet')), (420, state('onpost')), (450, state('onpost', slot=1, start=420)),
                    (480, state('pending'))]
    result = day_history(observations, 'new0', 500, False)
    assert [r['minutes'] for r in result['rows']] == [30, 30]
    assert result['visits'][0]['minutes'] == 60


def test_first_occupied_snapshot_never_claims_exact_post_start_or_finished_end():
    result = day_history([(450, state('onpost')), (480, state('pending')), (482, state('rest', start=482))], 'new0', 497, False)
    assert result['rows'][0]['minutes'] is None
    assert result['rows'][1]['minutes'] is None  # Past live day is only a last-seen state.
    assert result['rows'][1]['beforeWork'] == 60  # Verified continuous clock remains useful.


def test_prearrangement_is_not_work_and_scheduled_start_uses_actual_clock():
    result = day_history([(240, state('notyet')), (421, state('onpost', start=420)),
                          (442, state('pending'))], 'new0', 450, False)
    assert result['rows'][0]['start'] == 420
    assert result['rows'][0]['minutes'] == 22


def test_rewinds_and_missing_clocks_are_not_precision_statistics():
    result = day_history([(400, state('notyet')), (420, state('onpost')), (500, state('pending')),
                          (480, state('rest', start=480))], 'new0', 490, True)
    assert result['rewound']
    assert all(r['minutes'] is None for r in result['rows'])
    assert all(r.get('beforeWork') is None for r in result['rows'])
    json.dumps(result, allow_nan=False)


def test_meals_are_separate_from_rest_average():
    result = day_history([(400, state('notyet')), (420, state('onpost')), (480, state('pending')),
                          (482, state('meal', start=482)), (512, state('onpost', start=512, continuous=512))], 'new0', 522, True)
    stats = summary([{**result, 'fallback': []}])
    assert stats['avgRest'] is None and stats['avgWork'] == 60
    assert result['rows'][1]['kind'] == 'meal' and result['rows'][1]['minutes'] == 30


@pytest.mark.parametrize('start,end', [('bad', '2026-11-27'), ('2026-11-28', '2026-11-27'), ('2026-10-01', '2026-11-27')])
def test_range_validation(start, end):
    with pytest.raises(ActionError): bounds(start, end)


def test_read_only_scoped_payload_and_historical_name_search(current):
    before = board(current)
    day = before['date']
    with current.factory() as db:
        T = tables_for(db)
        counts = [db.query(model).count() for model in (T.RotationEvent, T.RotationSegment, T.RotationSnapshot)]
        result = payload(db, current.attraction_id, '1', day, day)
        assert result['employee_no'] == '1'
        name = result['name']
        assert any(p['pid'] == '1' for p in search(db, current.attraction_id, name, day, day))
        assert payload(db, current.attraction_id + 999, '1', day, day)['days'] == []
        assert search(db, current.attraction_id + 999, name, day, day) == []
        assert counts == [db.query(model).count() for model in (T.RotationEvent, T.RotationSegment, T.RotationSnapshot)]
        assert not db.dirty and not db.new
    assert board(current)['forecastSnapshot'] == before['forecastSnapshot']


def test_legacy_line_segments_do_not_fabricate_posts_or_rests(current):
    day = board(current)['date']
    with current.factory() as db, S.transaction(db):
        T = tables_for(db)
        db.query(T.RotationSnapshot).filter_by(attraction_id=current.attraction_id).delete()
        db.add(T.RotationSegment(attraction_id=current.attraction_id, employee_no='1', work_date=day,
                                line='A', start_min=420, end_min=450, minutes=30))
    with current.factory() as db:
        result = payload(db, current.attraction_id, '1', day, day)
        assert result['days'][0]['rows'] == []
        assert result['days'][0]['fallback'][0]['minutes'] == 30
        assert result['summary']['avgWork'] is None and result['summary']['avgRest'] is None
        assert result['lines'][0]['minutes'] == 30 and result['lines'][0]['average'] is None


def test_damaged_snapshot_is_reported_without_crashing_or_faking_precision(current):
    day = board(current)['date']
    with current.factory() as db, S.transaction(db):
        T = tables_for(db)
        db.add(T.RotationSnapshot(attraction_id=current.attraction_id, work_date=day, minute=599,
                                 fingerprint='damaged-test', payload='invalid-zlib'))
    with current.factory() as db:
        result = payload(db, current.attraction_id, '1', day, day)
        assert result['days'][0]['damaged']
        assert all(r['minutes'] is None for r in result['days'][0]['rows'])


def test_ongoing_rest_does_not_skew_completed_rest_average():
    result = day_history([(400, state('notyet')), (420, state('onpost')), (480, state('pending')),
                          (482, state('rest', start=482))], 'new0', 487, True)
    stats = summary([{**result, 'fallback': []}])
    assert result['rows'][-1]['minutes'] == 5
    assert stats['avgRest'] is None and stats['avgWork'] == 60


def test_workday_crosses_midnight_without_resetting_durations():
    result = day_history([(1420, state('notyet')), (1430, state('onpost', start=1430, continuous=1430)),
                          (1460, state('pending')), (1462, state('rest', start=1462)),
                          (1482, state('onpost', start=1482, continuous=1482))], 'new0', 1490, True)
    assert result['rows'][0]['minutes'] == 30
    assert result['rows'][1]['minutes'] == 20 and result['rows'][1]['beforeWork'] == 30
