"""Real isolated SQLite, version-2 service transactions and committed projections."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from threading import Barrier
from types import SimpleNamespace

import pytest

from test_rotation_transaction_boundaries import rotation, DAY
from app.rotation import service as S, forecast as F
from app.rotation.forecast_service import snapshot, search_people
from app.rotation.models import RotationDay, RotationDuty, RotationEvent, RotationSegment, RotationSnapshot


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


@pytest.mark.parametrize('count,skip', [(6, ()), (2, ()), (6, ('快迎1', '铁马入口', '熊出口'))])
def test_new_workday_prearranges_open_early_posts_in_priority_order(rotation, count, skip):
    names = ['迎宾', '快迎1', '小C', '铁马入口', '铁马检票1', '熊出口']
    people = {str(i): {'name': f'员工{i:02d}'} for i in range(1, count + 1)}
    parsed = {'people': people, 'days': {DAY: dict.fromkeys(people, '07:00-16:00')}, 'hours': {}}
    lines = [{'id': f'L{i}', 'standby': name == '熊出口' and name in skip, 'posts': [
        {'name': name, **({'draftClosed': True} if name == '快迎1' and name in skip else {}),
         **({'openAt': '07:10'} if name == '铁马入口' and name in skip else {})},
    ]} for i, name in enumerate(reversed(names))]
    with rotation.factory() as db:
        with S.transaction(db):
            S.load_config(db, rotation.attraction_id).lines_json = json.dumps(lines)
            S.save_roster(db, rotation.attraction_id, parsed, scope='day', file_name='六个早班岗.xlsx',
                          uploader_id=None, uploader_name='测试')
        S.do_draft_action(db, rotation.attraction_id, 'test', {'action': 'draft_generate', 'date': DAY})
        state = S.day_state(S.day_row(db, rotation.attraction_id, DAY))
        post_names = {f"{line['id']}#{i}": post['name'] for line in state['lines']
                      for i, post in enumerate(line['posts'])}
        assert list(map(post_names.__getitem__, state['plan']['seven'])) == [
            name for name in names if name not in skip][:count]
        assert list(state['plan']['seven'].values()) == list(people)[:len(state['plan']['seven'])]
        assert db.query(RotationDuty).filter_by(kind='push7').count() == 0


def test_opening_relief_uses_actual_history_and_other_unseated_staff_rest(rotation):
    aid = rotation.attraction_id
    previous = (date.fromisoformat(DAY) - timedelta(days=8)).isoformat()
    people = {str(i): {'name': f'员工{i}'} for i in range(1, 5)}
    shifts = dict.fromkeys(people, '07:15-16:00')
    shifts['1'] = '07:00-16:00'
    with rotation.factory() as db, S.transaction(db):
        S.load_config(db, aid).lines_json = '[{"id":"A","posts":[{"name":"迎宾"}]}]'
        S.save_roster(db, aid, {'people': people, 'days': {DAY: shifts}, 'hours': {}},
                      scope='day', file_name='早班接替.xlsx', uploader_id=None, uploader_name='测试')
        for pid, minute in [('2', 435), ('2', 455), ('3', 445), ('4', 435)]:
            db.add(RotationEvent(attraction_id=aid, work_date=previous, minute=minute, event_type='depart',
                                 employee_no=pid, actor_type='employee', actor_name='测试',
                                 detail_json='{"purpose":"push7"}'))
        db.add(RotationDuty(attraction_id=aid, work_date=previous, employee_no='4', kind='push7'))
    with rotation.factory() as db:
        S.do_draft_action(db, aid, 'test', {'action': 'draft_generate', 'date': DAY})
        state = S.day_state(S.day_row(db, aid, DAY))
        assert state['plan']['push7'] == ['4']
        S.do_draft_action(db, aid, 'test', {'action': 'publish', 'date': DAY})
    with rotation.factory() as db, S.transaction(db):
        cfg = S.load_config(db, aid)
        S.set_sim_clock(cfg, DAY, 415, paused=True)
        row = S.day_row(db, aid, DAY)
        S.advance(db, cfg, row, S.day_state(row), 415)
    opening = person(rotation, '1')
    reliever = person(rotation, '4')
    assert opening['upcomingShift'] and opening['previewAssign']['entryPost'] == '迎宾'
    assert reliever['upcomingShift'] and reliever['previewAssign']['kind'] == 'push7'
    assert reliever['previewAssign']['target'] == '1' and not reliever['canDepart']
    with rotation.factory() as db, S.transaction(db):
        row = S.day_row(db, aid, DAY)
        state = S.day_state(row)
        S.advance(db, S.load_config(db, aid), row, state, 435)
        assert state['persons']['4']['assign']['target'] == '1'
        assert all(state['persons'][pid]['state'] == 'rest' and state['persons'][pid]['readyAt'] == 450
                   for pid in ('2', '3'))
        assert db.query(RotationDuty).filter_by(work_date=DAY, kind='push7').count() == 0


def test_deployed_legacy_post_names_do_not_silently_fill_other_early_posts(rotation):
    aid = rotation.attraction_id
    people = {str(i): {'name': f'员工{i}'} for i in range(1, 7)}
    roster = {'people': people, 'days': {DAY: dict.fromkeys(people, '07:00-16:00')}, 'hours': {}}
    state = S.E.build_draft(DAY, roster, {'lines': S.E.DEFAULT_LINES}, S.E.DEFAULT_SETTINGS, {}, {}, push7_history={})
    assert list(state['plan']['seven']) == ['B2#1', 'C2#0', 'A2#1']
    assert any('铁马入口、铁马检票1、熊出口' in alert for alert in state['draftAlerts'])

    lines = deepcopy(S.E.DEFAULT_LINES)
    d2 = next(line for line in lines if line['id'] == 'D2')
    d2['standby'] = False
    d2['posts'][0]['name'] = '铁马入口'
    d2['posts'][1]['name'] = '铁马检票1'
    next(line for line in lines if line['id'] == 'D1')['posts'][0]['name'] = '熊出口'
    with rotation.factory() as db, S.transaction(db):
        S.load_config(db, aid).lines_json = json.dumps(lines)
        S.save_roster(db, aid, roster, scope='day', file_name='六岗线路.xlsx', uploader_id=None, uploader_name='测试')
    with rotation.factory() as db:
        S.do_draft_action(db, aid, 'test', {'action': 'draft_generate', 'date': DAY})
        state = S.day_state(S.day_row(db, aid, DAY))
        assert list(state['plan']['seven']) == ['B2#1', 'C2#0', 'A2#1', 'D2#0', 'D2#1', 'D1#0']


def test_enabling_prestart_early_line_fills_only_new_slots_and_keeps_existing_assignments(rotation):
    from app.rotation import daily as D
    aid = rotation.attraction_id
    lines = [{'id': lid, 'standby': lid == 'D2', 'posts': [{'name': name} for name in names]}
             for lid, names in [('B2', ['迎宾']), ('C2', ['快迎1']), ('A2', ['小C']),
                                ('D2', ['铁马入口', '铁马检票1']), ('D1', ['熊出口'])]]
    people = {str(i): {'name': f'员工{i:02d}'} for i in range(1, 13)}
    shifts = {pid: '07:00-16:00' if int(pid) <= 6 else '07:15-16:00' for pid in people}
    with rotation.factory() as db, S.transaction(db):
        S.load_config(db, aid).lines_json = json.dumps(lines)
        S.save_roster(db, aid, {'people': people, 'days': {DAY: shifts}, 'hours': {}},
                      scope='day', file_name='开线.xlsx', uploader_id=None, uploader_name='测试')
    with rotation.factory() as db:
        S.do_draft_action(db, aid, 'test', {'action': 'draft_generate', 'date': DAY})
        row = S.day_row(db, aid, DAY)
        prior = S.day_state(row)
        assert len(prior['plan']['seven']) == 4
        D.edit(db, aid, 'test', DAY, {'action': 'draft_line', 'line': 'D2', 'active': True, 'revision': row.version})
        state = S.day_state(S.day_row(db, aid, DAY))
        assert D.seven_slot_order(state) == ['B2#0', 'C2#0', 'A2#0', 'D2#0', 'D2#1', 'D1#0']
        assert all(state['plan']['seven'][key] == pid for key, pid in prior['plan']['seven'].items())
        assert len(state['plan']['push7']) == 6
        assert all(p['state'] == 'notyet' for p in state['persons'].values())


def morning_relievers(current, *, second_ready=600):
    with current.factory() as db, S.transaction(db):
        aid = current.attraction_id
        row = S.day_row(db, aid, DAY)
        state = S.day_state(row)
        state['lines'][1]['active'] = False
        state['persons']['2'].update(start=420, flags=['7点岗'])
        state['persons']['6'] = dict(state['persons']['5'], pid='6', name='另一个接替人',
                                     forecastOrder='zz', readyAt=second_ready)
        eng, _ = S.engine_for(db, S.load_config(db, aid), row, state)
        F.plan(eng, 600)
        S.save_day(db, row, state)


def depart_body(current, pid):
    p = person(current, pid)
    return {'action': 'depart', 'pid': pid, 'expected_line': p['task']['line'],
            'expected_push7': p['task']['push7Route'], 'confirmation_token': p['confirmationTokens']['depart']}


def restore_latest(current):
    from app.rotation.history import preview
    with current.factory() as db:
        undo = preview(db, current.attraction_id, 'A')
    return act(current, {'action': 'restore_line', 'line': 'A', 'checkpoint': undo['checkpoint'],
                         'expected_snapshot': undo['expected_snapshot'], 'notified': True})


def test_two_distinct_people_cannot_confirm_the_same_morning_target(current):
    morning_relievers(current)
    first, second = depart_body(current, '5'), depart_body(current, '6')
    assert first['expected_push7'] and second['expected_push7'] is None
    second['expected_push7'] = first['expected_push7']  # The other person's stale morning route.

    def submit(body):
        try:
            return act(current, body)
        except S.ActionError:
            return {}

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(submit, [first, second]))
    assert results[0]['executed'] and not results[1].get('executed')
    future = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    with current.factory() as db:
        assert S.completed_push7(db, current.attraction_id, future) == {'5': [1, DAY, 600]}
    assert person(current, '2')['state'] == 'pending'


def test_undo_of_later_same_second_confirmation_preserves_earlier_morning_receipt(current):
    morning_relievers(current)
    first = depart_body(current, '5')
    assert act(current, first)['executed']
    with current.factory() as db:
        prior_segments = {row.id for row in db.query(RotationSegment).filter_by(
            work_date=DAY, employee_no='1', end_min=600)}
        assert prior_segments
    second = depart_body(current, '6')
    assert act(current, second)['executed']
    restore_latest(current)
    assert act(current, first)['duplicate']
    with current.factory() as db:
        state = S.day_state(S.day_row(db, current.attraction_id, DAY))
        assert state['confirmations'][second['confirmation_token']]['status'] == 'reversed'
        assert state['persons']['2']['push7Completion']['owner'] == '5'
        assert {row.id for row in db.query(RotationSegment).filter_by(
            work_date=DAY, employee_no='1', end_min=600)} == prior_segments


def test_timed_out_manual_chain_retry_and_undo_then_redo_count_once(current):
    morning_relievers(current, second_ready=602)
    old_body = depart_body(current, '5')
    aid = current.attraction_id
    future = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    with current.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, aid), DAY, 603, paused=True)
    S.tick_attraction(aid)
    assert person(current, '5')['exception']['assignment']['kind'] == 'push7'
    with pytest.raises(S.ActionError):
        act(current, old_body)
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {}
    manual = {'action': 'place', 'pid': '5', 'line': 'A', 'i': 0, 'place_mode': 'push',
              'expected_snapshot': board(current)['forecastSnapshot']}
    assert act(current, manual)['executed']
    with pytest.raises(S.ConfirmationConflict):
        act(current, manual)
    assert person(current, '2')['state'] == 'pending'
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {'5': [1, DAY, 603]}
    restore_latest(current)
    assert person(current, '5')['state'] == 'exception'
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {}
    redone = depart_body(current, '6')
    assert act(current, redone)['executed']
    assert act(current, redone)['duplicate']
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {'6': [1, DAY, 603]}


def test_concurrent_manual_and_self_confirmation_only_credit_one_reliever(current):
    morning_relievers(current, second_ready=602)
    aid = current.attraction_id
    with current.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, aid), DAY, 603, paused=True)
    S.tick_attraction(aid)
    automatic = depart_body(current, '6')
    assert automatic['expected_push7']
    manual = {'action': 'place', 'pid': '5', 'line': 'A', 'i': 0, 'place_mode': 'push',
              'expected_snapshot': board(current)['forecastSnapshot']}
    barrier = Barrier(2)

    def submit(body):
        barrier.wait(timeout=5)
        try:
            return act(current, body)
        except S.ActionError:
            return {}

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(submit, [manual, automatic]))
    assert sum(bool(result.get('executed')) for result in results) == 1
    winner = '5' if results[0].get('executed') else '6'
    future = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {winner: [1, DAY, 603]}


def test_undo_excludes_a_preexisting_execution_without_a_completion_id(current):
    morning_relievers(current)
    aid = current.attraction_id
    future = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    assert act(current, depart_body(current, '5'))['executed']
    with current.factory() as db, S.transaction(db):
        row = S.day_row(db, aid, DAY)
        state = S.day_state(row)
        state['persons']['2'].pop('push7Completion')
        state['push7History'] = {'5': [1, DAY, 600]}
        S.save_day(db, row, state)
        execution = db.query(RotationEvent).filter_by(work_date=DAY, employee_no='5', event_type='depart').one()
        detail = json.loads(execution.detail_json)
        detail.pop('push7_execution')
        detail.pop('target')
        execution.detail_json = json.dumps(detail)
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {'5': [1, DAY, 600]}
    restore_latest(current)
    with current.factory() as db:
        state = S.day_state(S.day_row(db, aid, DAY))
        assert state.get('push7History', {}) == {}
    assert act(current, depart_body(current, '5'))['executed']
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {'5': [1, DAY, 600]}


def test_push7_counts_only_committed_executions_across_retries_and_restore(current):
    aid = current.attraction_id
    previous = (date.fromisoformat(DAY) - timedelta(days=8)).isoformat()
    future = (date.fromisoformat(DAY) + timedelta(days=1)).isoformat()
    with current.factory() as db, S.transaction(db):
        db.add_all([
            RotationEvent(attraction_id=aid, work_date=previous, minute=435, event_type='push7',
                          employee_no='5', actor_type='system', actor_name='测试', detail_json='{"target":"2"}'),
            RotationEvent(attraction_id=aid, work_date=previous, minute=435, event_type='depart',
                          employee_no='5', actor_type='system', actor_name='测试', detail_json='{"task":"legacy"}'),
            RotationDuty(attraction_id=aid, work_date=previous, employee_no='5', kind='push7'),
        ])
        row = S.day_row(db, aid, DAY)
        state = S.day_state(row)
        state['persons']['2'].update(start=420, flags=['7点岗'])
        state.pop('push7History', None)
        S.save_day(db, row, state)
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {'5': [1, previous, 435]}
    with current.factory() as db, S.transaction(db):
        row = S.day_row(db, aid, DAY)
        S.advance(db, S.load_config(db, aid), row, S.day_state(row), 600)
    p = person(current)
    assert p['assign']['kind'] == 'push7' and p['task']['push7Route']
    body = {'action': 'depart', 'pid': '5', 'expected_line': p['task']['line'],
            'expected_push7': p['task']['push7Route'], 'confirmation_token': p['confirmationTokens']['depart']}
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: act(current, body), range(2)))
    assert sum(bool(result.get('duplicate')) for result in results) == 1
    assert sum(bool(result.get('executed')) for result in results) == 1
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {'5': [2, DAY, 600]}
        logs = db.query(RotationEvent).filter_by(work_date=DAY, employee_no='5', event_type='depart').all()
        assert len(logs) == 1 and json.loads(logs[0].detail_json)['purpose'] == 'push7'
        assert db.query(RotationDuty).filter_by(work_date=DAY, kind='push7').count() == 0
        from app.rotation.history import preview
        undo = preview(db, aid, body['expected_line'])
    act(current, {'action': 'restore_line', 'line': body['expected_line'], 'checkpoint': undo['checkpoint'],
                  'expected_snapshot': undo['expected_snapshot'], 'notified': True})
    with current.factory() as db:
        assert S.completed_push7(db, aid, future) == {'5': [1, previous, 435]}


@pytest.mark.parametrize('end,now,expected', [(625,599+59/60,None),(625,600,'推下班'),(625,625,'推下班'),
                                           (1460,1434+59/60,None),(1460,1435,'推下班'),(1470,1445,'推下班')])
def test_preparing_shift_end_uses_shared_25_minute_boundary_and_workday_clock(current,end,now,expected):
    with current.factory() as db:
        row=S.day_row(db,current.attraction_id,DAY);state=S.day_state(row)
        eng,_=S.engine_for(db,S.load_config(db,current.attraction_id),row,state)
        p=eng.P['1'];p.update(start=430,end=end)
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
        eng.P['1'].update(start=430,end=625)
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


@pytest.mark.parametrize('start', [620, 1450])
def test_upcoming_shift_forecasts_at_20_minutes_without_starting_a_task(current, start):
    aid = current.attraction_id
    with current.factory() as db, S.transaction(db):
        cfg = S.load_config(db, aid)
        row = S.day_row(db, aid, DAY)
        state = S.day_state(row)
        state['lastTick'] = start - 20 - 1 / 60
        for p in state['persons'].values():
            p['end'] = start + 120
        for key, pid in list(state['plan']['crew'].items()):
            if pid == '5':
                del state['plan']['crew'][key]
        state['persons']['5'].update(start=start, state='notyet', readyAt=None, assign=None, breakKind=None)
        S.set_sim_clock(cfg, DAY, state['lastTick'], paused=True)
        S.save_day(db, row, state)

    def move_clock(at):
        day_offset, minute = divmod(at, 1440)
        date_text = (date.fromisoformat(DAY) + timedelta(days=int(day_offset))).isoformat()
        with current.factory() as db, S.transaction(db):
            cfg = S.load_config(db, aid)
            row = S.day_row(db, aid, DAY)
            S.set_sim_clock(cfg, date_text, minute, paused=True)
            return S.advance(db, cfg, row, S.day_state(row), at)

    assert not person(current)['upcomingShift']
    assert move_clock(start - 20), 'The 20-minute boundary must refresh connected views'
    preview = person(current)
    assert preview['upcomingShift'] and preview['previewAssign']['line'] == 'A'
    assert preview['state'] == 'notyet' and preview['assign'] is None and preview['task'] is None
    assert preview['breakStart'] is None and not preview['canDepart'] and not preview['canArrive']
    assert preview['confirmationTokens'] == {}
    with current.factory() as db:
        screen = next(p for p in S.screen_payload(db, aid)['day']['persons'] if p['pid'] == '5')
        member = S.member_payload(db, aid, '5')['person']
        assert preview['previewAssign'] == screen['previewAssign'] == member['previewAssign']
        state = S.day_state(S.day_row(db, aid, DAY))
        eng, _ = S.engine_for(db, S.load_config(db, aid), S.day_row(db, aid, DAY), state)
        token = F.token(eng, eng.P['5'], 'depart')
    with pytest.raises(S.ActionError):
        act(current, {'action': 'depart', 'pid': '5', 'expected_line': 'A', 'confirmation_token': token})

    act(current, {'action': 'sort_time', 'line': 'B', 'time': '00:00:00'})
    assert person(current)['previewAssign']['line'] == 'B'
    move_clock(start - 1 / 60)
    waiting = person(current)
    assert waiting['state'] == 'notyet' and not waiting.get('exception')
    move_clock(start)
    arrived = person(current)
    assert arrived['state'] == 'ready' and arrived['canDepart'] and not arrived['canArrive']
    assert arrived['awaitingShiftEntry'] and not arrived['upcomingShift']
    assert arrived['readyAt'] == start and arrived['breakStart'] is None
    result = act(current, {'action': 'depart', 'pid': '5', 'expected_line': 'A',
                           'confirmation_token': arrived['confirmationTokens']['depart']})
    assert result['line_changed'] and person(current)['awaitingShiftEntry']
    result = act(current, {'action': 'depart', 'pid': '5', 'expected_line': result['line'],
                           'confirmation_token': arrived['confirmationTokens']['depart']})
    assert result['executed']
    assert person(current)['state'] == 'onpost' and not person(current)['awaitingShiftEntry']
    with current.factory() as db:
        assert db.query(RotationEvent).filter_by(work_date=DAY, employee_no='5', event_type='forecast_timeout').count() == 0


def test_preparing_keeps_sickness_priority_and_shift_change_recalculates(current):
    from app.rotation.forecast_service import person_view
    with current.factory() as db:
        row=S.day_row(db,current.attraction_id,DAY);state=S.day_state(row)
        eng,_=S.engine_for(db,S.load_config(db,current.attraction_id),row,state)
        p=eng.P['1'];p.update(start=430,end=625,requestedExit='out')
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


@pytest.mark.parametrize('enable_at', [629 + 59 / 60, 630, 631])
@pytest.mark.parametrize('scheduled_while_active', [False, True])
def test_scheduled_addition_only_executes_if_line_is_enabled_at_due_time(current, enable_at, scheduled_while_active):
    r = current
    act(r, {'action': 'line', 'line': 'B', 'active': False})
    if scheduled_while_active:
        act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': False})
    else:
        act(r, {'action': 'line', 'line': 'A', 'active': False})
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '10:30:00'})
    if scheduled_while_active:
        act(r, {'action': 'line', 'line': 'A', 'active': False})
    act(r, {'action': 'sort_time', 'line': 'A', 'time': '08:38:00'})
    act(r, {'action': 'break_time', 'pid': '5', 'remaining': 30})
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, enable_at, paused=True)
    # Advance inside the enable action so the exact-time case uses the previous line state.
    act(r, {'action': 'line', 'line': 'A', 'active': True})
    if enable_at < 630:
        data = board(r)
        before = data['lines'][0]
        assert not before['posts'][1]['open'] and not before['posts'][1]['openExpired']
        act(r, {'action': 'place', 'pid': '1', 'line': 'A', 'i': 0, 'place_mode': 'swap',
                'expected_snapshot': data['forecastSnapshot']})
        with r.factory() as db, S.transaction(db):
            S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, 630, paused=True)
        S.tick_attraction(r.attraction_id)
        opened = board(r)['lines'][0]
        assert opened['posts'][1]['open'] and opened['adding'] and opened['sortAt'] == 518
        assert person(r)['task']['kind'] == 'opening'
        assert act(r, {**depart_body(r, '5'), 'expected_kind': 'opening'})['executed']
        assert board(r)['lines'][0]['sortAt'] == 518 and not board(r)['lines'][0]['adding']
    else:
        expired = board(r)['lines'][0]
        assert expired['active'] and expired['posts'][0]['open']
        assert expired['posts'][1]['openExpired'] and not expired['posts'][1]['open']
        assert not expired['adding'] and expired['sortAt'] == 518
        assert (person(r)['assign'] or {}).get('kind') != 'opening'
        act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '10:40:00'})
        rescheduled = board(r)['lines'][0]['posts'][1]
        assert not rescheduled['openExpired'] and not rescheduled['opened'] and not rescheduled['open']


def test_scheduling_for_the_current_second_does_not_enable_an_inactive_line(current):
    r = current
    act(r, {'action': 'line', 'line': 'A', 'active': False})
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '10:00:00'})
    line = board(r)['lines'][0]
    assert not line['active'] and not line['posts'][1]['open'] and line['posts'][1]['openExpired']


def test_immediate_opening_reactivates_line_and_preserves_its_other_scheduled_addition(current):
    r = current
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': False})
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '10:30:00'})
    act(r, {'action': 'line', 'line': 'A', 'active': False})
    act(r, {'action': 'sort_time', 'line': 'A', 'time': '08:38:00'})
    act(r, {'action': 'post', 'line': 'A', 'i': 0, 'open': True})
    before = board(r)['lines'][0]
    assert before['active'] and before['posts'][0]['open'] and not before['posts'][1]['open']
    with r.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, 630, paused=True)
    S.tick_attraction(r.attraction_id)
    after = board(r)['lines'][0]
    assert after['posts'][1]['open'] and not after['posts'][1]['openExpired'] and after['sortAt'] == 518


def test_daily_prestart_enable_expires_a_missed_addition_without_waiting_for_the_ticker(rotation):
    from app.rotation import daily as D
    r = rotation
    with r.factory() as db:
        S.do_draft_action(db, r.attraction_id, 'test', {'action': 'draft_generate', 'date': DAY})
        S.do_draft_action(db, r.attraction_id, 'test', {'action': 'publish', 'date': DAY})
    act(r, {'action': 'line', 'line': 'A', 'active': False})
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True, 'openAt': '06:45:00'})
    with r.factory() as db:
        with S.transaction(db):
            S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, 406, paused=True)
        row = S.day_row(db, r.attraction_id, DAY)
        D.edit(db, r.attraction_id, 'test', DAY, {'action': 'draft_line', 'line': 'A', 'active': True,
                                                'plan_revision': D.plan_revision(S.day_state(row))})
    S.tick_attraction(r.attraction_id)
    line = board(r)['lines'][0]
    assert line['active'] and not line['adding'] and line['sortAt'] == 0
    assert line['posts'][1]['openExpired'] and not line['posts'][1]['open']
    with r.factory() as db:
        state = S.day_state(S.day_row(db, r.attraction_id, DAY))
        assert state['lines'][0]['posts'][1]['draftClosed']


def test_stale_opening_confirmation_requires_reconfirmation_before_updating_header(current):
    r = current
    act(r, {'action': 'line', 'line': 'B', 'active': False})
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': False})
    act(r, {'action': 'sort_time', 'line': 'A', 'time': '08:38:00'})
    act(r, {'action': 'post', 'line': 'A', 'i': 1, 'open': True})
    p = person(r)
    assert p['task']['kind'] == 'opening'
    body = {**depart_body(r, '5'), 'expected_kind': 'opening'}
    act(r, {'action': 'add_person', 'temporary': True, 'name': '现场加岗支援', 'start': '07:15', 'end': '16:15'})
    replacement = next(p['pid'] for p in board(r)['persons'] if p['name'] == '现场加岗支援')
    act(r, {'action': 'pool', 'pid': replacement, 'pool': 'rest'})
    act(r, {'action': 'break_time', 'pid': replacement, 'remaining': 0})
    # Either employee may confirm the same line. The first consumes the addition.
    assert act(r, {**depart_body(r, replacement), 'expected_kind': 'opening'})['executed']
    line = board(r)['lines'][0]
    assert not line['adding'] and line['sortAt'] == 518
    updated = act(r, body)
    assert updated['line_changed'] and updated['task']['kind'] == 'push'
    assert board(r)['lines'][0]['sortAt'] == 518 and person(r)['state'] == 'rest'
    assert act(r, {**body, 'expected_kind': updated['task']['kind']})['executed']
    assert board(r)['lines'][0]['sortAt'] == 600


def test_old_day_opening_marker_can_be_manually_completed_with_current_snapshot(current):
    r = current
    with r.factory() as db, S.transaction(db):
        row = S.day_row(db, r.attraction_id, DAY)
        state = S.day_state(row)
        post = state['lines'][0]['posts'][0]
        post.update(occ=None, openRequest=True)
        state['persons']['1'].update(state='pending', line=None, lineStart=None)
        eng, _ = S.engine_for(db, S.load_config(db, r.attraction_id), row, state)
        F.plan(eng, 600)
        post.pop('openRequest')
        state['lines'][0]['adding'] = True
        S.save_day(db, row, state)
    data = board(r)
    assert data['lines'][0]['adding']
    assert act(r, {'action': 'place', 'pid': '5', 'line': 'A', 'i': 0, 'place_mode': 'push',
                   'update_sort_time': True, 'expected_snapshot': data['forecastSnapshot']})['executed']
    line = board(r)['lines'][0]
    assert line['posts'][0]['occ'] == '5' and line['sortAt'] == 435 and not line['adding']


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


def test_personal_meal_choice_and_rest_conversion_require_reserve(current):
    act(current, {'action': 'pool', 'pid': '5', 'pool': 'pending'})
    p = person(current)
    assert not p['mealOptions']['canChoose'] and not p['mealOptions']['forced']
    with pytest.raises(S.ActionError, match='储备不足'):
        act(current, {'action': 'arrive', 'pid': '5', 'confirmation_token': p['confirmationTokens']['arrive'], 'meal_choice': 'meal'})
    act(current, {'action': 'arrive', 'pid': '5', 'confirmation_token': p['confirmationTokens']['arrive'], 'meal_choice': 'rest'})
    assert person(current)['state'] == 'rest'
    with pytest.raises(S.ActionError, match='储备不足'):
        act(current, {'action': 'pool', 'pid': '5', 'pool': 'meal'})
    assert person(current)['readyAt'] == 615 and not person(current)['ate']
    act(current, {'action': 'pool', 'pid': '5', 'pool': 'pending'})
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
