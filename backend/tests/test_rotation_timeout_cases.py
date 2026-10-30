"""Only same-line dependent timeouts enter the special recovery branch."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from app.rotation import workflow as W, timeout_cases as C
from app.rotation.engine import ActionError
from test_rotation_workflow import fixture

ACTOR=SimpleNamespace(kind='supervisor',name='值班主管',actor_id=1)
OTHER=SimpleNamespace(kind='supervisor',name='另一主管',actor_id=2)


def case_fixture():
    eng,hooks=fixture(3)
    eng.P['new1']['assign']=None;eng.P['new2']['assign']=None
    W.plan(eng,540)
    original=W.task_for(eng,eng.P['new0'])
    eng.P['new1'].update(readyAt=544,state='ready',assign={'line':'A','mode':'chain','target':'old0','departAt':544,'why':'推下班'})
    eng.P['new2'].update(readyAt=544,state='ready',assign={'line':'B','mode':'push','departAt':544})
    W.plan(eng,541)
    W.tick(eng,544)
    case=C.active(eng)[0]
    return eng,hooks,case,deepcopy(original)


def payload(eng,c,executed=False):
    positions={p['key']:p.get('occ') for p in eng.line('A')['posts']}
    if executed:
        t=c['roots'][0]['task']
        for m in t['moves']:
            if m['from']:positions[m['from']]=None
        for m in t['moves']:
            if m['to']:positions[m['to']]=m['pid']
    return {'case_id':c['id'],'positions':positions,'decisions':{'new0':{'result':'executed' if executed else 'absent','actualAt':540 if executed else None}},
            'positions_verified':True,'notified':True}


def test_unrelated_timeout_still_transfers_and_can_acknowledge():
    eng,_=fixture(1);W.plan(eng,540);W.tick(eng,544)
    assert not C.active(eng)
    W.acknowledge_timeout(eng,eng.P['new0'],544);W.tick(eng,544)
    assert W.task_for(eng,eng.P['new0'])['status']=='assigned'


def test_only_dependent_same_line_tasks_are_held_and_stable_on_refresh():
    eng,h,c,original=case_fixture()
    assert eng.d['rotationTasks'][original['id']]['status']=='exception_hold'
    assert W.task_for(eng,eng.P['new1'])['status']=='exception_hold'
    other=W.task_for(eng,eng.P['new2']);assert other and other['line']=='B' and other['status']=='assigned'
    version=c['revision'];seq=eng.P['new1']['confirmationSequence']
    W.tick(eng,545)
    assert c['revision']==version and eng.P['new1']['confirmationSequence']==seq
    with pytest.raises(ActionError):W.execute(eng,eng.P['new1'],545,'screen')


def test_supplemented_execution_is_not_replayed_and_duplicate_is_idempotent():
    eng,h,c,_=case_fixture();C.claim(eng,c['id'],ACTOR,544)
    data=payload(eng,c,True);C.resolve(eng,c['id'],data,ACTOR,544)
    assert eng.P['new0']['state']=='onpost' and not eng.P['new0'].get('entryHold')
    assert c['roots'][0]['task']['id'] in eng.d['rotationTasks']
    assert eng.d['rotationTasks'][c['roots'][0]['task']['id']]['status']=='executed'
    before=deepcopy(eng.d);out=C.resolve(eng,c['id'],data,ACTOR,544)
    assert out['duplicate'] and eng.d==before
    assert not C.active(eng)
    assert W.task_for(eng,eng.P['new1'])['allowedAt']>=544
    assert eng.P['old0']['continuousStart']==510


def test_absent_shifts_task_to_next_owner_and_preserves_other_line():
    eng,h,c,original=case_fixture();unrelated=deepcopy(W.task_for(eng,eng.P['new2']))
    C.claim(eng,c['id'],ACTOR,544);C.resolve(eng,c['id'],payload(eng,c),ACTOR,544)
    task=W.task_for(eng,eng.P['new1'])
    assert task['transferredFrom']=='new0' and task['deadlineAt']==task['allowedAt']+3
    assert eng.P['new0'].get('entryHold') and not eng.P['new0'].get('assign')
    assert W.task_for(eng,eng.P['new2'])==unrelated
    assert any(d['owner'] is None for d in eng.d['timeoutDemands'])
    W.execute(eng,eng.P['new1'],544,'screen')
    # A target-specific demand already satisfied by the first push must not be run twice.
    assert eng.P['new1']['state']=='onpost'


def test_claim_and_station_verification_are_required():
    eng,h,c,_=case_fixture();data=payload(eng,c)
    with pytest.raises(ActionError):C.resolve(eng,c['id'],data,ACTOR,544)
    C.claim(eng,c['id'],ACTOR,544)
    with pytest.raises(ActionError):C.claim(eng,c['id'],OTHER,544)
    with pytest.raises(ActionError):C.resolve(eng,c['id'],{**data,'notified':False},ACTOR,544)
    same=next(iter(data['positions'].values()));bad={k:same for k in data['positions']}
    with pytest.raises(ActionError):C.resolve(eng,c['id'],{**data,'positions':bad},ACTOR,544)


def test_new_dependency_after_ordinary_timeout_is_promoted():
    eng,_=fixture(2);eng.P['new1']['assign']=None;eng.P['new1']['state']='away'
    W.plan(eng,540);W.tick(eng,544);assert not C.active(eng)
    eng.P['new1'].update(state='ready',readyAt=545,assign={'line':'A','mode':'chain','target':'old0','departAt':545})
    W.plan(eng,545)
    assert C.active(eng) and W.task_for(eng,eng.P['new1'])['status']=='exception_hold'


def test_same_line_disjoint_tasks_do_not_enter_special_case():
    eng,_=fixture(3);W.plan(eng,538)
    first=W.task_for(eng,eng.P['new0']);first.update(allowedAt=538,deadlineAt=541)
    eng.P['new0']['assign']['departAt']=538
    others=[deepcopy(W.task_for(eng,eng.P['new'+str(i)])) for i in (1,2)]
    W.tick(eng,542)
    assert not C.active(eng)
    assert [W.task_for(eng,eng.P['new'+str(i)]) for i in (1,2)]==others


def test_later_real_station_result_is_preserved_without_repeating_task():
    eng,h,c,_=case_fixture();C.claim(eng,c['id'],ACTOR,544)
    data=payload(eng,c,True)
    # Both newcomers have actually arrived; reconcile current truth, not a replay.
    keys=[p['key'] for p in eng.line('A')['posts']]
    data['positions']=dict(zip(keys,['new1','new0','old1','old3','old4']))
    C.resolve(eng,c['id'],data,ACTOR,544)
    assert [p.get('occ') for p in eng.line('A')['posts']]==['new1','new0','old1','old3','old4']
    assert eng.P['new1']['state']=='onpost' and not eng.P['new1']['assign']
    assert not eng.d['timeoutDemands']


def test_missing_pool_waits_without_stealing_another_task_or_shortening_rest():
    eng,h,c,_=case_fixture();C.claim(eng,c['id'],ACTOR,544)
    eng.P['new2']['readyAt']=550
    C.resolve(eng,c['id'],payload(eng,c),ACTOR,544)
    assert C.get(eng,c['id'])['waitingMatch']>=1
    W.execute(eng,eng.P['new1'],544,'screen');W.tick(eng,544)
    assert eng.P['new2']['readyAt']==550


def test_takeover_invalidates_prior_handler_and_unrelated_line_does_not_change_snapshot():
    eng,h,c,_=case_fixture();C.claim(eng,c['id'],ACTOR,544)
    before=C.fingerprint(eng,c['id']);eng.line('B')['posts'][0]['name']='改名'
    assert C.fingerprint(eng,c['id'])==before
    C.claim(eng,c['id'],OTHER,544,True,'原处理人暂离，现场交接')
    assert C.fingerprint(eng,c['id'])!=before
    with pytest.raises(ActionError):C.resolve(eng,c['id'],payload(eng,c),ACTOR,544)


def test_late_dependency_keeps_already_executed_transfer_and_does_not_repeat_it():
    eng,_=fixture(3);eng.P['new1']['assign']=None;eng.P['new2'].update(assign=None,state='away')
    W.plan(eng,540);W.tick(eng,544)
    task=W.task_for(eng,eng.P['new1']);assert task and not C.active(eng)
    W.execute(eng,eng.P['new1'],544,'screen');completed=deepcopy(task)
    eng.P['new2'].update(state='ready',readyAt=545,assign={'line':'A','mode':'push','departAt':545})
    W.plan(eng,545);c=C.active(eng)[0]
    assert eng.d['rotationTasks'][task['id']]==completed
    C.claim(eng,c['id'],ACTOR,545)
    C.resolve(eng,c['id'],payload(eng,c),ACTOR,545)
    assert eng.d['rotationTasks'][task['id']]==completed
    assert eng.P['new1']['state']=='onpost'
    assert W.task_for(eng,eng.P['new2'])['status']=='assigned'
