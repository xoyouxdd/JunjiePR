
from copy import deepcopy
import json

import pytest

from app.rotation import forecast as F, forecast_service as API, history as H, service as S
from app.rotation.models import RotationEvent, RotationNotice, RotationSegment
from test_rotation_forecast import fixture, occupants
from test_rotation_forecast_api import act, board, current, person, rotation, DAY


def batch_fixture(counts, ready):
    eng = fixture(counts, now=807)
    template = deepcopy(eng.P['new0'])
    for pid in ('new0', 'new1', 'new2', 'new3'):
        del eng.P[pid]
    for n, line in enumerate(eng.d['lines']):
        line['sortAt'] = 780 + n
    for n in range(16):
        pid = f'late{n:02}'
        eng.P[pid] = dict(template, pid=pid, name=pid, start=810, end=1260,
                          state='notyet', readyAt=None, forecastOrder=pid)
        if n < ready:
            eng.P[pid]['batchCheckedInFor'] = 810
    return eng


@pytest.mark.parametrize('counts,expected', [
    ((2, 3), 'ABABB'),
    ((1, 2, 3, 2, 3), 'ABCDEBCDECE'),
])
def test_batch_skips_protected_workers_and_rests_overflow(counts, expected):
    eng = batch_fixture(counts, len(expected) + 1)
    F.tick(eng, 807, advance_batches=True)
    results = [eng.P[f'late{i:02}']['batchResult'] for i in range(len(expected) + 1)]
    assert ''.join(r['line'] for r in results[:-1]) == expected
    for n, line in enumerate(eng.d['lines']):
        lid = line['id']
        ids = [f'late{i:02}' for i, choice in enumerate(expected) if choice == lid]
        assert occupants(eng, lid) == ids[::-1]
        assert line['sortAt'] == 810 + expected.rindex(lid) / 60
        for pid in ids:
            result = eng.P[pid]['batchResult']
            assert result['handoff']['outcome'] == 'pending'
            assert result['post'] == line['posts'][occupants(eng, lid).index(pid)]['name']
            assert [p['occ'] for p in result['afterPosts']] == occupants(eng, lid)
            assert all(p['occ'].startswith(lid) for p in result['beforePosts'])
    assert results[-1]['restUntil'] == 822
    assert eng.P['late15']['state'] == 'notyet'
    stand = deepcopy(eng.d['lines'])
    F.tick(eng, 807, advance_batches=True)
    assert eng.d['lines'] == stand
    F.tick(eng, 810, advance_batches=True)
    assert eng.P['late15']['state'] == 'rest'
    assert API.person_view(eng, eng.P['late15'], 810)['canDepart']


@pytest.mark.parametrize('off, arrivals, headings', [
    ((1, 3), (1, 2, 3, 4), ('4推', '推下班', '', '推下班')),
    ((0,), (1, 2, 3, 4), ('推下班', '3推', '', '')),
    ((1,), (1, 2, 3, 4), ('4推', '推下班', '', '')),
    ((1,), (4, 3, 1), ('3推', '推下班', '2推')),
])
def test_batch_handoff_follows_original_worker_at_each_final_post(off, arrivals, headings):
    eng = batch_fixture((5,), len(arrivals))
    names = ('塔台', '上客一', '上协一', '下协一', '大出口')
    for post, name in zip(eng.line('A')['posts'], names):
        post['name'] = name
    for i in off:
        eng.P[f'A{i}']['end'] = 807
    F.tick(eng, 807, advance_batches=True)
    notices = API.notices(eng, 807)
    for i, (person, task) in enumerate(zip(reversed(arrivals), headings)):
        pid = f'late{arrivals.index(person):02}'
        result = API.batch_result_view(eng.P[pid]['batchResult'])
        title = f'A线 · {names[i]}' + (f' · {task}' if task else '')
        assert result['heading'] == title
        assert result['instruction'].startswith(f'通知原{names[i]}的A{i}')
        assert notices[(pid, 'batch')][2] == f"{title}。{result['instruction']}"
        if task == '推下班':
            assert result['instruction'].endswith('下班')
        elif not task:
            assert result['instruction'].endswith('下岗，回休息室')
        else:
            assert result['instruction'].endswith(f"去{names[i + int(task[0])]}")


