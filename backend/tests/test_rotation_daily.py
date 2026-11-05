"""Daily plans: real isolated SQLite transactions, history and concurrent edit guards."""
from copy import deepcopy

import pytest

from test_rotation_transaction_boundaries import rotation, DAY, draft, live
from app.rotation import service as S
from app.rotation import daily as D
from app.rotation.models import RotationDay, RotationDuty, RotationSegment, RotationNotice, RotationSnapshot

FUTURE = '2026-10-05'


def test_board_and_screen_prearrange_at_exactly_0300_without_early_staffing(rotation):
    with rotation.factory() as db,S.transaction(db):
        cfg=S.load_config(db,rotation.attraction_id)
        S.set_sim_clock(cfg,DAY,180-1/60,paused=True)
    with rotation.factory() as db:
        assert S.board_payload(db,rotation.attraction_id)['day'] is None
        assert S.screen_payload(db,rotation.attraction_id)['day'] is None
        assert S.day_row(db,rotation.attraction_id,DAY) is None
    with rotation.factory() as db,S.transaction(db):
        S.set_sim_clock(S.load_config(db,rotation.attraction_id),DAY,180,paused=True)
    with rotation.factory() as db:
        board=S.board_payload(db,rotation.attraction_id)['day']
        screen=S.screen_payload(db,rotation.attraction_id)['day']
        assert board['status']=='live' and board['lines']==screen['lines']
        assert all(p['state']=='notyet' and p.get('continuousStart') is None for p in board['persons'])
        assert all(line['sortAt']==0 for line in board['lines'])
        assert db.query(RotationSegment).count()==0
        row=S.day_row(db,rotation.attraction_id,DAY)
        assert S.day_state(row)['lastTick']==180


def test_0300_future_prearrangement_preserves_manual_plan(rotation):
    prepare(rotation)
    edit(rotation,action='draft_swap_people',source='A#0',target='B#0')
    expected=deepcopy(read(rotation)['draft']['plan'])
    with rotation.factory() as db,S.transaction(db):
        cfg=S.load_config(db,rotation.attraction_id);S.set_sim_clock(cfg,FUTURE,180,paused=True)
    with rotation.factory() as db:
        data=S.screen_payload(db,rotation.attraction_id)['day']
        assert data['status']=='live'
        assert S.day_state(S.day_row(db,rotation.attraction_id,FUTURE))['plan']==expected
        assert all(p['state']=='notyet' for p in data['persons'])


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


def test_v2_daily_person_shift_sick_and_return_preserve_import_and_configuration(rotation):
    prepare(rotation)
    before = read(rotation)
    pid = next(iter(before['draft']['persons']))
    with rotation.factory() as db:
        cfg = S.load_config(db, rotation.attraction_id)
        config = (cfg.lines_json, cfg.settings_json)
    edit(rotation, action='draft_person', pid=pid, operation='shift', start='08:00', end='00:30')
    changed = read(rotation)['draft']
    assert changed['persons'][pid]['start'] == 480 and changed['persons'][pid]['end'] == 1470
    edit(rotation, action='draft_person', pid=pid, operation='sick')
    changed = read(rotation)['draft']
    assert changed['persons'][pid]['initialPool'] == 'sick'
    assert all(pid not in changed['plan'][kind].values() for kind in ('crew', 'seven'))
    edit(rotation, action='draft_person', pid=pid, operation='back')
    state = deepcopy(read(rotation)['draft'])
    from test_rotation_engine import Hooks
    engine = S.E.Engine(state, dict(S.E.DEFAULT_SETTINGS), Hooks())
    engine.publish(240, 'test')
    engine.tick(480)
    assert engine.P[pid]['state'] == 'rest' and engine.P[pid]['readyAt'] == 495
    with rotation.factory() as db:
        cfg = S.load_config(db, rotation.attraction_id)
        assert (cfg.lines_json, cfg.settings_json) == config
    assert before['roster'] == read(rotation)['roster']


