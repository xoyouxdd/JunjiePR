"""Daily plans: real isolated SQLite transactions, history and concurrent edit guards."""
from copy import deepcopy

import pytest

from test_rotation_transaction_boundaries import rotation, DAY, draft, live
from app.rotation import service as S
from app.rotation import daily as D
from app.rotation.models import RotationDay, RotationDuty, RotationSegment, RotationNotice, RotationSnapshot

FUTURE = '2026-10-05'


def prepare(rotation):
    with rotation.factory() as db:
        parsed = {'people': {str(i): {'name': f'测试员工{i}', 'type': '', 'mark': ''} for i in range(1, 6)},
                  'days': {FUTURE: {str(i): '07:15-16:15' for i in range(1, 6)}}, 'hours': {}}
        S.save_roster(db, rotation.attraction_id, parsed, scope='day', file_name='明日.xlsx', uploader_id=None, uploader_name='测试')
        before = D.day_payload(db, rotation.attraction_id, FUTURE)
        assert before['revision'] == 0 and before['can_generate'] and before['day'] is None
        assert db.query(RotationDay).count() == 0  # GET never generates or starts anything.
        D.edit(db, rotation.attraction_id, '测试', FUTURE, {'action': 'draft_generate', 'revision': 0})


def read(rotation, day=FUTURE, at=None):
    with rotation.factory() as db:
        return D.day_payload(db, rotation.attraction_id, day, at)


def edit(rotation, **body):
    data = read(rotation)
    with rotation.factory() as db:
        D.edit(db, rotation.attraction_id, '测试', FUTURE, {'revision': data['revision'], **body})


def test_future_preview_swap_and_stale_revision_are_atomic(rotation):
    prepare(rotation)
    before = read(rotation)
    assert before['draft']['dailyAutoStart'] and before['can_edit']
    with rotation.factory() as db:
        assert all(db.query(model).count() == 0 for model in (RotationDuty, RotationSegment, RotationNotice, RotationSnapshot))
        assert S.clock_now(S.load_config(db, rotation.attraction_id))[0] == DAY
    edit(rotation, action='draft_swap_people', source='A#0', target='B#0')
    after = read(rotation)
    assert after['draft']['plan']['crew']['A#0'] == before['draft']['plan']['crew']['B#0']
    assert after['draft']['plan']['crew']['B#0'] == before['draft']['plan']['crew']['A#0']
    with rotation.factory() as db, pytest.raises(S.ConfirmationConflict):
        D.edit(db, rotation.attraction_id, '测试', FUTURE, {'revision': before['revision'], 'action': 'draft_swap_people', 'source': 'A#0', 'target': 'B#0'})
    assert read(rotation)['draft'] == after['draft']
    edit(rotation, action='draft_swap_posts', source='A#0', target='A#1')
    swapped = read(rotation)['draft']
    assert swapped['lines'][0]['posts'][0]['name'] == after['draft']['lines'][0]['posts'][1]['name']
    assert swapped['plan']['crew']['A#0'] == after['draft']['plan']['crew']['A#1']
    for body in ({'action': 'draft_swap_posts', 'source': 'A#0', 'target': 'B#0'},
                 {'action': 'draft_swap_people', 'source': 'A#-1', 'target': 'B#0'},
                 {'action': 'publish'}, {'action': 'draft_generate', 'force': True}):
        with pytest.raises(S.ActionError): edit(rotation, **body)
        assert read(rotation)['draft'] == swapped
    edit(rotation, action='draft_set', kind='crew', key='B#1', pid='')
    edit(rotation, action='draft_swap_people', source='A#0', target='B#1')
    assert 'A#0' not in read(rotation)['draft']['plan']['crew']


def test_future_plan_starts_only_on_its_day_preserving_edits(rotation):
    prepare(rotation)
    edit(rotation, action='draft_swap_people', source='A#0', target='B#0')
    plan = deepcopy(read(rotation)['draft']['plan'])
    with rotation.factory() as db, S.transaction(db):
        cfg = S.load_config(db, rotation.attraction_id)
        S.ensure_prearranged_day(db, cfg, rotation.attraction_id, DAY, 240)
        assert S.day_row(db, rotation.attraction_id, FUTURE).status == 'draft'
        S.set_sim_clock(cfg, FUTURE, 240, paused=True)
        S.ensure_prearranged_day(db, cfg, rotation.attraction_id, FUTURE, 240)
    after = read(rotation)
    assert after['day']['status'] == 'live' and not after['can_edit']
    assert after['day']['plan'] == plan
    assert after['times'] and after['as_of'] == 240