def test_batch_uses_resting_workers_first_and_original_time_for_tail_relief():
    eng = batch_fixture((2, 3), 1)
    eng.P['resting'] = dict(eng.P['late00'], pid='resting', name='resting', state='rest',
                           start=435, end=1200, readyAt=808)
    eng.P['A1']['end'] = 820
    F.tick(eng, 807, advance_batches=True)
    assert eng.P['A1']['state'] == 'done'
    assert eng.P['resting']['batchResult']['handoff'] == {
        'post': 'A0', 'name': 'A0', 'outcome': 'post', 'destination': 'A1', 'steps': 1}
    assert eng.line('A')['sortAt'] == eng.P['A0']['lineStart'] == 808
    assert eng.P['resting']['batchResult']['line'] == 'A'
    assert eng.P['late00']['batchResult']['line'] == 'B'


def test_conflict_at_next_second_preserves_round_robin_in_preview_and_execution():
    eng = batch_fixture((3,) * 8, 0)
    template = deepcopy(eng.P.pop('late00'))
    for i in range(19):
        pid = f'new{i:02}'
        eng.P[pid] = dict(template, pid=pid, start=435, end=1200, state='rest',
                          readyAt=810, forecastOrder=pid)
    expected = 'ABCDEFGHABCDEFGHABC'
    F.plan(eng, 810)
    assert ''.join(eng.P[f'new{i:02}']['assign']['line'] for i in range(19)) == expected
    for i, line in enumerate(expected):
        now = 810 if i < 8 else 810 + 1/60
        assert F.execute(eng, f'new{i:02}', now, 'test', line)['executed']
        assert eng.line(line)['sortAt'] == 810 + i/60
        assert eng.P[f'new{i:02}']['lineStart'] == eng.line(line)['sortAt']


@pytest.mark.parametrize('last,second,expected', [(2, 9, (False, False)), (3, 9, (True, False)),
                                                   (2, 10, (False, True)), (3, 10, (True, True))])
def test_late_shift_thresholds_are_independent(last, second, expected):
    eng = fixture()
    template = eng.P['new0']
    for start, count in ((650, 20), (700, second), (750, last)):
        for i in range(count):
            pid = f'{start}-{i}'
            eng.P[pid] = dict(template, pid=pid, start=start, end=900, forecastOrder=pid)
    assert not F.late_shift(eng, eng.P['650-0'])
    assert tuple(F.late_shift(eng, eng.P[f'{start}-0']) for start in (750, 700)) == expected


def test_late_shift_works_last_twenty_minutes_and_ordinary_exit_is_inclusive():
    eng = fixture()
    for p in eng.P.values():
        p.update(start=750 if p['state']=='onpost' else 430, end=900)
    for i in range(3):
        eng.P[f'new{i}'].update(start=750, readyAt=885 if i == 0 else 895)
    eng.P['new3'].update(state='pending')
    eng.P['A2'].update(start=430, end=910)
    F.tick(eng, 870)
    assert eng.P['new3']['state'] == 'done'
    assert not eng.preparing_shift_end(eng.P['B0'], 885)
    assert eng.can_board(eng.P['new0'], 885, 885)
    assert F.execute(eng, 'new0', 885, 'test', 'A')['executed']
    assert eng.P['A2']['state'] == 'done'
    assert F.execute(eng, 'new1', 895, 'test', 'B')['executed']
    assert eng.P['new1']['state'] == 'onpost'


def test_due_close_on_departing_worker_needs_no_relief():
    eng = batch_fixture((2, 3), 1)
    eng.P['A1']['end'] = 807
    eng.line('A')['posts'][1]['closeAt'] = '13:27'
    F.tick(eng, 807, advance_batches=True)
    assert eng.P['A1']['state'] == 'done'
    assert eng.line('A')['sortAt'] == 807
    assert eng.P['late00']['batchResult']['line'] == 'B'


