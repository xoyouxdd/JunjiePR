
from copy import deepcopy
import json

import pytest

from app.rotation import forecast as F, forecast_service as API, history as H, service as S
from app.rotation.models import RotationEvent, RotationNotice, RotationSegment
from test_rotation_forecast import add_later_shifts, fixture, occupants
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
    ((2, 3), 'ABBAB'),
    ((1, 2, 3, 2, 3), 'ABCDECEBDCE'),
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
        assert line['sortAt'] == 807
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
    assert eng.P['late15']['state'] == 'ready'
    assert API.person_view(eng, eng.P['late15'], 810)['canDepart']


@pytest.mark.parametrize('off, arrivals, headings', [
    ((1, 3), (1, 2, 3, 4), ('4推', '推下班', '', '推下班')),
    ((0,), (1, 2, 3, 4), ('推下班', '3推', '', '')),
    ((1,), (1, 2, 3, 4), ('4推', '推下班', '', '')),
    ((1,), (4, 3, 1), ('3推', '推下班', '2推')),
])
def test_batch_handoff_follows_original_worker_at_each_final_post(off, arrivals, headings):
    eng = batch_fixture((5,), len(arrivals))
    add_later_shifts(eng.P, (900,))
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
        first_instruction = result['instruction'].split('。', 1)[0]
        if task == '推下班':
            assert first_instruction.endswith('下班')
        elif not task:
            assert first_instruction.endswith('下岗，回休息室')
        else:
            assert first_instruction.endswith(f"去{names[i + int(task[0])]}")


def test_batch_handoffs_keep_personal_instructions_and_share_the_lines_shift_end_marker():
    eng = batch_fixture((5,), 2)
    add_later_shifts(eng.P, (945, 975))
    names = ('塔台', '上客一', '上协一', '下协一', '大出口')
    for post, name in zip(eng.line('A')['posts'], names):
        post['name'] = name
    eng.P['A2']['end'] = 807
    F.tick(eng, 807, advance_batches=True)

    first = API.batch_result_view(eng.P['late01']['batchResult'])
    second = API.batch_result_view(eng.P['late00']['batchResult'])
    assert first['heading'] == 'A线 · 塔台 · 2推'
    assert first['instruction'] == '通知原塔台的A0去上协一。A0到岗后通知原上协一的A2下班'
    assert [part['text'] for part in first['instructionParts'] if part.get('emphasis')] == ['A2下班']
    assert first['shiftEndPosts'] == [eng.line('A')['posts'][2]['key']]
    assert second['heading'] == 'A线 · 上客一 · 2推'
    assert second['instruction'] == ('通知原上客一的A1去下协一。A1到岗后通知原下协一的A3去大出口。'
                                     'A3到岗后通知原大出口的A4下岗，回休息室')
    assert second['shiftEndPosts'] == first['shiftEndPosts']
    assert not any(part.get('emphasis') for part in second['instructionParts'])


@pytest.mark.parametrize('added,closed,personal', [
    ((4,), (), ((), (4,))),
    ((4,), (1,), ((4,), ())),
    ((3, 4), (), ((3,), (4,))),
])
def test_batch_marks_actual_openings_and_counts_participating_posts(added, closed, personal):
    eng = batch_fixture((5,), 2)
    posts = eng.line('A')['posts']
    for i in added + closed:
        del eng.P[posts[i]['occ']]
        posts[i].update(occ=None, open=False, opened=False)
        if i in added:
            posts[i].update(openAt='13:27', openRequest=True)
    F.tick(eng, 807, advance_batches=True)
    for i, own in enumerate(personal):
        result = API.batch_result_view(eng.P[f'late{i:02}']['batchResult'])
        assert result['heading'].endswith(' · 2推')
        assert [item['key'] for item in result['openingPosts']] == [posts[pos]['key'] for pos in added]
        assert [item['key'] for item in result['ownOpeningPosts']] == [posts[pos]['key'] for pos in own]
        assert [part['text'] for part in result['instructionParts'] if part.get('emphasis')] == [
            f"{eng.P[posts[pos]['occ']]['name']}去加{posts[pos]['name']}" for pos in own
        ]


