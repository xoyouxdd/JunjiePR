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


def test_future_post_rename_preserves_plan_and_rejects_invalid_or_stale_edits(rotation):
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        state['lines'][0]['posts'][0]['name']='塔台';S.save_day(db,row,state)
    before=read(rotation);original=before['draft']['lines'][0]['posts'][0]['name']
    edit(rotation,action='draft_rename_post',post='A#0',expected_name=original,name='新入口')
    after=read(rotation)
    assert after['draft']['lines'][0]['posts'][0]['name']=='新入口'
    assert after['draft']['lines'][0]['posts'][0]['hold101Role']=='tower'
    assert after['draft']['plan']==before['draft']['plan']
    assert after['draft']['persons']==before['draft']['persons']
    for name in ('', ' '*3, '字'*41, 123, after['draft']['lines'][0]['posts'][1]['name']):
        with pytest.raises(S.ActionError):edit(rotation,action='draft_rename_post',post='A#0',expected_name='新入口',name=name)
        assert read(rotation)['draft']==after['draft']
    with pytest.raises(S.ConfirmationConflict):edit(rotation,action='draft_rename_post',post='A#0',expected_name=original,name='旧页面改名')
    with rotation.factory() as db,pytest.raises(S.ConfirmationConflict):
        D.edit(db,rotation.attraction_id,'测试',FUTURE,{'revision':before['revision'],'action':'draft_rename_post','post':'A#0','expected_name':original,'name':'旧版本'})
    edit(rotation,action='draft_swap_posts',source='A#0',target='A#1')
    assert read(rotation)['draft']['lines'][0]['posts'][1]['name']=='新入口'
    assert read(rotation)['draft']['lines'][0]['posts'][1]['key']==after['draft']['lines'][0]['posts'][0]['key']


def test_prestart_rename_syncs_live_board_and_frozen_day_is_readonly(rotation):
    live(rotation);before=read(rotation,DAY);old=before['draft']['lines'][0]['posts'][0]['name']
    daily_edit_today(rotation,before,action='draft_rename_post',post='A#0',expected_name=old,name='开班前改名')
    after=read(rotation,DAY)
    with rotation.factory() as db:board=S.board_payload(db,rotation.attraction_id)['day']
    assert board['lines'][0]['posts'][0]['name']=='开班前改名'
    assert after['draft']['plan']==before['draft']['plan']
    assert all(p['state']=='notyet' and p['lineStart'] is None for p in after['day']['persons'])
    set_clock(rotation,DAY,435);frozen=read(rotation,DAY)
    assert frozen['frozen'] and frozen['day']['lines'][0]['posts'][0]['name']=='开班前改名'
    with pytest.raises(S.ActionError,match='已固定'):
        daily_edit_today(rotation,action='draft_rename_post',post='A#0',expected_name='开班前改名',name='不能覆盖历史')
    assert read(rotation,DAY)['day']==frozen['day']


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
    assert after['day']['status'] == 'live' and after['can_edit'] and after['prestart_live']
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


def set_clock(rotation, day, minute):
    with rotation.factory() as db, S.transaction(db):
        S.set_sim_clock(S.load_config(db, rotation.attraction_id), day, minute, paused=True)


def daily_edit_today(rotation, data=None, **body):
    data = data or read(rotation, DAY)
    with rotation.factory() as db:
        D.edit(db, rotation.attraction_id, '测试', DAY,
               {'revision':data['revision'], 'plan_revision':data['plan_revision'], **body})


def test_today_published_prestart_plan_and_board_edit_same_arrangement(rotation):
    live(rotation)
    before = read(rotation, DAY)
    assert before['can_edit'] and before['prestart_live'] and not before['frozen']
    daily_edit_today(rotation, before, action='draft_swap_people', source='A#0', target='B#0')
    after = read(rotation, DAY)
    with rotation.factory() as db:
        board = S.board_payload(db, rotation.attraction_id)['day']
    assert after['day']['lines'] == board['lines']
    assert after['draft']['plan']['crew']['A#0'] == before['draft']['plan']['crew']['B#0']
    pid = after['draft']['plan']['crew']['A#0']
    with rotation.factory() as db:
        S.do_live_action(db, rotation.attraction_id, 'test', {'action':'move', 'pid':pid, 'line':'B', 'i':0})
    restored = read(rotation, DAY)
    assert restored['draft']['plan'] == before['draft']['plan']
    assert all(p['state']=='notyet' and p['lineStart'] is None for p in restored['day']['persons'])


def test_opening_freezes_whole_day_once_and_later_board_changes_do_not_touch_it(rotation):
    live(rotation)
    daily_edit_today(rotation, action='draft_swap_people', source='A#0', target='B#0')
    with rotation.factory() as db, S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,DAY);state=S.day_state(row)
        state['persons']['5']['start']=720;S.save_day(db,row,state)
    expected = read(rotation, DAY)['day']['lines']
    set_clock(rotation, DAY, 435)
    opening = read(rotation, DAY)
    assert opening['frozen'] and not opening['can_edit'] and opening['opening_source']=='opening'
    assert opening['as_of']==435 and opening['day']['lines']==expected
    with rotation.factory() as db, S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,DAY);state=S.day_state(row)
        state['lines'][0]['posts'][0]['name']='运行后的调整'
        state['persons']['5']['start']=780
        S.save_day(db,row,state)
    assert read(rotation, DAY)['day']==opening['day']
    with pytest.raises(S.ActionError,match='已固定'):
        daily_edit_today(rotation, action='draft_swap_people', source='A#0', target='B#0')
    set_clock(rotation,FUTURE,240)
    historical=read(rotation,DAY)
    assert historical['day']==opening['day'] and not historical['can_edit']
    assert historical['opening_source']=='opening'