def test_check_in_window_and_batch_commit_with_isolated_database(current, monkeypatch):
    r = current
    with r.factory() as db, S.transaction(db):
        cfg = S.load_config(db, r.attraction_id)
        row = S.day_row(db, r.attraction_id, DAY)
        state = S.day_state(row)
        base = state['persons']['5']
        for i in range(16):
            pid = f'{101+i}'
            state['persons'][pid] = dict(base, pid=pid, name=pid, state='notyet', start=810,
                                         end=1260, readyAt=None, forecastOrder=pid)
        state['persons']['5']['readyAt'] = 808
        S.save_day(db, row, state)
        S.set_sim_clock(cfg, DAY, 794, paused=True)
    def advance(minute):
        with r.factory() as db, S.transaction(db):
            S.set_sim_clock(S.load_config(db, r.attraction_id), DAY, minute, paused=True)
        S.tick_attraction(r.attraction_id)
    advance(794)
    assert person(r, '101')['upcomingShift']
    assert not person(r, '101')['canBatchCheckIn']
    advance(795)
    worker = person(r, '101')
    assert worker['canBatchCheckIn']
    payload = dict(action='batch_check_in', pid='101', confirmation_token=worker['confirmationTokens']['batch_check_in'])
    assert act(r, payload)['executed']
    assert act(r, payload)['duplicate']
    assert person(r, '101')['batchCheckedIn']
    with r.factory() as db:
        cfg = S.load_config(db, r.attraction_id)
        row = S.day_row(db, r.attraction_id, DAY)
        with monkeypatch.context() as patch:
            def fail_commit():
                raise RuntimeError('injected batch commit failure')
            patch.setattr(db, 'commit', fail_commit)
            with pytest.raises(RuntimeError, match='injected batch commit failure'), S.transaction(db):
                S.advance(db, cfg, row, S.day_state(row), 807)
    assert person(r, '101')['state'] == 'notyet'
    advance(807)
    assert person(r, '101')['state'] == 'onpost'
    assert person(r, '5')['state'] == 'onpost'
    assert person(r, '102')['state'] == 'notyet'
    advance(807)
    with r.factory() as db:
        assert db.query(RotationEvent).filter_by(event_type='depart', actor_type='system').count() == 2
        assert db.query(RotationNotice).filter_by(kind='batch_result', status='open').count() == 2
        assert {seg.end_min for seg in db.query(RotationSegment)} == {808, 810}
    advance(810)
    assert person(r, '102')['state'] == 'rest'


def test_scheduled_close_inside_relief_path_waits_for_relief():
    eng = batch_fixture((3, 3), 1)
    eng.P['A2']['end'] = 807
    eng.line('A')['posts'][1]['closeAt'] = '13:27'
    F.tick(eng, 807, advance_batches=True)
    assert eng.P['A2']['state'] == 'done'
    assert occupants(eng, 'A') == ['late00', None, 'A1']
    assert eng.line('A')['sortAt'] == 807
    assert eng.P['late00']['batchResult']['handoff'] == {
        'post': 'A0', 'name': 'A0', 'outcome': 'pending'}


def test_fifteen_workers_do_not_open_sign_in_or_execute_batch():
    eng = batch_fixture((2, 3), 2)
    del eng.P['late15']
    p = eng.P['late00']
    assert not F.can_check_in(eng, p, 795)
    F.tick(eng, 807, advance_batches=True)
    assert p['state'] == 'notyet'