def test_batch_shift_end_marker_stays_on_the_affected_line():
    eng = batch_fixture((5, 5), 4)
    add_later_shifts(eng.P, (945, 975))
    eng.P['A2']['end'] = 807
    F.tick(eng, 807, advance_batches=True)

    results = [API.batch_result_view(eng.P[f'late{i:02}']['batchResult']) for i in range(4)]
    line_a = [result for result in results if result['line'] == 'A']
    line_b = [result for result in results if result['line'] == 'B']
    assert line_a and line_b
    assert all(result['shiftEndPosts'] == [eng.line('A')['posts'][2]['key']] for result in line_a)
    assert all(result['shiftEndPosts'] == [] for result in line_b)


def test_older_direct_shift_end_results_keep_the_shift_end_label():
    legacy = {'line': 'A', 'post': '塔台',
              'handoff': {'post': '塔台', 'name': '王明', 'outcome': 'done'},
              'beforePosts': [{'key': 'a0', 'name': '塔台', 'occName': '王明'}]}
    result = API.batch_result_view(legacy)
    assert result['heading'] == 'A线 · 塔台 · 推下班'
    assert result['instruction'] == '通知原塔台的王明下班'
    assert result['shiftEndPosts'] == ['a0']


def test_batch_uses_resting_workers_first_and_keeps_their_ready_time_for_standing():
    eng = batch_fixture((2, 3), 1)
    add_later_shifts(eng.P, (900,))
    eng.P['resting'] = dict(eng.P['late00'], pid='resting', name='resting', state='rest',
                           start=435, end=1200, readyAt=808)
    eng.P['A1']['end'] = 820
    F.tick(eng, 807, advance_batches=True)
    assert eng.P['A1']['state'] == 'done'
    assert eng.P['resting']['batchResult']['handoff'] == {
        'post': 'A0', 'name': 'A0', 'outcome': 'post', 'destination': 'A1', 'steps': 1}
    assert eng.line('A')['sortAt'] == 807
    assert eng.P['resting']['entrySortAt'] == 807
    assert eng.P['A0']['lineStart'] == 808
    assert eng.P['resting']['batchResult']['line'] == 'A'
    assert eng.P['late00']['batchResult']['line'] == 'B'


def test_same_second_header_keeps_real_time_in_preview_and_execution():
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
        assert eng.line(line)['sortAt'] == now
        assert eng.P[f'new{i:02}']['lineStart'] == eng.line(line)['sortAt']


def test_seven_people_over_three_lines_use_following_positions_after_the_batch():
    eng = batch_fixture((4, 4, 4), 7)
    F.tick(eng, 807, advance_batches=True)

    first_seven = [eng.P[f'late{i:02}'] for i in range(7)]
    assert ''.join(p['batchResult']['line'] for p in first_seven) == 'ABCABCA'
    assert all(p['lineStart'] == 810 and p['continuousStart'] == 810 for p in first_seven)
    assert {line['sortAt'] for line in eng.d['lines']} == {807}
    assert eng.line('A')['previousSortAt'] == 807

    F.tick(eng, 810, advance_batches=True)
    eighth = eng.P['late07']
    assert eighth['assign']['line'] == 'B'
    assert F.execute(eng, eighth['pid'], 810, 'test', 'B')['executed']
    assert eng.line('B')['sortAt'] == eighth['lineStart'] == 810


@pytest.mark.parametrize('last,second', [(1, 1), (2, 9), (3, 10)])
def test_late_shifts_default_to_latest_two_regardless_of_headcount(last, second):
    eng = fixture()
    template = eng.P['new0']
    for start, count in ((650, 20), (700, second), (750, last)):
        for i in range(count):
            pid = f'{start}-{i}'
            eng.P[pid] = dict(template, pid=pid, start=start, end=900, forecastOrder=pid)
    assert not F.late_shift(eng, eng.P['650-0'])
    assert all(F.late_shift(eng, eng.P[f'{start}-0']) for start in (750, 700))