@pytest.mark.parametrize('lid',['D2','E'])
def test_future_standby_line_can_enable_assign_and_disable_without_starting_timers(rotation,lid):
    with rotation.factory() as db,S.transaction(db):
        cfg=S.load_config(db,rotation.attraction_id)
        cfg.lines_json=S._dumps(S.lines_of(cfg)+[{'id':lid,'standby':True,'posts':[{'name':'备用'+str(i)} for i in range(5)]}])
        configured=cfg.lines_json
    prepare(rotation);before=read(rotation);original=deepcopy(before['draft']['plan'])
    from test_rotation_engine import Hooks
    from app.rotation import workflow as W
    board=S.E.Engine(deepcopy(before['draft']),dict(S.E.DEFAULT_SETTINGS),Hooks());W.initialize(board);board.publish(240,'board')
    board.act_line(lid,True,240,'board')
    edit(rotation,action='draft_line',line=lid,active=True)
    opened=read(rotation);line=next(l for l in opened['draft']['lines'] if l['id']==lid)
    assert not line['standby'] and all(post['open'] for post in line['posts'][:5])
    assert not line['posts'][5]['open']
    assert next(l for l in opened['day']['lines'] if l['id']==lid)['active']
    assigned=set(original['crew'].values())|set(original['seven'].values())
    pid=next(pid for pid in opened['draft']['persons'] if pid not in assigned)
    edit(rotation,action='draft_swap_people',source='person:'+pid,target=lid+'#4')
    board.act_plan_place(pid,lid,4,240,'board')
    assert board.d['plan']==read(rotation)['draft']['plan']
    assert read(rotation)['draft']['plan']['crew'][lid+'#4']==pid
    edit(rotation,action='draft_line',line=lid,active=False)
    board.act_line(lid,False,240,'board')
    closed=read(rotation)['draft'];assert closed['plan']==original
    assert board.d['plan']==closed['plan']
    assert next(l for l in closed['lines'] if l['id']==lid)['standby']
    with rotation.factory() as db:
        assert S.load_config(db,rotation.attraction_id).lines_json==configured
        assert db.query(RotationSegment).count()==0
        row=S.day_row(db,rotation.attraction_id,FUTURE);assert row.status=='draft'
        assert not S.day_state(row).get('confirmations')


def test_prearranged_line_enable_survives_publish_but_not_global_template(rotation):
    with rotation.factory() as db,S.transaction(db):
        cfg=S.load_config(db,rotation.attraction_id)
        cfg.lines_json=S._dumps(S.lines_of(cfg)+[{'id':'D2','standby':True,'posts':[{'name':'铁马A'}]}])
    prepare(rotation);edit(rotation,action='draft_line',line='D2',active=True)
    with rotation.factory() as db,S.transaction(db):S.set_sim_clock(S.load_config(db,rotation.attraction_id),FUTURE,240,paused=True)
    edit(rotation,action='publish')
    with rotation.factory() as db:
        state=S.day_state(S.day_row(db,rotation.attraction_id,FUTURE))
        assert next(l for l in state['lines'] if l['id']=='D2')['active']
        assert all(p.get('continuousStart') is None for p in state['persons'].values())
        assert db.query(RotationSegment).count()==0
        assert next(l for l in S.lines_of(S.load_config(db,rotation.attraction_id)) if l['id']=='D2')['standby']


def test_prearranged_line_disable_keeps_other_early_relief_pair(rotation):
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        state['persons']['6']={**deepcopy(state['persons']['5']),'pid':'6'}
        for lid in ['A','B']:
            key=lid+'#0';pid=state['plan']['crew'].pop(key)
            state['persons'][pid]['start']=420;D.slot(state,key)[0]['posts'][0]['seven']=1 if lid=='A' else 2
            state['plan']['seven'][key]=pid
        targets=dict(zip(['5','6'],state['plan']['seven'].values()))
        state['plan']['push7']=['5','6'];S.save_day(db,row,state)
    edit(rotation,action='draft_line',line='A',active=False)
    state=read(rotation)['draft']
    assert state['plan']['push7']==['6'] and state['plan']['push7Targets']=={'6':targets['6']}
    assert not any(key.startswith('A#') for kind in ('crew','seven') for key in state['plan'][kind])