def test_direct_shift_entry_waits_for_a_route_before_starting_timeout():
    eng = fixture()
    p = eng.P['new0']
    p.update(state='notyet', start=810, readyAt=None, breakStart=None)
    for pid in ('new1', 'new2', 'new3'):
        eng.P[pid]['state'] = 'other'
    for line in eng.d['lines']:
        line['active'] = False
    F.tick(eng, 810)
    F.tick(eng, 815)
    waiting = API.person_view(eng, p, 815)
    assert waiting['awaitingShiftEntry'] and not waiting['canDepart']
    assert p.get('timeoutAt') is None

    eng.line('A')['active'] = True
    F.tick(eng, 816)
    ready = API.person_view(eng, p, 816)
    assert ready['awaitingShiftEntry'] and ready['canDepart']
    assert ready['task']['deadlineAt'] == 819
    assert F.execute(eng, p['pid'], 816, 'test', 'A')['executed']
    assert not API.person_view(eng, p, 816)['awaitingShiftEntry']


def test_direct_shift_entry_is_not_picked_up_by_a_later_batch():
    eng = batch_fixture((2, 3), 1)
    eng.P['direct'] = dict(eng.P['late15'], pid='direct', start=807, state='notyet')
    F.tick(eng, 807, advance_batches=True)
    assert F.awaiting_shift_entry(eng.P['direct'])
    assert not eng.P['direct'].get('batchResult')
    assert eng.P['late00']['state'] == 'onpost'


def test_opening_shifts_of_sixteen_keep_opening_rest_and_personal_push7(rotation):
    aid = rotation.attraction_id
    names = ['迎宾', '快迎1', '小C', '铁马入口', '铁马检票1', '熊出口']
    early = [str(i) for i in range(1, 17)]
    relief = [str(i) for i in range(17, 33)]
    people = {pid: {'name': f'员工{int(pid):02d}'} for pid in early + relief}
    shifts = {**dict.fromkeys(early, '07:00-16:00'), **dict.fromkeys(relief, '07:15-16:00')}
    with rotation.factory() as db, S.transaction(db):
        S.load_config(db, aid).lines_json = json.dumps([
            {'id': str(i), 'posts': [{'name': name}]} for i, name in enumerate(reversed(names))
        ])
        S.save_roster(db, aid, {'people': people, 'days': {DAY: shifts}, 'hours': {}},
                      scope='day', file_name='隔离早班.xlsx', uploader_id=None, uploader_name='测试')
        # The 07:00 employees who finish resting at 07:15 also compete for
        # relief. Give them a prior completion to exercise history ordering.
        db.add_all(RotationEvent(
            attraction_id=aid, work_date='2026-10-03', minute=435, employee_no=pid,
            event_type='depart', actor_type='employee', actor_name='测试',
            detail_json='{"purpose":"push7"}',
        ) for pid in early[6:])
    with rotation.factory() as db:
        S.do_draft_action(db, aid, 'test', {'action': 'draft_generate', 'date': DAY})
        S.do_draft_action(db, aid, 'test', {'action': 'publish', 'date': DAY})

    def advance(minute):
        with rotation.factory() as db, S.transaction(db):
            cfg = S.load_config(db, aid)
            S.set_sim_clock(cfg, DAY, minute, paused=True)
        S.tick_attraction(aid)
        day = board(rotation)
        return {p['pid']: p for p in day['persons']}, day['lines']

    for minute in (405, 417):
        by_id, _ = advance(minute)
        assert all(by_id[pid]['upcomingShift'] and not by_id[pid]['canBatchCheckIn']
                   and by_id[pid]['state'] == 'notyet' for pid in early)
        with rotation.factory() as db:
            assert not S.day_state(S.day_row(db, aid, DAY)).get('dailyOpening')
    by_id, lines = advance(420)
    assert [line['posts'][0]['occ'] for line in reversed(lines)] == early[:6]
    assert all(by_id[pid]['state'] == 'rest' and by_id[pid]['readyAt'] == 435 for pid in early[6:])
    assert all(by_id[pid]['upcomingShift'] and not by_id[pid]['canBatchCheckIn'] for pid in relief)
    by_id, before_relief = advance(432)
    assert all(by_id[pid]['state'] == 'notyet' for pid in relief)
    by_id, at_relief = advance(435)
    assert [line['posts'][0]['occ'] for line in at_relief] == [line['posts'][0]['occ'] for line in before_relief]
    assert all(by_id[pid]['state'] == 'rest' and by_id[pid]['readyAt'] == 450 for pid in relief[6:])
    assert all(by_id[pid]['canDepart'] and by_id[pid]['assign']['kind'] == 'push7' for pid in relief[:6])
    assert not any(p.get('batchResult') for p in by_id.values())
    with rotation.factory() as db:
        state = S.day_state(S.day_row(db, aid, DAY))
        assert state['dailyOpening']['captured_at'] == 420
        assert not state.get('completedShiftBatches')
        assert not db.query(RotationEvent).filter_by(work_date=DAY, event_type='depart').count()

    from types import SimpleNamespace
    pid = relief[0]
    worker = by_id[pid]
    target = worker['assign']['target']
    request = {'action': 'depart', 'pid': pid, 'expected_line': worker['assign']['line'],
               'confirmation_token': worker['confirmationTokens']['depart']}
    with pytest.raises(S.ActionError, match='本人待办'):
        act(rotation, request, actor=SimpleNamespace(kind='screen', actor_id=None, name='大屏'))
    assert person(rotation, target)['state'] == 'onpost'
    result = act(rotation, request, actor=SimpleNamespace(
        kind='member', member_employee_no=pid, actor_id=None, name=pid))
    assert result['executed']
    assert person(rotation, pid)['state'] == 'onpost'
    assert person(rotation, target)['state'] == 'pending'