def test_late_shift_works_last_twenty_minutes_and_ordinary_exit_is_inclusive():
    eng = fixture()
    for p in eng.P.values():
        p.update(start=750 if p['state']=='onpost' else 430, end=900)
    for i in range(3):
        eng.P[f'new{i}'].update(start=750, readyAt=885 if i == 0 else 895)
    eng.P['new2']['start'] = 700
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
    advance(807 - 1/60)
    assert person(r, '101')['state'] == 'notyet'
    dispatch_at = 807 + 5/60
    with r.factory() as db:
        cfg = S.load_config(db, r.attraction_id)
        row = S.day_row(db, r.attraction_id, DAY)
        with monkeypatch.context() as patch:
            def fail_commit():
                raise RuntimeError('injected batch commit failure')
            patch.setattr(db, 'commit', fail_commit)
            with pytest.raises(RuntimeError, match='injected batch commit failure'), S.transaction(db):
                S.advance(db, cfg, row, S.day_state(row), dispatch_at)
    assert person(r, '101')['state'] == 'notyet'
    advance(dispatch_at)
    worker = person(r, '101')
    assert worker['state'] == 'onpost'
    assert worker['lineStart'] == 810
    assert worker['entrySortAt'] == dispatch_at
    assert next(l for l in board(r)['lines'] if l['id'] == worker['line'])['sortAt'] == dispatch_at
    assert person(r, '5')['state'] == 'onpost'
    assert person(r, '102')['state'] == 'notyet'
    advance(dispatch_at)
    with r.factory() as db:
        events = db.query(RotationEvent).filter_by(event_type='depart', actor_type='system').all()
        assert len(events) == 2 and all(e.minute == dispatch_at for e in events)
        assert db.query(RotationNotice).filter_by(kind='batch_result', status='open').count() == 2
        assert {seg.end_min for seg in db.query(RotationSegment)} == {808, 810}
    advance(810)
    assert person(r, '102')['awaitingShiftEntry']


def test_late_tick_dispatches_after_opening_and_carries_the_entry_header_through_a_push(rotation):
    from app.rotation.models import RotationDay
    eng = batch_fixture((4,), 1)
    line = eng.line('A')
    post = line['posts'][-1]
    del eng.P[post['occ']]
    post.update(occ=None, open=False, opened=False, openAt='13:27:03')
    state = eng.d
    state.update(date=DAY, lastTick=806)
    dispatch_at = 807 + 5/60
    with rotation.factory() as db, S.transaction(db):
        aid = rotation.attraction_id
        cfg = S.load_config(db, aid)
        row = RotationDay(attraction_id=aid, work_date=DAY, status='live',
                          state_json=json.dumps(state), version=1)
        db.add(row)
        db.flush()
        assert S.advance(db, cfg, row, state, dispatch_at)
        saved, _ = S.engine_for(db, cfg, row, S.day_state(row))
        arriving = saved.P['late00']
        assert saved.d['completedShiftBatches'][str(810 * 60)] == dispatch_at
        assert saved.line('A')['sortAt'] == arriving['entrySortAt'] == 780
        assert arriving['lineStart'] == 810
        assert arriving['batchResult']['openingPosts'] == [{'key': post['key'], 'name': post['name']}]
        assert arriving['batchResult']['afterPosts'][0]['entrySortAt'] == 780

        F.tick(saved, 811)
        next_person = saved.P['late01']
        assert F.execute(saved, next_person['pid'], 811, 'test', 'A')['executed']
        assert occupants(saved)[:2] == ['late01', 'late00']
        assert saved.line('A')['sortAt'] == next_person['entrySortAt'] == 811
        assert arriving['entrySortAt'] == 780
        assert S.lines_view(saved)[0]['posts'][1]['entrySortAt'] == 780


def test_scheduled_close_inside_relief_path_waits_for_relief():
    eng = batch_fixture((3, 3), 1)
    add_later_shifts(eng.P, (900,))
    eng.P['A2']['end'] = 807
    eng.line('A')['posts'][1]['closeAt'] = '13:27'
    F.tick(eng, 807, advance_batches=True)
    assert eng.P['A2']['state'] == 'done'
    assert occupants(eng, 'A') == ['late00', None, 'A1']
    assert eng.line('A')['sortAt'] == 807
    assert eng.P['late00']['batchResult']['handoff'] == {
        'post': 'A0', 'name': 'A0', 'outcome': 'pending'}


@pytest.mark.parametrize('count', [1, 15])
def test_ordinary_shifts_use_the_check_in_window_at_any_headcount(count):
    eng = batch_fixture((2, 3), 2)
    for i in range(count, 16):
        del eng.P[f'late{i:02}']
    p = eng.P['late00']
    assert not F.can_check_in(eng, p, 795 - 1/60)
    assert F.can_check_in(eng, p, 795)
    assert not F.can_check_in(eng, p, 807)
    F.tick(eng, 807, advance_batches=True)
    assert p['state'] == 'onpost'
    assert p['batchResult']['start'] == 810


