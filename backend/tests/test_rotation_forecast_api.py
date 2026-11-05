"""Real isolated SQLite, version-2 service transactions and committed projections."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from test_rotation_transaction_boundaries import rotation, DAY
from app.rotation import service as S, forecast as F
from app.rotation.forecast_service import snapshot, search_people
from app.rotation.models import RotationDay, RotationEvent, RotationSnapshot


@pytest.fixture
def current(rotation):
    with rotation.factory() as db:
        S.do_draft_action(db, rotation.attraction_id, 'test', {'action': 'draft_generate', 'date': DAY})
        S.do_draft_action(db, rotation.attraction_id, 'test', {'action': 'publish', 'date': DAY})
        with S.transaction(db):
            cfg = S.load_config(db, rotation.attraction_id)
            S.set_sim_clock(cfg, DAY, 600, paused=True)
            row = S.day_row(db, rotation.attraction_id, DAY)
            state = S.day_state(row)
            state.update(ruleVersion=2, lastTick=600)
            eng, _ = S.engine_for(db, cfg, row, state)
            for n, p in enumerate(eng.P.values()):
                p.update(end=1200, state='onpost' if n < 4 else 'rest', readyAt=600 if n == 4 else None,
                         breakKind='rest' if n == 4 else None, continuousStart=435 if n < 4 else None,
                         lineStart=435 if n < 4 else None, line='A' if n < 2 else 'B' if n < 4 else None,
                         forecastOrder=str(n))
            for L, ids in zip(state['lines'], [('1', '2'), ('3', '4')]):
                L.update(active=True, sortAt=435)
                for post, pid in zip(L['posts'], ids):
                    post.update(open=True, opened=True, occ=pid, since=435)
            F.plan(eng, 600)
            S.save_day(db, row, state)
    return rotation


def board(r):
    with r.factory() as db:
        return S.board_payload(db, r.attraction_id)['day']


def act(r, body, actor='test'):
    with r.factory() as db:
        return S.do_live_action(db, r.attraction_id, actor, body)


def person(r, pid='5'):
    return next(p for p in board(r)['persons'] if p['pid'] == pid)


@pytest.mark.parametrize('end,now,expected', [(625,599+59/60,None),(625,600,'推下班'),(625,625,'推下班'),
                                           (1460,1434+59/60,None),(1460,1435,'推下班'),(1470,1445,'推下班')])
def test_preparing_shift_end_uses_shared_25_minute_boundary_and_workday_clock(current,end,now,expected):
    with current.factory() as db:
        row=S.day_row(db,current.attraction_id,DAY);state=S.day_state(row)
        eng,_=S.engine_for(db,S.load_config(db,current.attraction_id),row,state)
        p=eng.P['1'];p['end']=end
        from app.rotation.forecast_service import person_view
        assert person_view(eng,p,now)['preparing']==expected
        p['state']='pending'
        assert person_view(eng,p,now)['preparing'] is None


def test_prepare_boundary_notifies_even_without_available_relief_and_clears_after_removal(current):
    from app.rotation.forecast_service import person_view
    with current.factory() as db,S.transaction(db):
        cfg=S.load_config(db,current.attraction_id);row=S.day_row(db,current.attraction_id,DAY);state=S.day_state(row)
        eng,_=S.engine_for(db,cfg,row,state)
        state['lastTick']=599
        eng.P['1']['end']=625
        eng.P['5'].update(state='pending',assign=None,readyAt=None)
        F.plan(eng,599)
        S.save_day(db,row,state)
    with current.factory() as db,S.transaction(db):
        cfg=S.load_config(db,current.attraction_id);row=S.day_row(db,current.attraction_id,DAY);state=S.day_state(row)
        assert S.advance(db,cfg,row,state,600),'Crossing preparation time must notify all connected views'
        eng,_=S.engine_for(db,cfg,row,state)
        assert person_view(eng,eng.P['1'],600)['preparing']=='推下班'
        F.pool(eng,'1','pending',600,'test')
        assert person_view(eng,eng.P['1'],600)['preparing'] is None


def test_preparing_keeps_sickness_priority_and_shift_change_recalculates(current):
    from app.rotation.forecast_service import person_view
    with current.factory() as db:
        row=S.day_row(db,current.attraction_id,DAY);state=S.day_state(row)
        eng,_=S.engine_for(db,S.load_config(db,current.attraction_id),row,state)
        p=eng.P['1'];p.update(end=625,requestedExit='out')
        assert person_view(eng,p,600)['preparing']=='推下班'
        p['requestedExit']='sick'
        assert person_view(eng,p,600)['preparing']=='推病假'
        p.pop('requestedExit');p['end']=700
        assert person_view(eng,p,600)['preparing'] is None


def test_new_day_is_v2_and_preview_never_rewrites_config_or_historical_snapshots(current):
    r = current
    with r.factory() as db:
        cfg = S.load_config(db, r.attraction_id)
        before = (cfg.lines_json, cfg.settings_json)
        snapshots = {x.id: x.payload for x in db.query(RotationSnapshot).all()}
    data = board(r)
    assert data['ruleVersion'] == 2 and data['taskWorkflow'] == 2
    assert person(r)['task']['status'] == 'forecast'
    with r.factory() as db:
        cfg = S.load_config(db, r.attraction_id)
        assert (cfg.lines_json, cfg.settings_json) == before
        assert all(db.get(RotationSnapshot, key).payload == value for key, value in snapshots.items())


def test_prearrange_at_0300_does_not_cut_off_live_overnight_day(current):
    from datetime import date,timedelta
    tomorrow=(date.fromisoformat(DAY)+timedelta(days=1)).isoformat()
    with current.factory() as db,S.transaction(db):
        cfg=S.load_config(db,current.attraction_id);row=S.day_row(db,current.attraction_id,DAY);state=S.day_state(row)
        for p in state['persons'].values():p['end']=1680
        state['lastTick']=1439
        S.save_day(db,row,state);S.set_sim_clock(cfg,tomorrow,180,paused=True)
        assert S.clock_now(cfg)==(DAY,1620)
    S.tick_attraction(current.attraction_id)
    data=board(current)
    assert data['date']==DAY and data['status']=='live'


def test_committed_confirmation_is_idempotent_and_recomputes_others(current):
    p = person(current)
    body = {'action': 'depart', 'pid': '5', 'confirmation_token': p['confirmationTokens']['depart'],
            'expected_line': p['assign']['line']}
    first = act(current, body)
    assert first['executed']
    after = board(current)
    assert act(current, body)['duplicate']
    assert board(current)['lines'] == after['lines']
    assert person(current)['state'] == 'onpost'


def test_line_change_requires_reconfirmation_but_does_not_reset_deadline(current):
    p = person(current)
    token = p['confirmationTokens']['depart']
    act(current, {'action': 'sort_time', 'line': 'B', 'time': '00:00:01'})
    result = act(current, {'action': 'depart', 'pid': '5', 'confirmation_token': token, 'expected_line': 'A'})
    assert result['line_changed'] and result['line'] == 'B'
    assert person(current)['state'] == 'rest' and person(current)['readyAt'] == 600
    assert act(current, {'action': 'depart', 'pid': '5', 'confirmation_token': token, 'expected_line': 'B'})['executed']


def test_two_simultaneous_clicks_execute_exactly_once(current):
    p = person(current)
    body = {'action': 'depart', 'pid': '5', 'confirmation_token': p['confirmationTokens']['depart'], 'expected_line': 'A'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: act(current, body), range(2)))
    assert sum(bool(x.get('executed')) for x in results) == 1
    assert sum(bool(x.get('duplicate')) for x in results) == 1


def test_timeout_excludes_person_screen_cannot_drag_and_manager_preserves_evidence(current):
    with current.factory() as db, S.transaction(db):
        cfg = S.load_config(db, current.attraction_id)
        S.set_sim_clock(cfg, DAY, 603, paused=True)
    S.tick_attraction(current.attraction_id)
    assert person(current)['state'] == 'exception' and person(current)['assign'] is None
    screen = SimpleNamespace(kind='screen', formal=False, adjustment=False, actor_id=None, name='大屏')
    body={'action': 'place', 'pid': '5', 'line': 'A', 'i': 0, 'place_mode': 'swap',
          'expected_snapshot': board(current)['forecastSnapshot']}
    with pytest.raises(S.ActionError,match='大屏只可'):act(current,body,screen)
    assert person(current)['state']=='exception'
    act(current,body,'主管')
    assert person(current)['state'] == 'onpost'
    with current.factory() as db:
        log = db.query(RotationEvent).filter_by(event_type='manual_forecast_complete').one()
        assert 'timeout' in log.detail_json and '603' in log.detail_json


def test_all_close_moves_people_to_pending_and_pool_controls_restore(current):
    act(current, {'action': 'all_close'})
    data = board(current)
    assert not any(post['occ'] for L in data['lines'] for post in L['posts'])
    assert all(person(current, pid)['state'] == 'pending' for pid in ['1', '2', '3', '4'])
    assert person(current)['assign'] is None
    act(current, {'action': 'pool', 'pid': '1', 'pool': 'other'})
    assert person(current, '1')['state'] == 'other'
    act(current, {'action': 'pool', 'pid': '1', 'pool': 'rest'})
    assert person(current, '1')['readyAt'] == 615


def test_rest_duration_edits_refresh_and_validate(current):
    act(current, {'action': 'break_time', 'pid': '5', 'remaining': 25})
    assert person(current)['readyAt'] == 625 and not person(current)['canDepart']
    with pytest.raises(S.ActionError):
        act(current, {'action': 'break_time', 'pid': '5', 'remaining': -1})
    assert person(current)['readyAt'] == 625


def test_sick_waits_for_prefix_relief_and_does_not_reset_head_for_middle_target(current):
    act(current, {'action': 'sick', 'pid': '1'})
    assert person(current, '1')['state'] == 'onpost'
    p = person(current)
    assert p['assign']['kind'] == 'sick'
    act(current, {'action': 'depart', 'pid': '5', 'confirmation_token': p['confirmationTokens']['depart'], 'expected_line': 'A'})
    assert person(current, '1')['state'] == 'sick'
    assert board(current)['lines'][0]['sortAt'] == 435


def test_temporary_person_can_have_same_name_without_creating_employee(current):
    from app.v2_models import Employee
    with current.factory() as db:
        count = db.query(Employee).count()
    for _ in range(2):
        act(current, {'action': 'add_person', 'temporary': True, 'name': '临时甲', 'start': '11:00', 'end': '00:30'})
    people = [p for p in board(current)['persons'] if p['name'] == '临时甲']
    assert len(people) == 2 and people[0]['pid'] != people[1]['pid']
    assert all(p['state'] == 'notyet' and p['end'] == 1470 for p in people)
    with current.factory() as db:
        assert db.query(Employee).count() == count


def test_service_overnight_clock_keeps_previous_day_until_real_end(current):
    with current.factory() as db, S.transaction(db):
        row = S.day_row(db, current.attraction_id, DAY)
        state = S.day_state(row)
        for p in state['persons'].values():
            p['end'] = 1500
        S.save_day(db, row, state)
        cfg = S.load_config(db, current.attraction_id)
        S.set_sim_clock(cfg, '2026-10-05', 10, paused=True)
    with current.factory() as db:
        assert S.clock_now(S.load_config(db, current.attraction_id)) == (DAY, 1450)
    S.tick_attraction(current.attraction_id)
    with current.factory() as db:
        assert S.day_row(db, current.attraction_id, DAY).status == 'live'


def test_commit_failure_does_not_publish_partial_positions(current, monkeypatch):
    p = person(current)
    before = board(current)
    with current.factory() as db:
        monkeypatch.setattr(db, 'commit', lambda: (_ for _ in ()).throw(RuntimeError('failed commit')))
        with pytest.raises(RuntimeError):
            S.do_live_action(db, current.attraction_id, 'test', {'action': 'depart', 'pid': '5',
                             'confirmation_token': p['confirmationTokens']['depart'], 'expected_line': 'A'})
    assert board(current)['lines'] == before['lines']


def test_screen_cannot_change_shift_and_member_cannot_operate_other_people(current):
    screen = SimpleNamespace(kind='screen', formal=False, adjustment=False, actor_id=None, name='大屏')
    with pytest.raises(S.ActionError):
        act(current, {'action': 'shift', 'pid': '5', 'start': '10:00', 'end': '20:00'}, screen)
    member = SimpleNamespace(kind='member', member_employee_no='1', formal=False, adjustment=False)
    with pytest.raises(S.ActionError):
        act(current, {'action': 'pool', 'pid': '5', 'pool': 'rest'}, member)


def test_head_seconds_and_overnight_offset_and_post_schedule_preserve_precision(current):
    act(current, {'action': 'sort_time', 'line': 'A', 'time': '00:00:05', 'day_offset': 1})
    assert board(current)['lines'][0]['sortAt'] == pytest.approx(1440 + 5/60)
    act(current, {'action': 'post', 'line': 'A', 'i': 1, 'open': False})
    assert board(current)['lines'][0]['sortAt'] == 600
    act(current, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '10:00:30'})
    with current.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, current.attraction_id), DAY, 600.5, paused=True)
    S.tick_attraction(current.attraction_id)
    assert board(current)['lines'][0]['posts'][1]['open']
    assert board(current)['lines'][0]['adding']


def test_all_close_releases_manual_hold_and_pool_move_does_not_complete_upstream_task(current):
    act(current, {'action': 'post_hold', 'pid': '1', 'line': 'A', 'i': 0, 'on': True, 'reason': '现场'})
    with pytest.raises(S.ActionError):
        act(current, {'action': 'pool', 'pid': '1', 'pool': 'rest'})
    act(current, {'action': 'all_close'})
    data = board(current)
    assert not any(post['hold'] or post['occ'] for L in data['lines'] for post in L['posts'])
    assert person(current, '1')['state'] == 'pending'
    act(current, {'action': 'line', 'line': 'A', 'active': True})
    assert all(post['open'] and not post['occ'] for post in board(current)['lines'][0]['posts'])


def test_stale_drag_snapshot_rejects_without_double_moving_people(current):
    data = board(current)
    body = {'action': 'place', 'pid': '5', 'line': 'A', 'i': 1, 'place_mode': 'swap',
            'expected_snapshot': data['forecastSnapshot']}
    act(current, body)
    after = board(current)
    with pytest.raises(S.ConfirmationConflict):
        act(current, body)
    assert board(current)['lines'] == after['lines']


def test_personal_meal_choice_keeps_existing_reserve_rule_and_manual_pool_is_explicit_override(current):
    act(current, {'action': 'pool', 'pid': '5', 'pool': 'pending'})
    p = person(current)
    assert not p['mealOptions']['canChoose'] and not p['mealOptions']['forced']
    with pytest.raises(S.ActionError, match='储备不足'):
        act(current, {'action': 'arrive', 'pid': '5', 'confirmation_token': p['confirmationTokens']['arrive'], 'meal_choice': 'meal'})
    act(current, {'action': 'arrive', 'pid': '5', 'confirmation_token': p['confirmationTokens']['arrive'], 'meal_choice': 'rest'})
    assert person(current)['state'] == 'rest'
    act(current, {'action': 'pool', 'pid': '5', 'pool': 'meal'})
    assert person(current)['state'] == 'meal' and person(current)['ate']


def test_expired_scheduled_close_after_hold_cancel_uses_new_close_rule(current):
    act(current, {'action': 'post_hold', 'pid': '1', 'line': 'A', 'i': 0, 'on': True, 'reason': '现场'})
    with current.factory() as db, S.transaction(db):
        row = S.day_row(db, current.attraction_id, DAY)
        state = S.day_state(row)
        state['lines'][0]['posts'][0]['closeAt'] = '09:59'
        S.save_day(db, row, state)
    act(current, {'action': 'post_hold', 'pid': '1', 'line': 'A', 'i': 0, 'on': False})
    data = board(current)
    assert not data['lines'][0]['posts'][0]['open'] and data['lines'][0]['sortAt'] == 600
    assert person(current, '1')['state'] == 'pending'


def test_stale_pool_drag_cannot_pull_someone_back_out_after_they_board(current):
    p = person(current)
    context = {'state': p['state'], 'readyAt': p['readyAt'], 'sequence': p.get('confirmationSequence', 0),
               'line': None, 'postIndex': None}
    act(current, {'action': 'depart', 'pid': '5', 'expected_line': p['assign']['line'],
                  'confirmation_token': p['confirmationTokens']['depart']})
    with pytest.raises(S.ConfirmationConflict):
        act(current, {'action': 'pool', 'pid': '5', 'pool': 'other', 'person_context': context})
    assert person(current)['state'] == 'onpost'
