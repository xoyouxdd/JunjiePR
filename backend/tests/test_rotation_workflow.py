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


def test_one_person_relief_stops_at_longest_middle_post():
    eng,_=fixture(1);eng.P['old1']['continuousStart']=400
    W.plan(eng,540);eng.act_depart('new0',540,'screen')
    assert occupants(eng,'A')==['new0','old0','old2','old3','old4']
    assert eng.P['old1']['state']=='walkback'


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