@pytest.mark.parametrize('start', [420, 435])
def test_prearranged_early_post_only_marks_unsigned_workers(start):
    eng = fixture((1,), now=start - 15)
    worker = eng.P['A0']
    worker.update(state='notyet', start=start)

    post_before = S.lines_view(eng, now=start - 15 - 1/60)[0]['posts'][0]
    assert 'pendingCheckIn' not in post_before and 'missedCheckIn' not in post_before
    assert S.lines_view(eng, now=start - 15)[0]['posts'][0]['pendingCheckIn']
    assert S.lines_view(eng, now=start - 3)[0]['posts'][0]['missedCheckIn']

    worker['batchCheckedInFor'] = start
    for minute in (start - 15, start - 3):
        signed_post = S.lines_view(eng, now=minute)[0]['posts'][0]
        assert signed_post['occ'] == worker['pid']
        assert 'pendingCheckIn' not in signed_post and 'missedCheckIn' not in signed_post


@pytest.mark.parametrize('start', [300, 315])
def test_0500_and_0515_can_check_in_but_enter_pending_rest(start):
    eng = fixture(now=start - 15)
    worker = eng.P['new0']
    worker.update(state='notyet', start=start, readyAt=None, batchCheckedInFor=start)
    eng.d['plan']['crew']['A#0'] = worker['pid']
    assert F.can_check_in(eng, worker, start - 15)
    F.tick(eng, start - 3, advance_batches=True)
    assert worker['state'] == 'notyet'
    F.tick(eng, start, advance_batches=True)
    assert worker['state'] == 'pending' and not worker.get('batchResult')


@pytest.mark.parametrize(('shift', 'pool', 'duration'), [
    (420, 'rest', 15), (435, 'rest', 15), (435, 'pending', None), (435, 'meal', 45),
])
def test_early_prearranged_pool_move_keeps_original_shift(shift, pool, duration):
    moved_at = 241 + 2/60
    eng = fixture((2,), now=moved_at)
    worker = eng.P['A0']
    worker['start'] = shift
    eng.d['plan']['crew']['A#0'] = worker['pid']

    F.pool(eng, worker['pid'], pool, moved_at, 'test')
    assert worker['state'] == 'notyet' and worker['initialPool'] == pool
    assert worker['start'] == shift and worker['readyAt'] is None
    assert worker['pid'] not in eng.d['plan']['crew'].values()
    for minute in (moved_at + 16, shift - 1/60):
        F.tick(eng, minute)
        assert worker['state'] == 'notyet' and not worker.get('assign') and not worker.get('exception')
    assert F.can_check_in(eng, worker, shift - 15)
    F.tick(eng, shift)
    assert worker['state'] == pool
    if duration is None:
        assert worker['readyAt'] is None
    else:
        assert worker['breakStart'] == shift
        assert worker['readyAt'] == shift + duration
        assert F.deadline(eng, worker, shift) == shift + duration + 1


@pytest.mark.parametrize('start', [360, 390, 420, 430, 435])
def test_shifts_at_or_before_0715_are_not_dispatched_three_minutes_early(start):
    eng = fixture(now=start - 15)
    worker = eng.P['new0']
    worker.update(state='notyet', start=start, readyAt=None, batchCheckedInFor=start)
    F.tick(eng, start - 3, advance_batches=True)
    assert worker['state'] == 'notyet' and not worker.get('batchResult')


def test_0727_without_a_0730_shift_only_picks_signed_in_morning_rest():
    eng = fixture(now=447)
    for pid in ('new0', 'new1'):
        eng.P[pid].update(start=435, breakStart=435, readyAt=450)
    eng.P['new0']['batchCheckedInFor'] = 435
    eng.P['new2'].update(start=300, breakStart=435, readyAt=450)
    F.tick(eng, 447, advance_batches=True)
    assert eng.P['new0']['batchResult']['reportAt'] == 450
    assert all(eng.P[pid]['state'] == 'rest' and not eng.P[pid].get('batchResult')
               for pid in ('new1', 'new2'))


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
    assert ready['task']['deadlineAt'] == 817
    assert F.execute(eng, p['pid'], 816, 'test', 'A')['executed']
    assert not API.person_view(eng, p, 816)['awaitingShiftEntry']


def test_direct_shift_entry_is_not_picked_up_by_a_later_batch():
    eng = batch_fixture((2, 3), 1)
    eng.P['direct'] = dict(eng.P['late15'], pid='direct', start=807, state='notyet')
    F.tick(eng, 807, advance_batches=True)
    assert F.awaiting_shift_entry(eng.P['direct'])
    assert not eng.P['direct'].get('batchResult')
    assert eng.P['late00']['state'] == 'onpost'