@pytest.mark.parametrize('line,active',[('missing',True),('A','true'),('A',1),(None,True)])
def test_prearranged_line_rejects_invalid_or_stale_changes(rotation,line,active):
    prepare(rotation);before=read(rotation)
    with pytest.raises(S.ActionError):edit(rotation,action='draft_line',line=line,active=active)
    assert read(rotation)['draft']==before['draft']
    with rotation.factory() as db,pytest.raises(S.ConfirmationConflict):
        D.edit(db,rotation.attraction_id,'测试',FUTURE,{'action':'draft_line','line':'A','active':False,'revision':0})


def test_post_menu_saves_only_the_selected_day_and_activates_at_the_planned_times(rotation):
    prepare(rotation)
    original = read(rotation)['draft']
    pid = original['plan']['crew']['A#0']
    edit(rotation, action='draft_post', post='A#0', change='hold', reason='现场任务')
    with pytest.raises(S.ActionError, match='预予定岗'):
        edit(rotation, action='draft_swap_people', source='A#0', target='B#0')
    with pytest.raises(S.ActionError, match='取消定岗'):
        edit(rotation, action='draft_post', post='A#0', change='close')
    edit(rotation, action='draft_post', post='B#0', change='close')
    assert 'B#0' not in read(rotation)['draft']['plan']['crew']
    edit(rotation, action='draft_post', post='B#0', change='open', time='08:00')
    edit(rotation, action='draft_post', post='B#0', change='close', time='09:00')
    edit(rotation, action='draft_post', post='B#0', change='cancel_close')
    assert not read(rotation)['draft']['lines'][1]['posts'][0]['closeAt']
    edit(rotation, action='draft_post', post='B#0', change='close', time='09:00')
    edit(rotation, action='draft_post', post='A#1', change='close')
    planned = read(rotation)['draft']
    assert planned['lines'][0]['posts'][0]['draftHold']['pid'] == pid
    with rotation.factory() as db:
        assert db.query(RotationSegment).count() == 0
        cfg = S.load_config(db, rotation.attraction_id)
        assert not S.lines_of(cfg)[0]['posts'][0].get('draftHold')
        assert not S.lines_of(cfg)[1]['posts'][0].get('openAt')
    from test_rotation_engine import Hooks
    engine = S.E.Engine(deepcopy(planned), dict(S.E.DEFAULT_SETTINGS), Hooks())
    engine.publish(240, 'test')
    assert not engine.held(pid) and engine.P[pid]['state'] == 'notyet'
    assert not engine.line('B')['posts'][0]['open']
    assert not engine.line('A')['posts'][1]['open']
    engine.tick(435)
    assert engine.held(pid) and engine.P[pid]['continuousStart'] == 435
    engine.tick(480)
    assert engine.line('B')['posts'][0]['open']
    engine.tick(540)
    assert not engine.line('B')['posts'][0]['open']


def test_spares_use_existing_rename_flow_without_changing_other_slots(rotation):
    with rotation.factory() as db, S.transaction(db):
        cfg = S.load_config(db, rotation.attraction_id)
        cfg.lines_json = S._dumps(S.E.DEFAULT_LINES)
    prepare(rotation)
    before = read(rotation)
    assert all(len(line['posts']) == 6 for line in before['draft']['lines'])
    plan = deepcopy(before['draft']['plan'])
    original = deepcopy(before['draft']['lines'])
    identities = None
    for key, name in [('D2#3', '备用改名甲'), ('D2#4', '备用改名乙'),
                      ('E#2', '备用改名丙'), ('E#3', '备用改名丁'), ('E#4', '备用改名戊'),
                      ('A1#5', '备用改名己')]:
        edit(rotation, action='draft_rename_post', post=key, expected_name='备用', name=name)
        current_keys = [post['key'] for line in read(rotation)['draft']['lines'] for post in line['posts']]
        if identities is None:
            identities = current_keys  # Existing rename flow initializes workflow identities once.
            assert len(set(identities)) == 54
        assert current_keys == identities
        line_id, index = key.split('#')
        next(line for line in original if line['id'] == line_id)['posts'][int(index)]['name'] = name
    after = read(rotation)
    actual = deepcopy(after['draft']['lines'])
    for lines in (actual, original):
        for line in lines:
            for post in line['posts']:
                post.pop('key', None)
                post.pop('hold101Role', None)
    assert actual == original
    assert after['draft']['plan'] == plan
    assert next(line for line in after['draft']['lines'] if line['id'] == 'D2')['standby']
    assert next(line for line in after['draft']['lines'] if line['id'] == 'E')['standby']


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
    assert after['times'] and after['as_of'] == S.PREARRANGE_MINUTE


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


