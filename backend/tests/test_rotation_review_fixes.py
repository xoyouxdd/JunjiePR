"""Regression cases for the six locally reviewed rotation boundaries."""
from copy import deepcopy
import pytest

from app.rotation import forecast as F, service as S
from app.rotation.namespace import tables_for
from app.rotation.person_insights import accounting_gaps, day_history, payload, summary
from app.rotation.staffing import warnings
from test_rotation_forecast import fixture
from test_rotation_forecast_api import current, rotation, act, person, board
from test_rotation_person_insights import state


@pytest.mark.parametrize('kind,outcome', [('push', 'rest'), ('sick', 'sick'), ('out', 'away'), ('off', 'done')])
def test_confirmed_route_persists_explicit_outcome_and_duplicate_receipt(current, kind, outcome):
    day = board(current)['date']
    with current.factory() as db, S.transaction(db):
        cfg = S.load_config(db, current.attraction_id)
        row = S.day_row(db, current.attraction_id, day)
        saved = S.day_state(row)
        if kind in ('sick', 'out'): saved['persons']['1']['requestedExit'] = kind
        if kind == 'off':
            saved['persons']['1']['end'] = 620
            settings = S.settings_of(cfg)
            settings['lateShiftsByDay'] = {day: []}
            cfg.settings_json = S._dumps(settings)
        eng, _ = S.engine_for(db, cfg, row, saved)
        F.plan(eng, 600)
        S.save_day(db, row, saved)
    p = person(current, '5')
    task = p['task']
    assert task['kind'] == kind
    body = {'action': 'depart', 'pid': '5', 'confirmation_token': p['confirmationTokens']['depart'],
            'expected_line': task['line'], 'expected_kind': task['kind'], 'expected_route': task['routeSignature'],
            'expected_opening': task.get('openingRoute'), 'expected_push7': task.get('push7Route')}
    result = act(current, body)
    stored = result['result']['task']
    assert stored['status'] == 'executed' and stored['routes'] == task['routes']
    assert stored['departures'] and stored['departures'][-1]['outcome'] == outcome
    positions = board(current)['lines']
    assert act(current, body)['duplicate']
    assert board(current)['lines'] == positions
    with current.factory() as db:
        receipts = S.member_payload(db, current.attraction_id, '5')['confirmations']
        assert receipts[0]['task']['departures'] == stored['departures']


@pytest.mark.parametrize('mode', ['push', 'swap'])
@pytest.mark.parametrize('status', ['pending', 'reported'])
def test_manual_exception_placement_closes_report_and_retains_reason(mode, status):
    eng = fixture((3,), now=610)
    p = eng.P['new0']
    p.update(state='exception', assign=None, exception={'at': 600, 'assignment': {'line': 'A', 'kind': 'push'}})
    report = {'pid': p['pid'], 'name': p['name'], 'at': 600, 'line': 'A', 'status': status, 'reason': 'reported reason'}
    eng.d['exceptionReports'] = [report]
    F.manual_place(eng, p['pid'], 'A', 0, mode, 610, 'supervisor')
    assert p['state'] == 'onpost' and not p.get('exception')
    assert report['status'] == 'manager_resolved' and report['resolvedAt'] == 610
    assert report['reason'] == 'reported reason'
    assert len(eng.d['exceptionReports']) == 1


def test_self_resolved_report_is_not_relabelled_as_manager_resolution():
    eng = fixture()
    p = eng.P['new0']
    p.update(state='exception', exception={'at': 599, 'assignment': {'line': 'A'}})
    eng.d['exceptionReports'] = [{'pid': p['pid'], 'name': p['name'], 'at': 599, 'status': 'self_resolved', 'resolvedAt': 600}]
    F.pool(eng, p['pid'], 'rest', 600, 'member', remaining=0)
    assert eng.d['exceptionReports'][0]['status'] == 'self_resolved'