def test_batch_push7_accepts_attendee_before_their_scheduled_start():
    eng = batch_fixture((2, 3), 1)
    eng.P['B0']['start'] = 420
    F.tick(eng, 807, advance_batches=True)
    assert eng.P['B0']['state'] == 'pending'
    assert eng.P['late00']['batchResult']['handoff']['outcome'] == 'pending'


def test_batch_write_preserves_closure_event_order(rotation):
    r = rotation
    eng = batch_fixture((2, 3), 4)
    eng.P['A1']['end'] = 807
    eng.line('A')['posts'][1]['closeAt'] = '13:27'
    state = eng.d
    state['date'] = DAY
    expected = deepcopy(state)
    F.tick(type(eng)(expected, eng.S, eng.h), 807, advance_batches=True)

    from app.rotation.models import RotationDay
    with r.factory() as db, S.transaction(db):
        cfg = S.load_config(db, r.attraction_id)
        row = RotationDay(attraction_id=r.attraction_id, work_date=DAY, status='live',
                          state_json=json.dumps(state), version=1)
        db.add(row)
        db.flush()
        assert S.advance(db, cfg, row, state, 807)
        assert state['lines'] == expected['lines']
        assert state['persons'] == expected['persons']

    with r.factory() as db:
        events = db.query(RotationEvent).order_by(RotationEvent.id).all()
        assert [e.event_type for e in events] == ['post_close', *['depart'] * 4, 'workflow_checkpoint']
        assert json.loads(events[1].detail_json)['effectiveAt'] == 810
        assert db.query(RotationSegment).filter_by(employee_no='A1').one().end_min == 807


def test_undo_preserves_segment_written_by_earlier_future_timed_batch(current):
    r = current
    with r.factory() as db, S.transaction(db):
        row = S.day_row(db, r.attraction_id, DAY)
        eng, _ = S.engine_for(db, S.load_config(db, r.attraction_id), row, S.day_state(row))
        eng.h.segment('5', 'A', 600, 610)
        db.flush()
        before = H.snapshot(eng.d)
        before['existingSegmentIds'] = H.existing_segment_ids(db, eng, 600)
        eng.h.segment('5', 'B', 605, 610)
        F.execute(eng, '5', 600, 'test', 'A')
        H.record(db, eng, before, 'depart', 'test', 600)
        db.flush()
        undo = H.plan(db, eng, 'A')
        H.execute(db, eng, 'A', undo['id'], 600, 'test')
        assert [(seg.line, seg.start_min, seg.end_min)
                for seg in db.query(RotationSegment).filter_by(employee_no='5')] == [('A', 600, 610)]