def test_seven_post_accepts_another_shift_and_post_swap_carries_assignment(rotation):
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
    later=before['draft']['plan']['crew']['B#0']
    edit(rotation, action='draft_swap_people', source='A#0', target='B#0')
    swapped=read(rotation)['draft']
    assert swapped['plan']['crew']['A#0']==later and swapped['plan']['crew']['B#0']==pid
    assert 'A#0' not in swapped['plan']['seven']
    edit(rotation, action='draft_swap_posts', source='A#0', target='A#1')
    after = read(rotation)['draft']
    assert after['plan']['crew']['A#1'] == later
    assert after['plan']['crew']['A#0'] == before['draft']['plan']['crew']['A#1']
    assert after['lines'][0]['posts'][1]['seven'] == 1
    for bad in ([], {}, None):
        with pytest.raises(S.ActionError): edit(rotation, action=bad)


@pytest.mark.parametrize('post_name',['小C','迎宾','快迎1'])
@pytest.mark.parametrize('shift_start',[435,540])
def test_other_shift_on_early_post_keeps_own_clock_and_is_not_push7_target(rotation,post_name,shift_start):
    from test_rotation_engine import Hooks
    from app.rotation import workflow as W
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        early=state['plan']['crew'].pop('A#0');later=state['plan']['crew']['B#0']
        state['persons'][early]['start']=420;state['persons'][later]['start']=shift_start
        state['lines'][0]['posts'][0].update(name=post_name,seven=1)
        state['plan']['seven']['A#0']=early
        state['plan']['push7']=['relief']
        state['persons']['relief']={**deepcopy(state['persons'][later]),'pid':'relief','start':435}
        S.save_day(db,row,state)
    edit(rotation,action='draft_swap_people',source='A#0',target='B#0')
    saved=read(rotation)['draft']
    assert saved['lines'][0]['posts'][0]['seven']==1
    assert saved['persons'][later]['start']==shift_start and saved['persons'][early]['start']==420
    assert saved['plan']['crew']['A#0']==later and saved['plan']['seven']=={}
    assert saved['plan']['push7']==[]
    eng=S.E.Engine(deepcopy(saved),dict(S.E.DEFAULT_SETTINGS),Hooks());W.initialize(eng)
    eng.publish(419,'test');eng.tick(420)
    assert eng.P[later]['state']=='notyet' and eng.P[later]['continuousStart'] is None
    eng.tick(shift_start)
    assert eng.P[later]['state']=='onpost' and eng.P[later]['continuousStart']==shift_start
    assert '7点岗' not in eng.P[later]['flags'] and eng._push7_pairs()==[]


def test_early_post_dropdown_and_pool_replacements_do_not_duplicate_slot(rotation):
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        early=state['plan']['crew'].pop('A#0');later=state['plan']['crew']['B#0']
        state['persons'][early]['start']=420;state['lines'][0]['posts'][0]['seven']=1
        state['plan']['seven']['A#0']=early;S.save_day(db,row,state)
    edit(rotation,action='draft_set',kind='seven',key='A#0',pid=later)
    current=read(rotation)['draft']
    assert current['plan']['crew']['A#0']==later and 'A#0' not in current['plan']['seven']
    edit(rotation,action='draft_swap_people',source='person:'+early,target='A#0')
    current=read(rotation)['draft']
    assert current['plan']['seven']['A#0']==early and 'A#0' not in current['plan']['crew']
    assert all(later not in current['plan'][k].values() for k in ('seven','crew'))


