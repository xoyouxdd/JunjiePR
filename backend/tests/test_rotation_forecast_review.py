"""Adversarial review: mixed purposes, gaps, concurrent people and overnight edges."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from test_rotation_forecast import fixture as engine, occupants
from test_rotation_forecast_api import rotation, current, board, act, person, DAY
from app.rotation import forecast as F, service as S, engine as E, workflow as W
from test_rotation_engine import Hooks
from app.rotation.forecast_service import notices

PURPOSES = ['sick', 'push7', 'off', 'opening', 'vacancy', 'out', 'push']


def test_first_shift_is_automatic_but_subsequent_ready_forecast_requires_click():
    settings = deepcopy(E.DEFAULT_SETTINGS)
    roster = {'people': {str(i): {'name': '员工'+str(i)} for i in range(1, 4)},
              'days': {DAY: {str(i): '07:15-20:00' for i in range(1, 4)}}}
    state = E.build_draft(DAY, roster, {'lines': [{'id': 'A', 'posts': [{'name': '入口'}, {'name': '出口'}]}]},
                          settings, {}, {})
    state['ruleVersion'] = 2
    e = E.Engine(state, settings, Hooks())
    W.initialize(e)
    e.publish(240, 'test')
    assert e.line('A')['sortAt'] == 0 and all(p['state'] == 'notyet' for p in e.P.values())
    e.tick(435)
    seated = set(occupants(e))
    assert len(seated) == 2 and all(e.P[pid]['state'] == 'onpost' for pid in seated)
    assert e.line('A')['sortAt'] == 435
    next_person = next(p for p in e.P.values() if p['pid'] not in seated)
    assert next_person['readyAt'] == 450 and next_person['state'] == 'rest'
    e.tick(450)
    assert set(occupants(e)) == seated and e.line('A')['sortAt'] == 435
    F.execute(e, next_person['pid'], 450, 'test', 'A')
    assert next_person['state'] == 'onpost' and e.line('A')['sortAt'] == 450


@pytest.mark.parametrize('strong,weak', [(a, b) for i, a in enumerate(PURPOSES) for b in PURPOSES[i+1:]
                                      if {a, b} != {'opening', 'vacancy'}])
def test_confirmed_business_priority_matrix(strong, weak):
    e = engine()
    for lid, purpose in [('A', weak), ('B', strong)]:
        target = e.P[lid+'2']
        if purpose in ('sick', 'out'):
            target['requestedExit'] = purpose
        elif purpose == 'push7':
            target.update(start=420, flags=['7点岗'])
        elif purpose == 'off':
            target['end'] = 620
        elif purpose in ('opening', 'vacancy'):
            e.line(lid)['posts'][2]['occ'] = None
            target.update(state='other', manualPaused=True)
            e.line(lid)['adding'] = purpose == 'opening'
    F.plan(e, 600)
    assert e.P['new0']['assign']['kind'] == strong and e.P['new0']['assign']['line'] == 'B'


def test_vacancy_and_adding_outrank_outgoing_request_on_same_line():
    for adding in (False, True):
        e = engine()
        e.P['A1']['requestedExit'] = 'out'
        e.line('A')['posts'][2]['occ'] = None
        e.P['A2'].update(state='other', manualPaused=True)
        e.line('A')['adding'] = adding
        F.plan(e, 600)
        assert e.P['new0']['assign']['kind'] == ('opening' if adding else 'vacancy')
        assert e.P['new0']['assign']['line'] == 'A'


@pytest.mark.parametrize('purpose,outcome', [('sick', 'sick'), ('off', 'done'), ('push7', 'pending')])
@pytest.mark.parametrize('gap', [0, 1])
@pytest.mark.parametrize('last', [True, False])
def test_high_priority_target_is_actually_relieved_even_with_a_prefix_gap(purpose, outcome, gap, last):
    e = engine((3 if last else 4,))
    e.line('A')['posts'][gap]['occ'] = None
    e.P['A'+str(gap)].update(state='other', manualPaused=True)
    target = e.P['A2']
    if purpose == 'sick':
        target['requestedExit'] = 'sick'
    elif purpose == 'push7':
        target.update(start=420, flags=['7点岗'])
    else:
        target['end'] = 620
    F.plan(e, 600)
    assert e.P['new0']['assign']['kind'] == purpose
    F.execute(e, 'new0', 600, 'test', 'A')
    assert target['state'] == outcome
    assert 'A2' not in occupants(e)
    remaining = [pid for pid in occupants(e) if pid]
    assert len(remaining) == (2 if last else 3) and len(set(remaining)) == len(remaining)
    assert e.line('A')['sortAt'] == (600 if last else 435)


def test_reserved_push7_does_not_hide_other_special_demands():
    e = engine((3,))
    e.P['A0'].update(start=420, flags=['7点岗'])
    e.P['A2']['end'] = 620
    e.d['push7History'] = {'new0': [1, '2026-11-01', 435]}
    F.plan(e, 600)
    assert e.P['new0']['assign']['kind'] == 'off'
    assert e.P['new1']['assign']['kind'] == 'push7'
    actual, _ = F.confirmation_plan(e, 'new0', 600)
    assert actual['kind'] == 'off'


def test_moving_head_to_middle_without_replacement_does_not_claim_a_new_head_arrival():
    e = engine((3,))
    F.manual_place(e, 'A0', 'A', 1, 'push', 600, 'test')
    assert occupants(e)[0] is None
    assert e.line('A')['sortAt'] == 435


def test_scheduled_out_returns_at_interval_end_then_rests_fifteen_minutes():
    e = engine((3,))
    e.P['A1']['absences'] = [{'start': 610, 'end': 630, 'label': '现场工作'}]
    F.plan(e, 600)
    assert e.P['new0']['assign']['kind'] == 'out'
    F.execute(e, 'new0', 600, 'test', 'A')
    assert e.P['A1']['away']['until'] == 630
    F.tick(e, 630)
    assert e.P['A1']['state'] == 'rest' and e.P['A1']['readyAt'] == 645


def test_manual_return_during_fixed_absence_does_not_immediately_bounce_out_again():
    e = engine((3,))
    p = e.P['new0']
    p.update(state='away', away={'reason': '现场工作', 'until': 630})
    p['absences'] = [{'start': 590, 'end': 630, 'label': '现场工作'}]
    F.pool(e, p['pid'], 'rest', 600, 'test', remaining=15)
    F.tick(e, 601)
    assert p['state'] == 'rest' and p['readyAt'] == 615


def test_planned_opening_does_not_publish_ready_notice_or_deadline_before_opening():
    e = engine((1,))
    e.line('A')['posts'][0].update(open=False, occ=None, opened=False, manual=False, openAt='10:30')
    F.tick(e, 600)
    assert notices(e, 600)[('new0', 'step')][0] == 'rest'
    task = F.task_view(e, e.P['new0'], 600)
    assert task['allowedAt'] == 630 and task['deadlineAt'] == 633


def test_distinct_people_confirm_same_line_concurrently_and_each_retry_is_idempotent(current):
    r = current
    act(r, {'action': 'add_person', 'temporary': True, 'name': '并发伙伴', 'start': '00:00', 'end': '20:00'})
    extra = next(p['pid'] for p in board(r)['persons'] if p['name'] == '并发伙伴')
    act(r, {'action': 'pool', 'pid': extra, 'pool': 'rest'})
    act(r, {'action': 'break_time', 'pid': extra, 'remaining': 0})
    act(r, {'action': 'line', 'line': 'B', 'active': False})
    bodies = []
    for pid in ['5', extra]:
        p = person(r, pid)
        bodies.append({'action': 'depart', 'pid': pid, 'expected_line': 'A',
                       'confirmation_token': p['confirmationTokens']['depart']})
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda body: act(r, body), bodies + bodies))
    assert sum(bool(row.get('executed')) for row in results) == 2
    assert sum(bool(row.get('duplicate')) for row in results) == 2
    day = board(r)
    posted = [post['occ'] for line in day['lines'] for post in line['posts'] if post['occ']]
    assert len(posted) == len(set(posted))
    assert set(posted) == {'5', extra}
    assert all(person(r, pid)['state'] == 'onpost' for pid in ['5', extra])


def test_timeout_and_confirmation_race_at_exact_deadline_cannot_admit_late_person(current):
    r = current
    p = person(r)
    body = {'action': 'depart', 'pid': '5', 'expected_line': p['assign']['line'],
            'confirmation_token': p['confirmationTokens']['depart']}
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, 603, paused=True)
    def submit():
        try:
            return act(r, body)
        except S.ActionError:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        confirm = workers.submit(submit)
        tick = workers.submit(S.tick_attraction, r.attraction_id)
        tick.result()
        assert confirm.result() is None
    assert person(r)['state'] == 'exception' and person(r)['assign'] is None


def test_exception_stays_out_of_replanning_and_manual_pool_return_is_not_execution(current):
    from app.rotation.models import RotationEvent
    import json
    r = current
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, 603, paused=True)
    S.tick_attraction(r.attraction_id)
    evidence = deepcopy(person(r)['exception'])
    act(r, {'action': 'sort_time', 'line': 'A', 'time': '00:00:00'})
    p = person(r)
    assert p['state'] == 'exception' and p['assign'] is None and p['exception'] == evidence
    act(r, {'action': 'pool', 'pid': '5', 'pool': 'rest'})
    p = person(r)
    assert p['state'] == 'rest' and not p.get('exception') and p['readyAt'] == 618
    with r.factory() as db:
        assert db.query(RotationEvent).filter_by(event_type='manual_forecast_complete', employee_no='5').count() == 0
        records = db.query(RotationEvent).filter_by(event_type='pool_move', employee_no='5').all()
        assert any(json.loads(event.detail_json).get('endedException') == evidence for event in records)


def test_moving_shift_start_later_does_not_inherit_an_expired_readiness_deadline(current):
    r = current
    act(r, {'action': 'shift', 'pid': '5', 'start': '11:00', 'end': '20:00'})
    assert not person(r)['canDepart'] and person(r)['assign']['allowedAt'] == 660
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, 660, paused=True)
    S.tick_attraction(r.attraction_id)
    p = person(r)
    assert p['state'] == 'rest' and p['canDepart']
    assert p['readyAt'] == 660 and p['task']['deadlineAt'] == 663


def test_overnight_person_can_confirm_and_finish_at_exact_last_shift_end(current):
    r = current
    with r.factory() as db, S.transaction(db):
        row = S.day_row(db, r.attraction_id, DAY)
        state = S.day_state(row)
        state['lastTick'] = 1440
        for p in state['persons'].values():
            p['end'] = 1500
        state['persons']['5'].update(readyAt=1450, timeoutAt=1453, state='rest')
        S.save_day(db, row, state)
        S.set_sim_clock(S.load_config(db, r.attraction_id), '2026-10-05', 10, paused=True)
    S.tick_attraction(r.attraction_id)
    data = board(r)
    assert data['date'] == DAY and data['status'] == 'live'
    p = person(r)
    assert p['canDepart']
    assert act(r, {'action': 'depart', 'pid': '5', 'expected_line': p['assign']['line'],
                   'confirmation_token': p['confirmationTokens']['depart']})['executed']
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), '2026-10-05', 60, paused=True)
    S.tick_attraction(r.attraction_id)
    with r.factory() as db:
        assert S.day_row(db, r.attraction_id, DAY).status == 'ended'


def test_next_day_new_person_and_shift_edit_preserve_the_workday_offset(current):
    r = current
    act(r, {'action': 'add_person', 'temporary': True, 'name': '午夜支援',
            'start_day_offset': 1, 'start': '00:20', 'end': '04:00'})
    p = next(p for p in board(r)['persons'] if p['name'] == '午夜支援')
    assert p['start'] == 1460 and p['end'] == 1680 and p['state'] == 'notyet'
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), '2026-10-05', 10, paused=True)
    S.tick_attraction(r.attraction_id)
    act(r, {'action': 'shift', 'pid': p['pid'], 'start_day_offset': 1, 'start': '00:05', 'end': '04:00'})
    p = person(r, p['pid'])
    assert p['start'] == 1445 and p['end'] == 1680 and p['state'] == 'pending'


def test_overnight_post_schedule_keeps_date_and_closes_at_the_exact_workday_time(current):
    r = current
    with r.factory() as db, S.transaction(db):
        row = S.day_row(db, r.attraction_id, DAY)
        state = S.day_state(row)
        state['lastTick'] = 1450
        for p in state['persons'].values():
            p['end'] = 1600
        S.save_day(db, row, state)
        S.set_sim_clock(S.load_config(db, r.attraction_id), '2026-10-05', 10, paused=True)
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': False})
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '00:15:30', 'day_offset': 1})
    assert board(r)['lines'][0]['posts'][1]['openAt'] == '24:15:30'
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), '2026-10-05', 15.5, paused=True)
    S.tick_attraction(r.attraction_id)
    assert board(r)['lines'][0]['posts'][1]['open']
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': False, 'closeAt': '00:20:00', 'day_offset': 1})
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), '2026-10-05', 20, paused=True)
    S.tick_attraction(r.attraction_id)
    assert not board(r)['lines'][0]['posts'][1]['open'] and board(r)['lines'][0]['sortAt'] == 1460
    with pytest.raises(S.ActionError, match='轮岗结束'):
        act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '03:00:00', 'day_offset': 1})
