"""Personal task routes and continuous-time invariants, without a business DB."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from app.rotation import workflow as W
from app.rotation.engine import ActionError
from test_rotation_engine import person, live_day, seat, two_lines, occupants


def fixture(count=3):
    people=[person('old'+str(i),'07:00','21:00') for i in range(5)]
    people += [person('new'+str(i),'09:00','21:00') for i in range(count)]
    eng,hooks=live_day(people,lines=two_lines(5))
    for i in range(5):seat(eng,'old'+str(i),'A',i,'08:00')
    W.initialize(eng)
    for i,minutes in enumerate([30,40,90,70,80]):eng.P['old'+str(i)]['continuousStart']=540-minutes
    for i in range(count):
        eng.P['new'+str(i)].update(state='ready',readyAt=540+i*.5,
                                  assign={'line':'A','mode':'push','departAt':540+i*.5})
    return eng,hooks


def test_shift_preparation_appears_at_25_minutes_without_a_replacement():
    from app.rotation.service import person_view

    eng, _ = fixture(0)
    p = eng.P['old0']
    p['end'] = 565
    assert person_view(eng, p, 539.99, {})['preparing'] is None
    eng.tick(540)
    assert person_view(eng, p, 540, {})['preparing'] == '推下班'
    assert p['state'] == 'onpost' and eng.targeted() == {}


def test_new_dispatch_prioritizes_shift_end_over_out_and_ordinary_forecasts():
    eng, _ = fixture(2)
    early = eng.P['new0']
    late = eng.P['new1']
    eng.P['old0']['end'] = 565
    eng.P['old1']['absences'] = [{'start': 550, 'end': 610, 'label': '专项'}]
    early.update(state='rest', readyAt=543, assign=None)
    late.update(state='rest', readyAt=548, assign=None)
    eng.tick(539)
    assert early['assign']['target'] == 'old1'
    assert W.task_for(eng, early) is None

    eng.tick(540)
    task = W.task_for(eng, early)
    assert task['template']['target'] == 'old0'
    assert task['template']['why'] == '推下班'
    assert task['allowedAt'] == 543
    assert late['assign']['target'] == 'old1'
    assert late['assign']['why'] == '推出圈'
    assert W.task_for(eng, late) is None


@pytest.mark.parametrize('continuous_starts, expected', [((450, 460), 'old1'), ((450, 450), 'old3')])
def test_simultaneous_shift_ends_use_continuous_time_then_later_post(continuous_starts, expected):
    eng, _ = fixture(1)
    eng.P['old1'].update(end=565, continuousStart=continuous_starts[0], lineStart=530)
    eng.P['old3'].update(end=565, continuousStart=continuous_starts[1], lineStart=490)
    eng.tick(540)
    assert W.task_for(eng, eng.P['new0'])['template']['target'] == expected


@pytest.mark.parametrize('earlier_line', ['A', 'B'])
def test_overdue_earlier_shift_is_replaced_before_later_shift(earlier_line):
    eng, _ = fixture(1)
    if earlier_line == 'B':
        seat(eng, 'old1', 'B', 1, '12:50')
    eng.P['old1'].update(end=795, continuousStart=770)  # 13:15
    eng.P['old3'].update(end=825, continuousStart=730)  # 13:45, standing longer
    newcomer = eng.P['new0']
    newcomer.update(readyAt=800, assign=None)
    eng.tick(800)  # 13:20, both shifts are eligible for relief.
    task = W.task_for(eng, newcomer)
    assert task['template']['target'] == 'old1'
    assert task['line'] == earlier_line


@pytest.mark.parametrize('first_end, expected_owners', [(825, ['new1']), (795, ['new0', 'new1'])])
def test_same_line_batch_keeps_shift_priority_over_owner_number(first_end, expected_owners):
    eng, _ = fixture(2)
    for owner, target, end in [('new0', 'old1', first_end), ('new1', 'old3', 825)]:
        eng.P[target].update(end=end, continuousStart=730)
        eng.P[owner]['readyAt'] = 800
        eng.P[owner]['assign'].update(mode='chain', target=target, why='推下班', departAt=800)
    assert W.plan(eng, 800)
    tasks = list(eng.d['rotationTasks'].values())
    assert [task['owner'] for task in tasks] == expected_owners
    assert [task['template']['target'] for task in tasks] == [
        {'new0': 'old1', 'new1': 'old3'}[owner] for owner in expected_owners
    ]
    if len(tasks) == 2:
        assert not W.resources(tasks[0]) & W.resources(tasks[1])


def test_shift_end_priority_does_not_redirect_an_issued_task():
    eng, _ = fixture(2)
    first = eng.P['new0']
    second = eng.P['new1']
    eng.P['old4']['end'] = 565
    first.update(state='rest', readyAt=542, assign={'line': 'A', 'mode': 'push', 'departAt': 542})
    second.update(state='rest', readyAt=545, assign=None)
    assert W.plan(eng, 539)
    issued = deepcopy(W.task_for(eng, first))
    eng.tick(540)
    assert W.task_for(eng, first) == issued
    assert second['assign']['target'] == 'old4'


@pytest.mark.parametrize('remaining, expected', [(30.01, 'rest'), (30, 'done'), (29.99, 'done')])
def test_arrival_uses_actual_remaining_shift_minutes(remaining, expected):
    eng, _ = fixture(0)
    p = eng.P['old0']
    p['end'] = 540 + remaining
    eng.act_fix_remove(p['pid'], '现场移出', 537, 'supervisor')
    assert p['state'] == 'walkback'
    eng.act_arrive(p['pid'], 540, 'screen')
    assert p['state'] == expected


@pytest.mark.parametrize('remaining, expected', [(30.01, 'walkback'), (30, 'done'), (29.99, 'done')])
def test_ordinary_task_uses_actual_push_off_time_and_preserves_the_route(remaining, expected):
    from app.rotation.service import person_view

    eng, hooks = fixture(1)
    outgoing = eng.P['old2']  # Longest continuous time in the fixture.
    outgoing['end'] = 542 + remaining
    incoming = eng.P['new0']
    assert W.plan(eng, 537)
    task = W.task_for(eng, incoming)
    assert task['template']['mode'] == 'push'
    assert task['moves'][-1]['outcome'] == 'rest'  # Above 30 minutes at issue time and allowed time.
    issued_moves = deepcopy(task['moves'])
    earlier = person_view(eng, incoming, 540, eng.targeted())
    current = person_view(eng, incoming, 542, eng.targeted())
    assert earlier['confirmationTokens']['depart'] == current['confirmationTokens']['depart']
    assert earlier['assign'] == current['assign']
    assert {**earlier['task'], 'routes': current['task']['routes']} == current['task']
    assert current['departureRoutes'] == current['task']['routes']
    assert earlier['departureRoutes'][-1]['to'] == '下岗，回休息室确认休息／吃饭'
    assert current['departureRoutes'][-1]['to'] == ('下班' if expected == 'done' else '下岗，回休息室确认休息／吃饭')
    eng.act_depart(incoming['pid'], 542, 'screen')
    assert outgoing['state'] == expected
    assert outgoing['downReason'] == '正常轮岗'
    assert task['moves'] == issued_moves
    pushed_off = next(event for event in hooks.events if event['type'] == 'pushed_off')
    departure = next(event for event in hooks.events if event['type'] == 'depart')
    assert pushed_off['detail']['outcome'] == expected
    actual_route = next(route for route in departure['detail']['routes'] if route['pid'] == outgoing['pid'])
    assert actual_route['to'] == ('下班' if expected == 'done' else '下岗，回休息室确认休息／吃饭')
    assert occupants(eng, 'A') == ['new0', 'old0', 'old1', 'old3', 'old4']
    assert hooks.segments.count((outgoing['pid'], 'A', 480, 542)) == 1


@pytest.mark.parametrize('expected, reason, destination', [('done', '推下班', '下班'), ('away', '推出圈', '出圈')])
def test_chain_departure_logs_the_executed_destination(expected, reason, destination):
    eng, hooks = fixture(1)
    outgoing = eng.P['old0']
    if expected == 'done':
        outgoing['end'] = 565
    else:
        outgoing['absences'] = [{'start': 550, 'end': 610, 'label': '专项'}]
    incoming = eng.P['new0']
    eng.tick(540)
    task = W.task_for(eng, incoming)
    assert task['template']['why'] == reason
    outgoing['end'] = 565  # A committed out-of-circle task remains out of circle in the display.
    assert W.task_view(eng, incoming, 540)['routes'][-1]['to'] == destination
    eng.act_depart(incoming['pid'], 540, 'screen')
    pushed_out = next(event for event in hooks.events if event['type'] == 'pushed_out')
    departure = next(event for event in hooks.events if event['type'] == 'depart')
    assert outgoing['state'] == expected
    assert pushed_out['detail'] == {'reason': reason, 'outcome': expected}
    assert departure['detail']['routes'][-1]['to'] == destination


def test_minute_cohort_has_one_deadline_and_independent_longest_first_routes():
    eng,_=fixture();assert W.plan(eng,538)
    assert [W.task_for(eng,eng.P['new'+str(i)])['allowedAt'] for i in range(3)]==[541]*3
    assert [W.task_for(eng,eng.P['new'+str(i)])['deadlineAt'] for i in range(3)]==[544]*3
    assert [W.task_view(eng,eng.P['new'+str(i)])['entryPost'] for i in range(3)]==['A2','A1','A0']
    eng.act_depart('new0',541,'screen')
    assert occupants(eng,'A')==['old0','old1','new0','old3','old4']
    assert eng.P['old2']['state']=='walkback'
    eng.act_depart('new2',541,'screen');eng.act_depart('new1',541,'screen')
    assert occupants(eng,'A')==['new2','new1','new0','old0','old1']
    assert eng.P['old0']['continuousStart']==510


def test_whole_round_is_issued_three_minutes_before_latest_ready_without_shortening_rest():
    eng,_=fixture()
    for i in range(3):eng.P['new'+str(i)]['state']='rest'
    assert not W.plan(eng,537)
    assert all(not W.task_for(eng,eng.P['new'+str(i)]) for i in range(3))
    assert all(not eng.assignment_locked(eng.P['new'+str(i)],537) for i in range(3))
    W.plan(eng,538)
    for i in range(3):
        p=eng.P['new'+str(i)];task=W.task_for(eng,p)
        assert task['allowedAt']==541 and task['deadlineAt']==544
        assert task['allowedAt']>=p['readyAt']
        assert eng.assignment_locked(p,538)
        with pytest.raises(ActionError):eng.act_depart(p['pid'],540.9,'screen')
        p['state']='ready'
    for i in range(3):eng.act_depart('new'+str(i),541,'screen')


@pytest.mark.parametrize('break_state',['rest','meal'])
def test_published_task_is_a_departure_notice_while_actual_break_continues(break_state):
    from app.rotation.service import desired_notices
    eng,_=fixture(1);p=eng.P['new0'];p['state']=break_state
    assert not W.plan(eng,536)
    assert desired_notices(eng,536)[(p['pid'],'step')][0]=='rest'
    W.plan(eng,537)
    kind,title,body=desired_notices(eng,537)[(p['pid'],'step')]
    assert kind=='depart' and '待出发' in title
    assert '剩余餐休继续计时' in body
    assert p['state']==break_state and p['readyAt']==540
    with pytest.raises(ActionError):eng.act_depart(p['pid'],539.9,'screen')


def test_one_person_relief_stops_at_longest_middle_post():
    eng,_=fixture(1);eng.P['old1']['continuousStart']=400
    W.plan(eng,540);eng.act_depart('new0',540,'screen')
    assert occupants(eng,'A')==['new0','old0','old2','old3','old4']
    assert eng.P['old1']['state']=='walkback'
    assert eng.P['old1']['lastDeparture']=={'line':'A','post':eng.d['lines'][0]['posts'][1]['name']}


def opening_fixture(count=1):
    eng,hooks=fixture(count)
    post=eng.line('A')['posts'][4];post.update(occ=None,since=None,openRequest=True)
    eng.P['old4'].update(state='away',line=None,lineStart=None,assign=None)
    eng.P['new0']['assign'].update(mode='opening',post=4,openingKey=post['key'])
    return eng,hooks


def test_opening_display_tracks_match_task_timeout_and_actual_execution():
    from app.rotation.service import lines_view
    eng,_=opening_fixture();p=eng.P['new0'];planned=deepcopy(p['assign']);p['assign']=None
    status=lambda:lines_view(eng)[0]['posts'][4]['openingStatus']
    assert status()=='matching'
    p['assign']=planned
    assert status()=='preparing'  # Matched before the formal task is issued.
    W.plan(eng,540)
    assert status()=='preparing'
    # The newcomer enters the first post; the new post is filled by the chain.
    assert W.task_for(eng,p)['moves'][0]['to']!=eng.line('A')['posts'][4]['key']
    W.tick(eng,544)
    assert status()=='matching'
    W.acknowledge_timeout(eng,p,544);W.tick(eng,544)
    assert status()=='preparing'
    eng.act_depart(p['pid'],544,'screen')
    assert status() is None
    assert lines_view(eng)[0]['posts'][4]['occ']=='old3'


def test_cancelled_or_future_opening_does_not_display_as_waiting_to_fill():
    from app.rotation.service import opening_status
    eng,_=opening_fixture();line=eng.line('A');post=line['posts'][4]
    post['open']=False
    assert opening_status(eng,line,4) is None
    post.update(open=True,openRequest=False)
    assert opening_status(eng,line,4) is None


def test_opening_starts_at_first_post_and_fills_added_post_without_rest_relief():
    eng,_=opening_fixture();W.plan(eng,540)
    task=W.task_for(eng,eng.P['new0'])
    assert [m['pid'] for m in task['moves']]==['new0','old0','old1','old2','old3']
    assert all(m['to'] is not None for m in task['moves'])
    eng.act_depart('new0',540,'screen')
    assert occupants(eng,'A')==['new0','old0','old1','old2','old3']
    assert all(eng.P[pid]['state']=='onpost' for pid in occupants(eng,'A'))


def test_opening_skips_101_posts_but_preserves_occupants_and_continuous_time():
    eng,_=opening_fixture();line=eng.line('A')
    for post in line['posts'][:2]:post['hold101']={'since':539,'pid':post['occ']}
    starts={pid:eng.P[pid]['continuousStart'] for pid in ('old0','old1','old2','old3')}
    W.plan(eng,540);task=W.task_for(eng,eng.P['new0'])
    assert task['moves'][0]['to']==line['posts'][2]['key']
    eng.act_depart('new0',540,'screen')
    assert occupants(eng,'A')==['old0','old1','new0','old2','old3']
    assert all(eng.P[pid]['continuousStart']==start for pid,start in starts.items())


def test_opening_waits_for_same_round_downshift_instead_of_entering_mid_line():
    eng,_=opening_fixture(2)
    eng.P['new1']['assign'].update(mode='chain',target='old0',why='推下班')
    eng.P['new1']['assign']['departAt']=540;eng.P['new1']['readyAt']=540
    opening=deepcopy(eng.P['new0']['assign'])
    W.plan(eng,540)
    assert W.task_for(eng,eng.P['new0']) is None
    assert W.task_for(eng,eng.P['new1']) is not None
    eng.act_depart('new1',540,'screen')
    eng.P['new0']['assign']=opening;W.plan(eng,540)
    assert W.task_for(eng,eng.P['new0'])['moves'][0]['to']==eng.line('A')['posts'][0]['key']
    eng.act_depart('new0',540,'screen')
    assert occupants(eng,'A')==['new0','new1','old1','old2','old3']


def test_opening_waits_for_an_issued_prefix_route():
    eng,_=fixture(2)
    eng.P['new1'].update(assign=None,state='rest',readyAt=545)
    W.plan(eng,540);first=deepcopy(W.task_for(eng,eng.P['new0']))
    line=eng.line('A');line['posts'][4].update(occ=None,since=None,openRequest=True)
    eng.P['old4'].update(state='away',line=None,lineStart=None,assign=None)
    eng.P['new1'].update(state='ready',readyAt=540,assign={'line':'A','mode':'opening','post':4,
        'openingKey':line['posts'][4]['key'],'departAt':540})
    W.plan(eng,540)
    assert W.task_for(eng,eng.P['new1']) is None
    assert W.task_for(eng,eng.P['new0'])==first
    eng.act_depart('new0',540,'screen')
    eng.P['new1']['assign']={'line':'A','mode':'opening','post':4,
        'openingKey':line['posts'][4]['key'],'departAt':540}
    W.plan(eng,540)
    assert W.task_for(eng,eng.P['new1'])['moves'][0]['to']==line['posts'][0]['key']
    eng.act_depart('new1',540,'screen')
    assert occupants(eng,'A')==['new1','new0','old0','old1','old3']


def test_mixed_downshift_uses_prefix_then_remaining_ordinary_rotation():
    eng,_=fixture();eng.P['new0']['assign'].update(mode='chain',target='old0',why='推下班')
    W.plan(eng,538)
    eng.act_depart('new2',541,'screen');eng.act_depart('new0',541,'screen');eng.act_depart('new1',541,'screen')
    assert eng.P['old0']['state']=='done'
    assert occupants(eng,'A')[:3]==['new0','new2','new1']


def test_push_seven_direct_relief_can_share_a_round_with_ordinary_routes():
    eng,_=fixture();eng.P['new0']['assign'].update(mode='push7',target='old2')
    eng.P['old2']['flags']=['7点岗'];eng.P['new0']['flags']=['推7点']
    W.plan(eng,538)
    for pid in ['new1','new0','new2']:eng.act_depart(pid,541,'screen')
    assert occupants(eng,'A')==['new2','new1','new0','old0','old1']
    assert eng.P['old2']['sevenReplaced'] and eng.P['old2']['flags']==[] and eng.P['new0']['flags']==[]


def test_timeout_transfers_only_pending_route_with_fresh_deadline():
    eng,_=fixture(2)
    eng.P['new1'].update(assign=None,state='rest',readyAt=545)
    W.plan(eng,540);original=W.task_for(eng,eng.P['new0'])
    W.tick(eng,543.01)
    assert eng.P['new0']['entryHold']['task']==original['id']
    assert original['owner']=='new1' and original['allowedAt']==545 and original['deadlineAt']==548
    assert eng.P['new0']['taskTimeout']['transferredTo']=='new1'
    with pytest.raises(ActionError):eng.act_depart('new0',545,'screen')
    eng.P['new1']['state']='ready';eng.act_depart('new1',545,'screen')
    assert original['status']=='executed'


def test_timeout_acknowledgement_can_reclaim_waiting_task_without_another_break():
    eng,_=fixture(1);p=eng.P['new0'];W.plan(eng,540)
    original=W.task_for(eng,p);W.tick(eng,544)
    assert original['status']=='waiting_transfer' and original['owner'] is None
    W.tick(eng,545)
    assert original['owner'] is None  # The former owner must acknowledge first.
    p.update(ate=True,breakStart=500)
    W.acknowledge_timeout(eng,p,545);eng.tick(545)
    assert p['state']=='ready' and p['readyAt']==545
    assert p['ate'] and p['breakStart']==500
    assert W.task_for(eng,p)['id']==original['id']
    assert original['owner']==p['pid'] and original['allowedAt']==545 and original['deadlineAt']==548
    assert not original.get('transferredFrom') and p['taskTimeout']['transferredTo'] is None
    eng.act_depart(p['pid'],545,'screen')
    assert original['status']=='executed'


def test_timeout_acknowledgement_does_not_steal_transferred_task_and_gets_new_task():
    eng,_=fixture(2);p=eng.P['new0'];eng.P['new1'].update(assign=None,state='rest',readyAt=545)
    W.plan(eng,540);W.tick(eng,543.1)
    transferred=deepcopy(W.task_for(eng,eng.P['new1']))
    p.update(state='rest',readyAt=560)
    W.acknowledge_timeout(eng,p,544);eng.tick(544)
    assert p['state']=='ready' and p['readyAt']==544 and not p.get('entryHold')
    assert W.task_for(eng,p) and W.task_for(eng,p)['id']!=transferred['id']
    assert W.task_for(eng,p)['allowedAt']==544
    assert W.task_for(eng,eng.P['new1'])==transferred


def test_onpost_shift_end_waits_for_actual_replacement():
    eng,_=fixture(0);eng.P['old0']['end']=540
    eng.tick(541)
    assert eng.P['old0']['state']=='onpost' and occupants(eng,'A')[0]=='old0'
    assert W.continuous_minutes(eng.P['old0'],541)==31


def test_manual_hold_cancel_at_shift_end_resumes_replacement_without_clearing_post():
    eng,_=fixture(0);eng.P['old0']['end']=540
    eng.act_post_hold('A',0,'old0',True,'现场任务',539,'manager')
    eng.act_post_hold('A',0,'old0',False,'结束任务',541,'manager');eng.tick(541)
    assert eng.P['old0']['state']=='onpost' and occupants(eng,'A')[0]=='old0'


def test_drag_and_cross_line_swap_preserve_continuous_start():
    eng,_=fixture(0);start=eng.P['old0']['continuousStart']
    eng.act_move('old0','B',0,541,'manager')
    assert eng.P['old0']['continuousStart']==start
    eng.act_post_order('B',0,1,542,'manager',swap_mode='with_people',target_lid='A')
    assert eng.P['old0']['continuousStart']==start


def test_101_is_separate_from_manual_hold_and_cancel_keeps_existing_tasks():
    eng,_=fixture(1);eng.line('A')['posts'][0]['hold101Role']='tower'
    W.plan(eng,540);W.toggle_101(eng,True,540,'manager')
    task=W.task_for(eng,eng.P['new0']);assert task and eng.line('A')['posts'][0]['key'] not in task['expected']
    before=deepcopy(task)
    W.toggle_101(eng,False,540.5,'manager')
    assert W.task_for(eng,eng.P['new0'])==before


def test_seven_oclock_unseated_staff_have_initial_rest_without_early_timer():
    eng,_=fixture(0);p=person('early','07:00','16:00');p.update(state='notyet',flags=[],visited=[])
    eng.P['early']=p
    assert eng.opening_rest_candidate(p)


def test_cohort_uses_full_span_instead_of_chained_neighbours():
    eng,_=fixture(4)
    eng.P['new3']['readyAt']=541.5;eng.P['new3']['assign']['departAt']=541.5
    W.plan(eng,539)
    assert len({W.task_for(eng,eng.P['new'+str(i)])['batch'] for i in range(3)})==1
    assert W.task_for(eng,eng.P['new3']) is None  # overlapping posts cannot be reserved twice
    for i in range(3):eng.act_depart('new'+str(i),541,'screen')
    eng.P['new3']['assign']={'line':'A','mode':'push','departAt':541.5}
    W.plan(eng,541)
    assert W.task_for(eng,eng.P['new3'])['allowedAt']==541.5


def test_tasks_are_disjoint_and_tokens_survive_other_person_confirming_and_rename():
    from app.rotation.service import confirmation_token
    eng,_=fixture();W.plan(eng,538)
    token=confirmation_token(eng,eng.P['new0'],'depart')
    eng.act_depart('new1',541,'screen')
    task=W.task_for(eng,eng.P['new0']);_,_,post=W.locate(eng,task['moves'][0]['to'])
    post['name']='改名后的岗位'
    assert confirmation_token(eng,eng.P['new0'],'depart')==token
    assert W.task_view(eng,eng.P['new0'])['entryPost']=='改名后的岗位'
    eng.act_depart('new0',541,'screen')
    with pytest.raises(ActionError):eng.act_depart('new0',541,'screen')


def test_transfer_can_replace_early_displayed_plan_but_not_an_issued_task():
    eng,_=fixture(2);p=eng.P['new1']
    p.update(state='rest',readyAt=545,assign={'line':'B','mode':'push','departAt':545})
    W.plan(eng,540);task=W.task_for(eng,eng.P['new0']);W.tick(eng,543.1)
    assert task['owner']=='new1' and p['assign']['line']=='A'


def test_manual_refresh_keeps_deadline_and_changes_only_intersecting_tasks():
    from app.rotation.manual import apply
    eng,_=fixture();W.plan(eng,538)
    first=W.task_for(eng,eng.P['new0']);other=deepcopy(W.task_for(eng,eng.P['new1']))
    apply(eng,{'action':'move','pid':'old2','line':'B','i':0},540,'preview')
    refreshed=W.task_for(eng,eng.P['new0'])
    assert refreshed['revision']==first['revision']+1 and refreshed['deadlineAt']==first['deadlineAt']
    assert W.task_for(eng,eng.P['new1'])==other


def test_no_early_op_arrival_or_early_task_execution():
    eng,_=fixture(1);W.plan(eng,538)
    with pytest.raises(ActionError):eng.act_depart('new0',539,'screen')
    p=eng.P['new0'];p.update(role='op',state='notyet',start=550)
    with pytest.raises(ActionError):eng.act_arrive(p['pid'],540,'screen')


def test_independent_routes_can_execute_in_every_order():
    from itertools import permutations
    for order in permutations(range(3)):
        eng,_=fixture();W.plan(eng,538)
        for index in order:eng.act_depart('new'+str(index),541,'screen')
        assert occupants(eng,'A')==['new2','new1','new0','old0','old1']
        assert len(set(occupants(eng,'A')))==5


def test_cancelled_rest_and_cross_line_followups_restore_together():
    from app.rotation import history as H
    eng,_=fixture(1);before=H.snapshot(eng.d);W.plan(eng,540)
    eng.act_depart('new0',540,'screen')
    pushed=eng.P['old2'];pushed.update(state='meal',breakStart=541,readyAt=580,ate=True)
    # Simulate the dependency-scoped audit patch before departure and a later cross-line move.
    keys=[p['key'] for p in eng.line('A')['posts'][:3]]
    entry={'beforePeople':{pid:before['persons'][pid] for pid in ['new0','old0','old1','old2']},
           'beforePosts':{key:H._posts(before)[key] for key in keys},'beforeLines':{},'action':'depart'}
    undo={'entries':[(1,entry)],'resources':{'post:'+key for key in keys}|{'person:'+pid for pid in entry['beforePeople']},'minute':540}
    H.apply(eng,undo,550)
    assert occupants(eng,'A')==[f'old{i}' for i in range(5)]
    assert eng.P['old2']['state']=='onpost' and eng.P['old2']['continuousStart']==450
    assert eng.P['old2'].get('breakStart')!=541


def test_modern_400_person_projection_uses_issued_routes_without_legacy_simulations(monkeypatch):
    from app.rotation import cooperation as C
    from app.rotation.service import person_view
    def forbidden(*args,**kwargs):raise AssertionError('modern tasks must not replay the legacy engine per person')
    monkeypatch.setattr(C,'simulate',forbidden)
    eng,_=fixture(395);eng.tick(540)
    views=[person_view(eng,p,540,{}) for p in eng.P.values()]
    assert len(views)==400
    assert all(p['departureRoutes']==p['task']['routes'] for p in views if p['task'])

@pytest.mark.parametrize('break_state',['rest','meal'])
def test_long_break_forecast_does_not_publish_before_three_minute_window(break_state):
    eng,_=fixture(1)
    p=eng.P['new0'];p.update(state=break_state,readyAt=585,assign={'line':'A','mode':'push','departAt':585})
    assert not W.plan(eng,540)
    assert W.task_for(eng,p) is None
    assert p['state']==break_state and p['readyAt']==585
    assert not W.plan(eng,581.99)
    assert W.task_for(eng,p) is None
    assert W.plan(eng,582)
    task=W.task_for(eng,p)
    assert task['allowedAt']==585 and task['deadlineAt']==588
    assert p['state']==break_state and p['readyAt']==585
    with pytest.raises(ActionError):eng.act_depart(p['pid'],584.99,'screen')


@pytest.mark.parametrize('state,kind',[('rest','rest'),('meal','meal'),('rest','meal_rest'),('ready','rest')])
def test_unpublished_forecasts_never_become_personal_notice_routes(state,kind):
    from app.rotation.service import desired_notices
    eng,_=fixture(1);p=eng.P['new0']
    p.update(state=state,breakKind=kind,readyAt=585,
             assign={'line':'A','mode':'push','departAt':620})
    before=deepcopy(eng.d)
    notice=desired_notices(eng,540)[(p['pid'],'step')]
    assert notice[0]=='rest' and '正式任务' in notice[2]
    assert '10:20' not in str(notice) and '出发去 A' not in str(notice)
    assert eng.d==before  # Presentation must not change rest or the engine plan.


@pytest.mark.parametrize('minute,expected',[(585,'09:45'),(585.5,'09:45:30'),
    (585.75,'09:45:45'),(1439.999,'23:59:59'),(1440,'00:00'),(-.25,'23:59:45')])
def test_display_time_preserves_actual_clock_minute_and_fractional_seconds(minute,expected):
    from app.rotation.service import display_time
    assert display_time(minute)==expected


def test_published_fractional_deadline_is_not_rounded_to_another_minute():
    from app.rotation.service import desired_notices
    eng,_=fixture(1);p=eng.P['new0']
    p.update(state='rest',readyAt=540.75,assign={'line':'A','mode':'push','departAt':540.75})
    W.plan(eng,538)
    task=W.task_for(eng,p);assert task['allowedAt']==540.75
    notice=desired_notices(eng,538)[(p['pid'],'step')]
    assert '09:00:45 允许出发' in notice[2] and '09:03:45 前确认' in notice[2]
    with pytest.raises(ActionError):W.execute(eng,p,540.74,'screen')


def test_explicit_early_relief_target_survives_substitute_selection():
    eng,_=fixture(3)
    eng.d['plan'].update(seven={'A#0':'old0','A#1':'old1'},push7=['new0','new1'],
                         push7Targets={'new0':'old0','new1':'old1'})
    eng.P['new0'].update(state='excluded',role='excluded',assign=None)
    for pid in ['new1','new2']:
        eng.P[pid].update(state='ready',start=435,readyAt=540,assign=None)
    for pid in ['old0','old1']:eng.P[pid]['flags'].append('7点岗')
    eng._assign_all(540)
    assert eng.d['plan']['push7Targets']=={'new2':'old0','new1':'old1'}
    assert {a:b for _,a,b in eng._push7_pairs()}=={'new2':'old0','new1':'old1'}