@pytest.mark.parametrize('source_seated',[True,False])
@pytest.mark.parametrize('timed',[True,False])
def test_same_prestart_person_change_matches_daily_board_and_published_plan(rotation,source_seated,timed):
    from test_rotation_engine import Hooks
    from app.rotation import workflow as W
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        early=state['plan']['crew'].pop('A#0');later=state['plan']['crew']['B#0']
        state['persons'][early]['start']=420;state['persons'][later]['start']=540
        state['lines'][0]['posts'][0].update(seven=1,openAt='09:00' if timed else None)
        state['plan']['seven']['A#0']=early
        if not source_seated:state['plan']['crew'].pop('B#0')
        S.save_day(db,row,state)
    before=read(rotation)['draft'];board=S.E.Engine(deepcopy(before),dict(S.E.DEFAULT_SETTINGS),Hooks());W.initialize(board);board.publish(419,'board')
    board.act_plan_place(later,'A',0,419,'board')
    edit(rotation,action='draft_swap_people',source='B#0' if source_seated else 'person:'+later,target='A#0')
    daily=read(rotation)
    assert board.d['plan']==daily['draft']['plan']
    actual_lines=S.lines_view(board)
    for lines in [actual_lines,daily['day']['lines']]:
        post=next(line for line in lines if line['id']=='A')['posts'][0]
        assert post['occ']==later and post['prearranged']
        assert post['open'] is (not timed) and post['since'] is None
        assert sum(p['occ']==later for line in lines for p in line['posts'])==1
    assert board.P[later]['continuousStart'] is None
    board.tick(540)
    assert board.P[later]['state']=='onpost' and board.P[later]['continuousStart']==540
    assert '7点岗' not in board.P[later]['flags']


@pytest.mark.parametrize('target',[('A',1),('B',0)])
@pytest.mark.parametrize('second_seven',[False,True])
def test_prestart_swap_only_posts_keeps_staff_and_does_not_transfer_seven_duty(rotation,target,second_seven):
    from test_rotation_engine import Hooks
    from app.rotation import workflow as W
    prepare(rotation)
    state=read(rotation)['draft']
    target_line,target_index=target
    target_key=f'{target_line}#{target_index}'
    other_key='B#1'
    early,later,unaffected,old_relief,other_relief=list(state['persons'])[:5]
    for pid in (early,unaffected):state['persons'][pid]['start']=420
    state['persons'][later]['start']=420 if second_seven else 540
    state['lines'][0]['posts'][0]['seven']=1
    state['lines'][1]['posts'][1]['seven']=2
    state['plan'].update(seven={'A#0':early,other_key:unaffected},
        crew={target_key:later},push7=[old_relief,other_relief],lost=[])
    if second_seven:
        target_post=next(line for line in state['lines'] if line['id']==target_line)['posts'][target_index]
        target_post['seven']=3
        state['plan']['seven'][target_key]=state['plan']['crew'].pop(target_key)
    state['plan'].pop('push7Targets',None)
    board=S.E.Engine(state,dict(S.E.DEFAULT_SETTINGS),Hooks())
    W.initialize(board)
    board.publish(419,'board')
    board.act_post_order('A',0,target_index,419,'board',swap_mode='posts',target_lid=target_line)
    if second_seven:
        assert state['plan']['crew']=={}
        assert state['plan']['seven']=={'A#0':early,target_key:later,other_key:unaffected}
        assert state['plan']['push7Targets']=={old_relief:early,other_relief:unaffected}
        assert state['plan']['push7']==[old_relief,other_relief]
    else:
        assert state['plan']['crew']=={'A#0':early,target_key:later}
        assert state['plan']['seven']=={other_key:unaffected}
        assert state['plan']['push7Targets']=={other_relief:unaffected}
        assert state['plan']['push7']==[other_relief]
    assert board.post('A',0)[2]['occ']==early
    assert board.post(target_line,target_index)[2]['occ']==later
    assert board.post(target_line,target_index)[2]['seven']==1
    assert board.P[later]['continuousStart'] is None
    start=state['persons'][later]['start']
    board.tick(start)
    assert board.P[later]['continuousStart']==start
    assert ('7点岗' in board.P[later]['flags']) is second_seven