@pytest.mark.parametrize('done,expected_gap', [(True, 0), (False, 1)])
def test_staffing_only_subtracts_active_absences(done, expected_gap):
    eng = fixture((3,), now=1140)
    for p in eng.P.values():
        p['end'] = 1320
        if p['state'] != 'onpost': p['manualPaused'] = True
    eng.P['A0']['absences'] = [{'start': 1100, 'end': 1200, 'done': done, 'cancelledByManual': done}]
    before = deepcopy(eng.d)
    points = warnings(eng, 1140)
    assert (points[0]['gap'] if points else 0) == expected_gap
    assert eng.d == before


def test_exception_recovery_is_one_rest_including_wait_until_actual_work():
    observations = [(570, state('notyet')), (580, state('onpost', start=580, continuous=580)),
                    (598, state('pending')), (600, state('rest', start=600)), (615, state('ready', start=600)),
                    (616, state('exception')), (617, state('exception')), (618, state('rest', start=618)),
                    (619, state('onpost', start=619, continuous=619))]
    result = day_history(observations, 'new0', 620, True)
    rests = [r for r in result['rows'] if r['kind'] == 'rest']
    assert len(rests) == 1
    assert (rests[0]['start'], rests[0]['end'], rests[0]['minutes'], rests[0]['beforeWork']) == (600, 619, 19, 18)
    assert rests[0]['exceptionWait']
    stats = summary([{**result, 'fallback': []}])
    assert stats['restSamples'] == 1 and stats['avgRest'] == 19
    assert stats['workSamples'] == 1


def test_intervening_work_prevents_two_rests_from_merging():
    observations = [(590, state('notyet')), (600, state('rest', start=600)), (616, state('exception')),
                    (617, state('onpost', start=617, continuous=617)), (620, state('pending')),
                    (622, state('rest', start=622)), (630, state('onpost', start=630, continuous=630))]
    result = day_history(observations, 'new0', 631, True)
    assert [r['minutes'] for r in result['rows'] if r['kind'] == 'rest'] == [17, 8]


def test_unknown_prior_rest_after_exception_is_not_a_fake_complete_sample():
    result = day_history([(616, state('exception')), (618, state('rest', start=618)),
                          (619, state('onpost', start=619, continuous=619))], 'new0', 620, True)
    assert result['rows'][0]['minutes'] is None
    assert summary([{**result, 'fallback': []}])['restSamples'] == 0


def test_known_line_segments_fill_partial_snapshot_history(current):
    day = board(current)['date']
    with current.factory() as db, S.transaction(db):
        T = tables_for(db)
        latest = db.query(T.RotationSnapshot.id).filter_by(attraction_id=current.attraction_id, work_date=day).order_by(T.RotationSnapshot.id.desc()).first()[0]
        db.query(T.RotationSnapshot).filter(T.RotationSnapshot.attraction_id == current.attraction_id, T.RotationSnapshot.id != latest).delete()
        for _ in range(2):
            db.add(T.RotationSegment(attraction_id=current.attraction_id, work_date=day, employee_no='1',
                                     line='A', start_min=435, end_min=500, minutes=65))
    with current.factory() as db:
        result = payload(db, current.attraction_id, '1', day, day)
        assert result['lines'][0]['minutes'] == 65
        assert result['summary']['postMinutes'] == 65
        assert result['lines'][0]['average'] is None
        assert result['days'][0]['fallback'] == [{'line': 'A', 'start': 435, 'end': 500, 'minutes': 65}]


def test_line_accounting_is_split_around_snapshot_coverage_without_double_count():
    rows = [{'kind': 'post', 'line': 'A', 'start': 440, 'end': 450, 'minutes': 10},
            {'kind': 'post', 'line': 'A', 'start': 470, 'end': 480, 'minutes': 10}]
    segments = [{'line': 'A', 'start': 420, 'end': 470, 'minutes': 50},
                {'line': 'A', 'start': 450, 'end': 490, 'minutes': 40}]
    gaps = accounting_gaps(rows, segments)
    assert [(r['start'], r['end']) for r in gaps] == [(420, 440), (450, 470), (480, 490)]
    assert sum(r['minutes'] for r in rows + gaps) == 70