def test_historical_reads_are_readonly_and_quiet_ticks_do_not_add_snapshots(rotation):
    live(rotation)
    with rotation.factory() as db, S.transaction(db):
        cfg = S.load_config(db, rotation.attraction_id)
        row = S.day_row(db, rotation.attraction_id, DAY)
        S.advance(db, cfg, row, S.day_state(row), 241)
    first = read(rotation, DAY)
    assert first['as_of'] == 240
    with rotation.factory() as db, S.transaction(db):
        cfg = S.load_config(db, rotation.attraction_id)
        row = S.day_row(db, rotation.attraction_id, DAY)
        state = S.day_state(row)
        state['lines'][0]['posts'][0]['name'] = '后续变更岗位'
        state['lastTick'] = 242
        S.save_day(db, row, state)
    latest = read(rotation, DAY)
    historical = read(rotation, DAY, '04:01')
    assert len(latest['times']) == len(first['times']) + 1
    assert historical['day']['lines'][0]['posts'][0]['name'] != '后续变更岗位'
    assert latest['day']['lines'][0]['posts'][0]['name'] == '后续变更岗位'
    assert read(rotation, DAY, '03:59')['day'] is None
    with rotation.factory() as db:
        version, count = S.day_row(db, rotation.attraction_id, DAY).version, db.query(RotationSnapshot).count()
        clock_before = S.clock_now(S.load_config(db, rotation.attraction_id))
    for _ in range(3): read(rotation, DAY, '04:01')
    with rotation.factory() as db:
        assert S.day_row(db, rotation.attraction_id, DAY).version == version
        assert db.query(RotationSnapshot).count() == count
        assert S.clock_now(S.load_config(db, rotation.attraction_id)) == clock_before
    assert all(not p['canDepart'] and not p['canArrive'] and 'confirmationTokens' not in p for p in historical['day']['persons'])


def test_legacy_missing_history_is_explicit_and_past_edits_rejected(rotation):
    live(rotation)
    with rotation.factory() as db, S.transaction(db):
        db.query(RotationSnapshot).delete()
        S.set_sim_clock(S.load_config(db, rotation.attraction_id), FUTURE, 240, paused=True)
    result = read(rotation, DAY)
    assert result['legacy_history'] and result['day'] and not result['can_edit']
    assert read(rotation, DAY, '07:30')['day'] is None
    with rotation.factory() as db, pytest.raises(S.ActionError, match='往日班表只读'):
        D.edit(db, rotation.attraction_id, '测试', DAY, {'revision': result['revision'], 'action': 'draft_generate'})


def test_month_validation_roster_replacement_and_manual_draft_compatibility(rotation):
    prepare(rotation)
    with rotation.factory() as db:
        month = D.month_payload(db, rotation.attraction_id, '2026-10')
        assert len(month['days']) == 31
        assert month['days'][4]['status'] == 'draft' and month['days'][4]['has_roster']
        for bad in ('2026-13', '2026-1', 'oops', '0000-01'):
            with pytest.raises(S.ActionError): D.month_payload(db, rotation.attraction_id, bad)
        parsed = {'people': {'1': {'name': '调整班次', 'type': '', 'mark': ''}}, 'days': {FUTURE: {'1': '08:00-16:00'}}, 'hours': {}}
        S.save_roster(db, rotation.attraction_id, parsed, scope='day', file_name='修订.xlsx', uploader_id=None, uploader_name='测试')
    assert read(rotation)['roster_changed']
    with rotation.factory() as db, S.transaction(db):
        cfg = S.load_config(db, rotation.attraction_id)
        S.set_sim_clock(cfg, FUTURE, 240, paused=True)
        S.ensure_prearranged_day(db, cfg, rotation.attraction_id, FUTURE, 240)
        assert S.day_row(db, rotation.attraction_id, FUTURE).status == 'draft'
    with pytest.raises(S.ActionError, match='上传班表已更新'): edit(rotation, action='publish')
    with rotation.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, rotation.attraction_id), DAY, 240, paused=True)
    draft(rotation)
    with rotation.factory() as db, S.transaction(db):
        cfg = S.load_config(db, rotation.attraction_id)
        S.ensure_prearranged_day(db, cfg, rotation.attraction_id, DAY, 240)
        assert S.day_row(db, rotation.attraction_id, DAY).status == 'draft'


def test_seven_shift_validation_and_post_swap_carry_assignments(rotation):
    prepare(rotation)
    with rotation.factory() as db, S.transaction(db):
        row = S.day_row(db, rotation.attraction_id, FUTURE)
        state = S.day_state(row)
        pid = state['plan']['crew'].pop('A#0')
        state['persons'][pid]['start'] = 420
        state['lines'][0]['posts'][0]['seven'] = 1
        state['plan']['seven']['A#0'] = pid
        S.save_day(db, row, state)
    before = read(rotation)
    with pytest.raises(S.ActionError, match='7点岗位'): edit(rotation, action='draft_swap_people', source='A#0', target='B#0')
    assert read(rotation)['draft'] == before['draft']
    edit(rotation, action='draft_swap_posts', source='A#0', target='A#1')
    after = read(rotation)['draft']
    assert after['plan']['seven']['A#1'] == pid and 'A#0' not in after['plan']['seven']
    assert after['lines'][0]['posts'][1]['seven'] == 1
    for bad in ([], {}, None):
        with pytest.raises(S.ActionError): edit(rotation, action=bad)


def test_snapshot_write_rolls_back_with_failed_commit(rotation, monkeypatch):
    live(rotation)
    before = read(rotation, DAY)
    with rotation.factory() as db:
        def failed_commit(): raise RuntimeError('injected commit failure')
        monkeypatch.setattr(db, 'commit', failed_commit)
        with pytest.raises(RuntimeError, match='injected'):
            with S.transaction(db):
                row = S.day_row(db, rotation.attraction_id, DAY)
                state = S.day_state(row)
                state['lines'][0]['posts'][0]['name'] = '不得留下此状态'
                state['lastTick'] = 250
                S.save_day(db, row, state)
    after = read(rotation, DAY)
    assert after['revision'] == before['revision'] and after['times'] == before['times']
    assert after['day'] == before['day']