def test_prestart_editor_ignores_quiet_clock_but_rejects_real_plan_change(rotation):
    live(rotation)
    before=read(rotation,DAY)
    set_clock(rotation,DAY,241)
    read(rotation,DAY)
    daily_edit_today(rotation,before,action='draft_swap_people',source='A#0',target='B#0')
    with pytest.raises(S.ConfirmationConflict):
        daily_edit_today(rotation,before,action='draft_swap_people',source='A#0',target='B#0')


def test_prestart_pool_person_and_cross_line_posts_update_board_without_timers(rotation):
    live(rotation)
    before=read(rotation,DAY)
    used=set(before['draft']['plan']['crew'].values())|set(before['draft']['plan']['seven'].values())
    pid=next(pid for pid in before['draft']['persons'] if pid not in used)
    daily_edit_today(rotation,action='draft_swap_people',source='person:'+pid,target='A#0')
    after=read(rotation,DAY)
    assert after['draft']['plan']['crew']['A#0']==pid
    daily_edit_today(rotation,action='draft_swap_posts',source='A#0',target='B#0')
    after=read(rotation,DAY)
    assert after['draft']['plan']['crew']['B#0']==pid
    assert after['day']['lines'][1]['posts'][0]['occ']==pid
    assert all(p['lineStart'] is None for p in after['day']['persons'])


def test_opening_snapshot_rollback_does_not_leave_frozen_state(rotation,monkeypatch):
    live(rotation)
    with rotation.factory() as db:
        def fail():raise RuntimeError('opening commit failed')
        monkeypatch.setattr(db,'commit',fail)
        with pytest.raises(RuntimeError,match='opening commit'):
            with S.transaction(db):
                row=S.day_row(db,rotation.attraction_id,DAY)
                S.advance(db,S.load_config(db,rotation.attraction_id),row,S.day_state(row),435)
    with rotation.factory() as db:
        assert 'dailyOpening' not in S.day_state(S.day_row(db,rotation.attraction_id,DAY))


def test_legacy_opening_falls_back_to_saved_plan_without_rewriting_history(rotation):
    live(rotation)
    with rotation.factory() as db, S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,DAY);state=S.day_state(row)
        original=state['initialDraft']['lines'][0]['posts'][0]['name']
        state['lines'][0]['posts'][0]['name']='后来的现场岗位'
        S.save_day(db,row,state)
        S.set_sim_clock(S.load_config(db,rotation.attraction_id),FUTURE,240,paused=True)
    first=read(rotation,DAY);second=read(rotation,DAY)
    assert first['opening_source']=='initial_draft' and first['frozen'] and not first['can_edit']
    assert first['day']['lines'][0]['posts'][0]['name']==original
    assert first['revision']==second['revision'] and first['day']==second['day']


def test_late_publish_labels_actual_capture_instead_of_claiming_earlier_snapshot(rotation):
    draft(rotation)
    set_clock(rotation,DAY,600)
    with rotation.factory() as db:
        S.do_draft_action(db,rotation.attraction_id,'test',{'action':'publish','date':DAY})
    result=read(rotation,DAY)
    assert result['frozen'] and not result['can_edit']
    assert result['opening_source']=='late_publish' and result['as_of']==600


def test_unpublished_today_freezes_at_first_shift_but_can_still_publish(rotation):
    draft(rotation)
    before=read(rotation,DAY)
    assert before['can_edit']
    set_clock(rotation,DAY,before['first_start'])
    frozen=read(rotation,DAY)
    assert frozen['frozen'] and not frozen['can_edit'] and not frozen['can_generate']
    assert frozen['can_publish']
    for action in ('draft_rename_post','draft_swap_people','draft_generate','draft_discard'):
        with rotation.factory() as db,pytest.raises(S.ActionError,match='已固定'):
            D.edit(db,rotation.attraction_id,'test',DAY,{'action':action,'revision':frozen['revision']})
    with rotation.factory() as db,pytest.raises(S.ActionError,match='已固定'):
        S.do_draft_action(db,rotation.attraction_id,'test',{'action':'draft_role','date':DAY,'pid':'1','role':'excluded'})
    with rotation.factory() as db:
        D.edit(db,rotation.attraction_id,'test',DAY,{'action':'publish','revision':frozen['revision']})
    published=read(rotation,DAY)
    assert published['frozen'] and not published['can_edit']
    assert [[(p['name'],p['occ']) for p in l['posts']] for l in published['day']['lines']]==[[ (p['name'],p['occ']) for p in l['posts']] for l in frozen['day']['lines']]