def test_seven_post_replacement_preserves_other_eligible_relief_workers(rotation):
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        prototype=deepcopy(state['persons']['5'])
        state['persons']['6']={**prototype,'pid':'6'}
        state['plan']['crew']={};state['plan']['seven']={}
        state['lines'][0]['posts'].append({**deepcopy(state['lines'][0]['posts'][0]),
            'name':'第三7点岗位','key':'extra-seven-test','occ':None,'since':None})
        for i,pid in enumerate(['1','2','3']):
            state['persons'][pid]['start']=420
            state['lines'][0]['posts'][i]['seven']=i+1
            state['plan']['seven']['A#'+str(i)]=pid
        state['plan']['push7']=['4','5','6'];S.save_day(db,row,state)
    edit(rotation,action='draft_swap_people',source='person:4',target='A#0')
    state=read(rotation)['draft']
    assert state['plan']['crew']['A#0']=='4'
    assert state['plan']['seven']=={'A#1':'2','A#2':'3'}
    assert state['plan']['push7']==['5','6']


@pytest.mark.parametrize('index',[0,1,2])
@pytest.mark.parametrize('entry',['pool','dropdown','swap'])
def test_replacing_any_early_post_preserves_other_relief_targets(rotation,index,entry):
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        prototype=deepcopy(state['persons']['5'])
        for pid in ['6','7']:state['persons'][pid]={**deepcopy(prototype),'pid':pid}
        state['persons']['7']['start']=540
        state['lines'][0]['posts'].append({**deepcopy(state['lines'][0]['posts'][0]),'name':'第三7点岗位','key':'third-early','occ':None,'since':None})
        state['plan']['crew']={'B#0':'7'} if entry=='swap' else {}
        state['plan']['seven']={}
        for i,pid in enumerate(['1','2','3']):
            state['persons'][pid]['start']=420;state['lines'][0]['posts'][i]['seven']=i+1
            state['plan']['seven']['A#'+str(i)]=pid
        state['plan']['push7']=['4','5','6'];S.save_day(db,row,state)
    key='A#'+str(index)
    if entry=='dropdown':edit(rotation,action='draft_set',kind='seven',key=key,pid='7')
    else:edit(rotation,action='draft_swap_people',source='B#0' if entry=='swap' else 'person:7',target=key)
    saved=read(rotation)['draft'];expected={relay:target for i,(relay,target) in enumerate(zip(['4','5','6'],['1','2','3'])) if i!=index}
    assert saved['plan']['push7Targets']==expected
    assert saved['plan']['push7']==list(expected)
    assert saved['plan']['crew'][key]=='7' and saved['persons']['7']['start']==540
    from test_rotation_engine import Hooks
    from app.rotation import workflow as W
    eng=S.E.Engine(deepcopy(saved),dict(S.E.DEFAULT_SETTINGS),Hooks());W.initialize(eng)
    eng.publish(419,'test');eng.tick(435)
    assert {a:b for _,a,b in eng._push7_pairs()}==expected
    assert eng.P['7']['state']=='notyet' and eng.P['7']['continuousStart'] is None
    # Adding the original early worker back must not silently retarget peers.
    if entry!='swap':
        edit(rotation,action='draft_swap_people',source='person:'+str(index+1),target=key)
        restored=read(rotation)['draft']
        assert restored['plan']['push7Targets']==expected
        slots=['1','2','3'];selected=[expected.get(relay) and relay or '' for relay in ['4','5','6']]
        edit(rotation,action='draft_list',kind='push7',pids=selected,targets=slots)
        assert read(rotation)['draft']['plan']['push7Targets']==expected
        before_bad=read(rotation)['draft']
        with pytest.raises(S.ConfirmationConflict):
            edit(rotation,action='draft_list',kind='push7',pids=selected,targets=list(reversed(slots)))
        assert read(rotation)['draft']==before_bad
        edit(rotation,action='draft_list',kind='push7',pids=['4','5','6'])
        manual=read(rotation)['draft']
        assert 'push7Targets' not in manual['plan']
        assert S.E.Engine(manual,dict(S.E.DEFAULT_SETTINGS),Hooks())._push7_pairs()==[(0,'4','1'),(1,'5','2'),(2,'6','3')]


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