@pytest.mark.parametrize('actor_kind', ['member', 'screen', 'supervisor'])
def test_morning_check_in_direct_relief_and_scheduled_rest(rotation, actor_kind):
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
        # The first signed-in 07:15 worker has already relieved more people.
        db.add_all(RotationEvent(
            attraction_id=aid, work_date='2026-10-03', minute=435, employee_no=pid,
            event_type='depart', actor_type='employee', actor_name='测试',
            detail_json='{"purpose":"push7"}',
        ) for pid in [relief[0], *early[6:]])
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

    def check_in(p):
        from types import SimpleNamespace
        actor = SimpleNamespace(kind=actor_kind, member_employee_no=p['pid'], actor_id=None, name=p['pid'])
        request = {'action': 'batch_check_in', 'pid': p['pid'],
                   'confirmation_token': p['confirmationTokens']['batch_check_in']}
        assert act(rotation, request, actor=actor)['executed']
        assert act(rotation, request, actor=actor)['duplicate']

    by_id, _ = advance(405)
    assert all(by_id[pid]['upcomingShift'] and by_id[pid]['canBatchCheckIn'] for pid in early)
    check_in(by_id[early[0]])
    by_id, _ = advance(417)
    assert not by_id[early[0]]['missedCheckIn']
    assert all(by_id[pid]['missedCheckIn'] and not by_id[pid]['canBatchCheckIn'] for pid in early[1:])
    by_id, lines = advance(420)
    assert [line['posts'][0]['occ'] for line in reversed(lines)] == early[:6]
    assert all(by_id[pid]['state'] == 'rest' and by_id[pid]['readyAt'] == 435 for pid in early[6:])
    assert all(by_id[pid]['upcomingShift'] and by_id[pid]['canBatchCheckIn'] for pid in relief)
    for pid in relief[:7]:
        check_in(by_id[pid])
    by_id, _ = advance(432)
    assert all(by_id[pid]['state'] == 'notyet' and not by_id[pid]['canBatchCheckIn'] for pid in relief)
    by_id, at_relief = advance(435)
    assert {line['posts'][0]['occ'] for line in at_relief} == set(relief[1:7])
    assert all(by_id[pid]['batchResult']['specialTasks'] == ['推7点'] for pid in relief[1:7])
    assert all(by_id[pid]['state'] == 'rest' and by_id[pid]['readyAt'] == 450
               for pid in [relief[0], *relief[7:]])
    assert by_id[relief[0]]['morningRest']
    assert not by_id[early[0]]['missedCheckIn']
    with rotation.factory() as db:
        state = S.day_state(S.day_row(db, aid, DAY))
        assert state['dailyOpening']['captured_at'] == 420
        assert state['completedShiftBatches'][str(435 * 60)] == 435
        assert db.query(RotationEvent).filter_by(work_date=DAY, event_type='depart').count() == 6
    by_id, _ = advance(446)
    assert by_id[relief[0]]['state'] == 'rest'
    by_id, _ = advance(447)
    assert by_id[relief[0]]['state'] == 'onpost'
    assert by_id[relief[0]]['batchResult']['reportAt'] == 450
    assert all(by_id[pid]['state'] == 'rest' and not by_id[pid].get('batchResult') for pid in relief[7:])
    by_id, _ = advance(450)
    worker = by_id[relief[7]]
    assert worker['canDepart']
    request = {'action': 'depart', 'pid': worker['pid'], 'expected_line': worker['assign']['line'],
               'confirmation_token': worker['confirmationTokens']['depart']}
    result = act(rotation, request)
    if result.get('line_changed'):
        result = act(rotation, {**request, 'expected_line': result['line']})
    assert result['executed']


def test_batch_push7_accepts_attendee_before_their_scheduled_start():
    eng = batch_fixture((2, 3), 1)
    eng.P['B0']['start'] = 420
    F.tick(eng, 807, advance_batches=True)
    assert eng.P['B0']['state'] == 'pending'
    assert eng.P['late00']['batchResult']['handoff']['outcome'] == 'pending'
    result = API.batch_result_view(eng.P['late00']['batchResult'])
    assert result['specialTasks'] == ['推7点']
    assert result['instructionParts'][-1] == {'text': 'B0（7点班）下岗，回休息室', 'emphasis': True}
    assert [post['occ'] for post in result['beforePosts'] if post['seven']] == ['B0']
    assert not any(post['seven'] for post in result['afterPosts'])


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