def legacy_standard_day(rotation,formal=False,work_date=FUTURE):
    from app.rotation.namespace import tables_for
    with rotation.factory() as db:
        cfg=S.load_config(db,rotation.attraction_id)
        old=deepcopy(S.E.DEFAULT_LINES)
        for line in old:
            line['posts'].pop()
        old[-2]['posts']=old[-2]['posts'][:3];old[-1]['posts']=old[-1]['posts'][:2]
        cfg.lines_json=S._dumps(old);db.commit()
    prepare(rotation)
    with rotation.factory() as db,S.transaction(db):
        row=S.day_row(db,rotation.attraction_id,FUTURE);state=S.day_state(row)
        # Preserve a real custom name, assignment and all original stable identities.
        state['lines'][0]['posts'][0]['name']='已修改名称'
        state['date']=work_date
        if formal:
            db.info['rotation_formal']=True
            cfg=S.load_config(db,rotation.attraction_id);S.set_sim_clock(cfg,DAY,240,paused=True)
            row=tables_for(db).RotationDay(attraction_id=rotation.attraction_id,work_date=work_date,status='draft',state_json=S._dumps(state),version=1)
            db.add(row);db.flush()
        else:
            row.work_date=work_date;S.save_day(db,row,state)
        return deepcopy(state),row.version


@pytest.mark.parametrize('formal',[False,True])
def test_existing_unstarted_plans_fill_spares_without_resetting_arrangement(rotation,formal):
    before,version=legacy_standard_day(rotation,formal)
    def load():
        with rotation.factory() as db:
            db.info['rotation_formal']=formal
            return D.day_payload(db,rotation.attraction_id,FUTURE)
    after=load();lines={l['id']:l for l in after['draft']['lines']}
    assert all(len(line['posts']) == 6 and line['posts'][5]['draftClosed'] for line in lines.values())
    assert [p['name'] for p in lines['D2']['posts'][3:]]==['备用']*3
    assert [p['name'] for p in lines['E']['posts'][2:]]==['备用']*4
    assert after['draft']['plan']==before['plan'] and after['draft']['persons']==before['persons']
    for a,b in zip(after['draft']['lines'],before['lines']):assert a['posts'][:len(b['posts'])]==b['posts']
    keys=[p['key'] for line in after['draft']['lines'] for p in line['posts'] if p.get('name')=='备用']
    assert len(set(keys))==14
    assert after['revision']==version+1
    again=load();assert again['draft']==after['draft'] and again['revision']==after['revision']
    with rotation.factory() as db:
        from app.rotation.namespace import tables_for
        db.info['rotation_formal']=formal
        assert db.query(tables_for(db).RotationEvent).filter_by(event_type='draft_fill_spares').count()==1
    if formal:
        # Upgrading formal storage must not modify the independent sandbox plan.
        with rotation.factory() as db:assert len(S.day_state(S.day_row(db,rotation.attraction_id,FUTURE))['lines'][-1]['posts'])==2


@pytest.mark.parametrize('work_date,minute',[(DAY,240),(DAY,500),('2026-10-03',240)])
def test_fill_only_before_first_shift_never_rewrites_frozen_or_past_day(rotation,work_date,minute):
    before,version=legacy_standard_day(rotation,work_date=work_date)
    with rotation.factory() as db:
        cfg=S.load_config(db,rotation.attraction_id);S.set_sim_clock(cfg,DAY,minute,paused=True);db.commit()
    read(rotation,work_date)
    with rotation.factory() as db:
        row=S.day_row(db,rotation.attraction_id,work_date);stored=S.day_state(row)
        if work_date==DAY and minute<435:
            assert len(stored['lines'][-1]['posts'])==6 and row.version==version+1
        else:
            assert stored==before and row.version==version


def test_spare_fill_keeps_custom_lines_and_does_not_trim_extra_posts():
    state={'lines':[{'id':'F','posts':[{'name':'自定义'}]}, {'id':'A1','posts':[{'name':str(i)} for i in range(6)]}]}
    before=deepcopy(state);assert D.fill_prestart_spares(state)=={};assert state==before


def test_enabled_today_before_first_shift_adds_only_closed_empty_spares(rotation):
    legacy_standard_day(rotation,work_date=DAY)
    with rotation.factory() as db,S.transaction(db):
        cfg=S.load_config(db,rotation.attraction_id)
        row=S.day_row(db,rotation.attraction_id,DAY)
        state=S.day_state(row)
        engine,_=S.engine_for(db,cfg,row,state)
        engine.publish(240,'test')  # Simulate a day already published by the earlier code.
        S.save_day(db,row,state)
        before=deepcopy(state)
        snapshots={r.id:r.payload for r in db.query(RotationSnapshot).all()}
    S.tick_attraction(rotation.attraction_id)
    with rotation.factory() as db:
        assert len(S.board_payload(db,rotation.attraction_id)['day']['lines'][-1]['posts'])==6
    after=read(rotation,DAY)
    assert after['prestart_live'] and after['draft']['plan']==before['plan']
    for line in after['draft']['lines']:
        old=next(l for l in before['lines'] if l['id']==line['id'])
        assert line['posts'][:len(old['posts'])]==old['posts']
        for post in line['posts'][len(old['posts']):]:
            assert post['name']=='备用' and not post['open'] and post['occ'] is None
    with rotation.factory() as db:
        assert all(db.get(RotationSnapshot,key).payload==payload for key,payload in snapshots.items())


def test_legacy_spare_fill_commit_failure_rolls_back_and_can_retry(rotation,monkeypatch):
    before,version=legacy_standard_day(rotation)
    with rotation.factory() as db:
        def fail():raise RuntimeError('test fill commit failure')
        monkeypatch.setattr(db,'commit',fail)
        with pytest.raises(RuntimeError,match='fill commit failure'):D.day_payload(db,rotation.attraction_id,FUTURE)
    with rotation.factory() as db:
        row=S.day_row(db,rotation.attraction_id,FUTURE)
        assert row.version==version and S.day_state(row)==before
        from app.rotation.models import RotationEvent
        assert db.query(RotationEvent).filter_by(event_type='draft_fill_spares').count()==0
    assert len(read(rotation)['draft']['lines'][-1]['posts'])==6


@pytest.mark.parametrize('automatic', [False, True])
def test_legacy_draft_opens_with_closed_spares_without_daily_page_visit(rotation, automatic):
    before, _ = legacy_standard_day(rotation, work_date=DAY)
    with rotation.factory() as db:
        cfg = S.load_config(db, rotation.attraction_id)
        if automatic:
            with S.transaction(db):
                row = S.day_row(db, rotation.attraction_id, DAY)
                state = S.day_state(row)
                state.update(dailyAutoStart=True, rosterUploadId=S.roster_upload_for(db, rotation.attraction_id, DAY).id)
                S.save_day(db, row, state)
            S.ensure_prearranged_day(db, cfg, rotation.attraction_id, DAY, 240)
        else:
            S.do_draft_action(db, rotation.attraction_id, 'test', {'action': 'publish', 'date': DAY})
        row = S.day_row(db, rotation.attraction_id, DAY)
        state = S.day_state(row)
        assert row.status == 'live' and state['plan'] == before['plan']
        assert all(len(line['posts']) == 6 and not line['posts'][5]['open'] for line in state['lines'])
        from test_rotation_engine import Hooks
        engine = S.E.Engine(state, S.settings_of(cfg), Hooks())
        engine.tick(435)
        engine.act_line('D2', True, 436, 'test')
        assert all(post['open'] for post in engine.line('D2')['posts'][:5])
        assert not engine.line('D2')['posts'][5]['open']
        assert 5 not in engine.rotation_posts(engine.line('D2'))
        engine.act_post('D2', 5, True, 436, 'test', open_at=440)
        engine.tick(439)
        assert not engine.line('D2')['posts'][5]['open']
        engine.tick(440)
        assert engine.line('D2')['posts'][5]['open']
