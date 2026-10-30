"""Narrow, dependency-scoped recovery for an unconfirmed task affecting its line.

Ordinary timeouts keep workflow.tick's existing transfer/acknowledgement rules.
Cases, previews and sequential pending demands live in the existing day JSON.
"""
from copy import deepcopy
import hashlib
import json
from uuid import uuid4
from app.rotation import workflow as W
from app.rotation.engine import Engine, ActionError

OPEN = {'open', 'handling'}


class PreviewHooks:
    def __init__(self, source): self.source = source; self.events = []
    def log(self, *args): self.events.append(args)
    def segment(self, *args): pass
    def week_minutes(self): return self.source.week_minutes()


def clone(eng):
    return Engine(deepcopy(eng.d), eng.S, PreviewHooks(eng.h))


def actor_key(actor):
    id=getattr(actor,'actor_id',None)
    return f"{getattr(actor,'kind','operator')}:{id if id is not None else getattr(actor,'name',str(actor))}"


def active(eng):
    return [c for c in eng.d.get('timeoutCases', {}).values() if c['status'] in OPEN]


def for_person(eng, pid):
    return next((c for c in active(eng) if pid in c['people']), None)


def reserved(eng):
    return {v[5:] for c in active(eng) for v in c['resources'] if v.startswith('post:')}


def protected_tasks(eng):
    return {r['task']['id'] for c in active(eng) for r in c['roots']} | {
        p.get('taskTimeout', {}).get('task') for p in eng.P.values()
        if p.get('taskTimeout') and not p['taskTimeout'].get('acknowledged')}


def nominal(eng, p, now):
    """Only called for imminent unmaterialized demands on an unresolved line."""
    a=p.get('assign') or {}
    if a.get('taskId') or a.get('queueId') or a.get('line') is None or p.get('entryHold'):
        return None
    at=max(now,p.get('readyAt') or now,a.get('departAt',now),a.get('notBefore',0))
    if at>now+3+W.EPS:return None
    # The route builder touches this line and its occupants only. Avoid copying
    # the entire day's hundreds of people/tasks on a normal scheduler tick.
    line=eng.line(a['line']);ids={post.get('occ') for post in line['posts']}|{p['pid']}
    state={**eng.d,'persons':{pid:deepcopy(eng.P[pid]) for pid in ids if pid in eng.P},
           'lines':[deepcopy(line)],'rotationTasks':{},'timeoutCases':{}}
    trial=Engine(state,eng.S,PreviewHooks(eng.h))
    q=trial.P[p['pid']];q['assign']=deepcopy(a);q.pop('_taskRefresh',None)
    try:
        W._build_batch(trial,trial.line(a['line']),[q],at,now)
        return W.task_for(trial,q)
    except (ActionError,KeyError,ValueError):return None


def discover(eng, now):
    """Before automatic transfer, find the transitive same-line impact set."""
    changed=False;tasks=eng.d.get('rotationTasks',{})
    roots=[]
    for p in eng.P.values():
        timeout=p.get('taskTimeout') or {}
        t=tasks.get(timeout.get('task'))
        if t and t['status'] in {'assigned','waiting_transfer','exception_hold','executed'} and not timeout.get('acknowledged') and not timeout.get('resolvedCase'):
            roots.append((p,t))
        elif not timeout or timeout.get('acknowledged') or timeout.get('resolvedCase'):
            t=W.task_for(eng,p)
            if t and t['status']=='assigned' and now>t['deadlineAt']+W.EPS:roots.append((p,t))
    nominal_cache={}
    for person,root in roots:
        case=next((c for c in active(eng) if c['line']==root['line']),None)
        scope=set(case['resources'] if case else W.resources(root))
        selected={t['id']:t for t in tasks.values() if t['id']!=root['id'] and t['line']==root['line']
                  and t['status'] in {'assigned','waiting_transfer','exception_hold'}}
        raw={}
        for p in eng.P.values():
            a=p.get('assign') or {}
            if not a:
                blocked=eng.d.get('timeoutBlockedNeeds',{}).get(p['pid'])
                if blocked and p.get('state') in {'ready','rest','meal'} and not p.get('entryHold'):
                    a=blocked['template']
            if p['pid']==person['pid'] or a.get('line')!=root['line'] or a.get('taskId') or a.get('queueId'):continue
            if p['pid'] not in nominal_cache:
                candidate={**p,'assign':a}
                nominal_cache[p['pid']]=nominal(eng,candidate,now)
            if nominal_cache[p['pid']]:raw[p['pid']]=nominal_cache[p['pid']]
        related={};pending={};expanded=True
        while expanded:
            expanded=False
            for id,t in selected.items():
                if id not in related and W.resources(t)&scope:
                    related[id]=t;scope.update(W.resources(t));expanded=True
            for pid,t in raw.items():
                if pid not in pending and W.resources(t)&scope:
                    pending[pid]=t;scope.update(W.resources(t));expanded=True
        if not related and not pending and not case:continue
        if not case:
            case={'id':uuid4().hex,'line':root['line'],'status':'open','at':now,'revision':0,
                  'roots':[],'tasks':{},'raw':{},'people':[],'resources':[],'claimedBy':None}
            eng.d.setdefault('timeoutCases',{})[case['id']]=case
        old=json.dumps(case,sort_keys=True)
        if not any(r['pid']==person['pid'] and r['task']['id']==root['id'] for r in case['roots']):
            original=deepcopy((person.get('taskTimeout') or {}).get('originalTask') or root)
            # A previously transferred ordinary timeout still belongs to the original person.
            original['moves'][0]['pid']=person['pid']
            case['roots'].append({'pid':person['pid'],'task':original})
        all_tasks={root['id']:root,**related}
        for id,t in all_tasks.items():
            case['tasks'].setdefault(id,deepcopy(t))
            newly_held=t['status'] not in {'exception_hold','executed'}
            if t['status']!='executed':t['status']='exception_hold'
            owner=eng.P.get(t.get('owner'))
            if owner:
                if newly_held:owner['confirmationSequence']=owner.get('confirmationSequence',0)+1
                if owner['pid'] not in case['people']:case['people'].append(owner['pid'])
        for pid,t in pending.items():
            case['raw'].setdefault(pid,{'pid':pid,'template':deepcopy(t['template']),
                                       'allowedAt':t['allowedAt'],'resources':sorted(W.resources(t))})
            # Materialize a blocked task so ordinary estimate refreshes cannot steal its owner.
            held=deepcopy(t);held.update(id=uuid4().hex,status='exception_hold',caseId=case['id'])
            tasks[held['id']]=held;case['tasks'][held['id']]=deepcopy(held)
            W.bind(eng,eng.P[pid],held)
            eng.P[pid]['confirmationSequence']=eng.P[pid].get('confirmationSequence',0)+1
            eng.d.get('timeoutBlockedNeeds',{}).pop(pid,None)
            if pid not in case['people']:case['people'].append(pid)
        if person['pid'] not in case['people']:case['people'].append(person['pid'])
        person['entryHold']={'reason':'同线关联任务待核实','at':case['at'],'task':root['id']}
        person.setdefault('taskTimeout',{'task':root['id'],'line':root['line'],'at':now,
            'entryKey':root['moves'][0]['to'],'mode':root['template']['mode'],'transferredTo':None,'acknowledged':False})
        case['resources']=sorted(scope)
        if json.dumps(case,sort_keys=True)!=old:
            case['revision']+=1;changed=True
            eng.h.log('timeout_case_open','system',person['pid'],root['line'],None,{'case':case['id'],'people':case['people']})
    return changed


def fingerprint(eng,case_id):
    c=get(eng,case_id);line=eng.line(c['line']);ids=set(c['people'])|{p.get('occ') for p in line['posts']}
    value={'date':eng.d['date'],'epoch':eng.d.get('confirmationEpoch'),'case':c,'line':line,
           'people':{pid:eng.P.get(pid) for pid in sorted(ids-{None})},
           'tasks':{id:t for id,t in eng.d.get('rotationTasks',{}).items()
                    if id in c['tasks'] or t['line']==c['line'] and W.resources(t)&set(c['resources'])},
           'demands':[d for d in eng.d.get('timeoutDemands',[]) if d['line']==c['line']]}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def plan_signature(eng,case_id):
    tasks=[{'owner':t.get('owner'),'moves':t['moves'],'expected':t['expected'],'mode':t['template']['mode']}
           for t in eng.d.get('rotationTasks',{}).values() if t.get('recoveryCase')==case_id and t['status']=='assigned']
    queue=[{'owner':d.get('owner'),'source':d.get('source'),'line':d['line'],'template':d['template']}
           for d in eng.d.get('timeoutDemands',[]) if d['case']==case_id]
    return hashlib.sha256(json.dumps({'tasks':sorted(tasks,key=lambda t:t['owner'] or ''),'queue':queue},sort_keys=True).encode()).hexdigest()


def view(eng):
    def suggestion(c,r):
        t=r['task'];positions={p['key']:p.get('occ') for p in eng.line(c['line'])['posts']}
        if any(positions.get(key)!=pid for key,pid in t['expected'].items()):return None
        for m in t['moves']:
            if m['from']:positions[m['from']]=None
        for m in t['moves']:
            if m['to']:positions[m['to']]=m['pid']
        return positions
    return [{'id':c['id'],'line':c['line'],'status':c['status'],'at':c['at'],'revision':c['revision'],
             'claimedBy':c.get('claimedBy'),'handler':c.get('handler'),
             'roots':[{'pid':r['pid'],'name':eng.P[r['pid']]['name'],'taskId':r['task']['id'],
                       'allowedAt':r['task']['allowedAt'],'deadlineAt':r['task']['deadlineAt'],
                       'suggestedPositions':suggestion(c,r)} for r in c['roots']],
             'people':[{'pid':pid,'name':eng.P[pid]['name']} for pid in c['people'] if pid in eng.P],
             'posts':[{'key':p['key'],'name':p['name'],'pid':p.get('occ'),'hold':W.held(p),
                       'affected':'post:'+p['key'] in c['resources']} for p in eng.line(c['line'])['posts']]} for c in active(eng)]


def get(eng,id):
    c=eng.d.get('timeoutCases',{}).get(id)
    if not c:raise ActionError('关联异常不存在，请刷新')
    return c


def claim(eng,id,actor,now,takeover=False,reason=''):
    c=get(eng,id)
    if c['status'] not in OPEN:raise ActionError('异常已处理')
    other=c.get('claimedBy') and c['claimedBy']!=actor_key(actor)
    if other and (takeover is not True or not reason.strip()):raise ActionError('已有其他人员接手，请联系处理人；接替处理须填写交接原因')
    if not c.get('claimedBy') or other:
        c.update(status='handling',claimedBy=actor_key(actor),handler=getattr(actor,'name',str(actor)),claimedAt=now,revision=c['revision']+1)
        eng.h.log('timeout_case_claim',actor,None,c['line'],None,{'case':id,'takeover':bool(other),'reason':reason})


def queue_view(eng,p):
    return next(({'id':d['id'],'line':d['line'],'status':'sequence_wait','reason':'等待前序完成／人员匹配，暂不计确认超时',
                  'mode':d['template']['mode'],'transferredFromName':eng.P.get(d.get('source'),{}).get('name')}
                 for d in eng.d.get('timeoutDemands',[]) if d.get('owner')==p['pid']),None)


def can_receive(eng,p,template,now,case_id=None):
    if p.get('entryHold') or p.get('state') not in {'ready','rest','meal'} or p.get('role') not in {'rotation','op'}:return None
    if p.get('assign') or for_person(eng,p['pid']) and for_person(eng,p['pid'])['id']!=case_id:return None
    at=max(now,p.get('readyAt') or now,template.get('notBefore',0))
    return at if at<=now+3+W.EPS and eng.can_board(p,at,now) else None


def fill(eng,now):
    changed=False;queue=eng.d.get('timeoutDemands',[])
    for demand in list(queue):
        previous=eng.d.get('rotationTasks',{}).get(demand.get('after'))
        if previous and previous['status']!='executed':
            if previous['status'] in W.ACTIVE|{'exception_hold'}:continue
            owner=eng.P.get(previous.get('owner'))
            replacement=W.task_for(eng,owner) if owner else None
            if replacement and replacement['status']=='assigned':
                demand['after']=replacement['id'];continue
            # A manual change cancelled the head after recovery. Retain that
            # demand instead of leaving its successors stuck behind a dead ID.
            id=previous.get('requeuedDemand')
            if not id or not any(d['id']==id for d in queue):
                id=uuid4().hex
                queue.insert(0,{'id':id,'case':demand['case'],'line':demand['line'],
                    'template':deepcopy(previous['template']),'source':previous.get('transferredFrom') or previous.get('owner'),
                    'owner':None,'afterDemand':None,'createdAt':now})
                previous['requeuedDemand']=id;changed=True
            demand.update(after=None,afterDemand=id);continue
        if demand.get('afterDemand') and any(d['id']==demand['afterDemand'] for d in queue):continue
        if any(c['line']==demand['line'] for c in active(eng)):continue
        template=demand['template'];fulfilled=False
        if template['mode'] in {'chain','push7'}:
            target=eng.P.get(template.get('target'))
            fulfilled=not target or target.get('state')!='onpost' or target.get('line')!=demand['line']
        elif template['mode']=='opening':
            _,_,post=W.locate(eng,template.get('openingKey'))
            fulfilled=bool(post and post.get('occ'))
        if fulfilled:
            owner=eng.P.get(demand.get('owner'))
            if owner and (owner.get('assign') or {}).get('queueId')==demand['id']:owner['assign']=None
            for other in queue:
                if other.get('afterDemand')==demand['id']:other.update(after=demand.get('after'),afterDemand=None)
            queue.remove(demand);changed=True
            eng.h.log('timeout_demand_fulfilled','system',None,demand['line'],None,{'case':demand['case'],'source':demand.get('source')})
            continue
        owner=eng.P.get(demand.get('owner'))
        if owner and (owner.get('assign') or {}).get('queueId')==demand['id']:
            owner['assign']=None
        at=can_receive(eng,owner,demand['template'],now) if owner else None
        if owner and at is None:
            demand['owner']=None;owner=None
        if owner is None:
            candidates=[(at,p['start'],p['pid'],p) for p in eng.P.values()
                        if (at:=can_receive(eng,p,demand['template'],now)) is not None]
            if not candidates:continue
            at,_,_,owner=min(candidates,key=lambda x:x[:3]);demand['owner']=owner['pid']
        owner['assign']={**deepcopy(demand['template']),'line':demand['line'],'departAt':at}
        owner.pop('_taskRefresh',None)
        W._build_batch(eng,eng.line(demand['line']),[owner],at,now)
        task=W.task_for(eng,owner)
        if not task:
            owner['assign']={**deepcopy(demand['template']),'line':demand['line'],'departAt':at,'queueId':demand['id'],'blocked':'等待相关岗位可用'}
            continue
        task['transferredFrom']=demand.get('source') if demand.get('source')!=owner['pid'] else None
        task['recoveryCase']=demand['case']
        source=eng.P.get(demand.get('source'))
        if source and source.get('taskTimeout',{}).get('resolvedCase')==demand['case']:source['taskTimeout']['transferredTo']=owner['pid']
        if task['template']['mode']=='push7':
            if source:source['flags']=[f for f in source.get('flags',[]) if f!='推7点']
            if '推7点' not in owner['flags']:owner['flags'].append('推7点')
        for other in queue:
            if other.get('afterDemand')==demand['id']:other.update(after=task['id'],afterDemand=None)
        for pid in task.get('previousOwners',[]):
            if pid in eng.P:eng.P[pid].get('taskTimeout',{})['transferredTo']=owner['pid']
        eng.h.log('timeout_case_reassign','system',owner['pid'],task['line'],None,{'case':demand['case'],'from':demand.get('source'),'task':task['id']})
        queue.remove(demand);changed=True
    return changed


def resolve(eng,id,body,actor,now,preview=False):
    """Used unchanged by preview clone and transactional commit."""
    c=get(eng,id)
    digest=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
    if c['status'] not in OPEN:
        if c.get('submissionHash')==digest and c.get('resolvedByKey')==actor_key(actor):return {'case':id,'status':'resolved','duplicate':True,'waitingMatch':c.get('waitingMatch',0)}
        raise ActionError('异常已经处理，请刷新')
    if c.get('claimedBy')!=actor_key(actor):raise ActionError('请先接手处理本异常')
    if body.get('positions_verified') is not True:raise ActionError('请先核实现场当前站位')
    if not preview and body.get('notified') is not True:raise ActionError('请先通知可能在途的受影响人员')
    decisions=body.get('decisions')
    if not isinstance(decisions,dict) or set(decisions)!={r['pid'] for r in c['roots']}:raise ActionError('请逐一确认超时人员是否实际执行')
    line=eng.line(c['line']);positions=body.get('positions')
    if not isinstance(positions,dict) or set(positions)!={p['key'] for p in line['posts']}:raise ActionError('请核对本线全部当前站位')
    occupied=[pid for pid in positions.values() if pid is not None]
    if len(occupied)!=len(set(occupied)) or any(pid not in eng.P for pid in occupied):raise ActionError('现场人员无效或同一人重复占岗')
    for post in line['posts']:
        pid=positions[post['key']]
        if pid!=post.get('occ') and 'post:'+post['key'] not in c['resources']:raise ActionError('无关联岗位不在本次纠正范围，请使用原现场调整流程')
        if pid!=post.get('occ') and (W.held(post) or not post.get('open') or not line.get('active')):raise ActionError('定岗或已关闭岗位有变化，请先按原现场调整流程核对')
        if pid:
            other,_=eng.find_post(pid)
            if other and other['id']!=line['id']:raise ActionError('涉及跨线站位变化，请先核实现场调整')
            a=eng.P[pid].get('assign') or {}
            if a and a.get('taskId') not in c['tasks'] and pid not in {r['pid'] for r in c['roots']}:
                raise ActionError('此人另有未关联任务，请先核对，不可静默覆盖')
    times={};roots={r['pid']:r for r in c['roots']}
    for pid,r in roots.items():
        decision=decisions[pid]
        other,_=eng.find_post(pid)
        if other and other['id']!=line['id']:raise ActionError('超时人员现位于其他线路，请先核对现场')
        if not isinstance(decision,dict) or decision.get('result') not in {'executed','absent'}:raise ActionError('请选择已执行或未执行')
        at=decision.get('actualAt')
        if at is not None and (type(at) not in (int,float) or not r['task']['allowedAt']<=at<=now):raise ActionError('实际执行时间须介于允许出发与当前时间之间')
        if decision['result']=='absent' and pid in occupied:raise ActionError('未执行人员不能同时出现在现场岗位')
        if decision['result']=='executed' and pid not in occupied and decision.get('offpost') not in {'walkback','done','away'}:raise ActionError('已执行人员现已离岗，请确认其当前去向')
        times[pid]=at
    # Determine pending demands before correcting the actual station map.
    root_ids={r['task']['id'] for r in c['roots']}
    demands=[]
    for r in c['roots']:
        transferred_done=eng.d['rotationTasks'][r['task']['id']]['status']=='executed'
        if decisions[r['pid']]['result']=='absent' and not transferred_done:demands.append({'source':r['pid'],'template':deepcopy(r['task']['template']),'at':r['task']['allowedAt']})
    dependent=[t for key,t in c['tasks'].items() if key not in root_ids]
    observed_complete={t['id'] for t in dependent if t.get('owner') in occupied}
    for t in sorted(dependent,key=lambda t:(t['allowedAt'],t['id'])):
        if t['id'] in observed_complete:continue
        # A root may already have transferred to an owner: do not duplicate its demand.
        if t.get('owner') in roots:continue
        demands.append({'source':t.get('owner'),'template':deepcopy(t['template']),'at':t['allowedAt']})
    candidates=[]
    for t in sorted(c['tasks'].values(),key=lambda t:(t['allowedAt'],t['id'])):
        pid=t.get('owner')
        if pid and pid not in roots and pid not in candidates:candidates.append(pid)
    for pid in c['people']:
        if pid not in roots and pid not in candidates:candidates.append(pid)
    # Invalidate original credentials before any queued person can execute again.
    for t in eng.d['rotationTasks'].values():
        if t['id'] in c['tasks'] and t['status']=='exception_hold':
            t.update(status='executed' if t['id'] in observed_complete else 'superseded',resolvedCase=id)
            if t['id'] in observed_complete:t.update(executedAt=now,actualExecutedAt=None,verifiedBy=getattr(actor,'name',str(actor)))
    for pid in c['people']:
        p=eng.P[pid]
        if (p.get('assign') or {}).get('taskId') in c['tasks']:p['assign']=None
        p.pop('_taskRefresh',None);p['confirmationSequence']=p.get('confirmationSequence',0)+1
    # A precisely verified single original route may use its actual time. For
    # changed/later station histories, reconcile at verification time instead.
    correction_at=now
    if len(roots)==1:
        only=next(iter(roots.values()));pid=only['pid'];t=only['task']
        current_positions={p['key']:p.get('occ') for p in line['posts']}
        expected_after=dict(current_positions)
        for m in t['moves']:
            if m['from']:expected_after[m['from']]=None
        for m in t['moves']:
            if m['to']:expected_after[m['to']]=m['pid']
        if decisions[pid]['result']=='executed' and times[pid] is not None and positions==expected_after and all(current_positions.get(k)==v for k,v in t['expected'].items()):
            correction_at=times[pid]
    # Reconcile current truth, not a replay of the old route. Preserve later confirmed states.
    current={p['occ']:i for i,p in enumerate(line['posts']) if p.get('occ')}
    root_executed={pid for pid,d in decisions.items() if d['result']=='executed'}
    for pid,index in current.items():
        if positions[line['posts'][index]['key']]==pid:continue
        p=eng.P[pid]
        if pid in occupied:
            eng._leave_line(p,line,index,correction_at)
        else:
            move=next((m for r in c['roots'] for m in r['task']['moves'] if m['pid']==pid and not m['to']),None)
            if move and move.get('outcome') in {'done','away'}:eng._remove_leaving(pid,line,index,correction_at,'异常核实后确认离岗')
            else:eng._push_off(pid,line,index,correction_at,'异常核实后确认下岗')
        line['posts'][index]['occ']=None
    for post in line['posts']:
        pid=positions[post['key']]
        if post.get('occ')==pid:continue
        post.update(occ=pid,since=correction_at if pid else None)
        if pid:
            p=eng.P[pid]
            if pid in root_executed:
                p.pop('entryHold',None);p['state']='ready';p['continuousStart']=None
            eng._enter(p,line,correction_at)
            if correction_at==now and any(d['result']=='executed' and times[root] is None for root,d in decisions.items()):p['timingUnverified']=True
    for pid,r in roots.items():
        p=eng.P[pid];decision=decisions[pid];result=decision['result']
        p.setdefault('taskTimeout',{}).update(resolvedCase=id,acknowledged=result=='executed',acknowledgedAt=now if result=='executed' else None)
        original=eng.d['rotationTasks'][r['task']['id']]
        already_executed=original['status']=='executed' and original.get('owner')!=pid
        if result=='executed':
            p.pop('entryHold',None);p['assign']=None
            if not already_executed:original.update(status='executed',executedAt=times[pid] if times[pid] is not None else now,
                            actualExecutedAt=times[pid],verifiedAt=now,verifiedBy=getattr(actor,'name',str(actor)))
            else:r['supplement']={'pid':pid,'actualAt':times[pid],'verifiedAt':now,'by':getattr(actor,'name',str(actor))}
            p['timingUnverified']=times[pid] is None
            if pid in occupied and times[pid] is not None and pid not in current:
                p.update(continuousStart=times[pid],lineStart=times[pid],departedAt=times[pid])
                next(post for post in line['posts'] if post.get('occ')==pid)['since']=times[pid]
            elif pid not in occupied:
                p.update(state=decision['offpost'],line=None,lineStart=None,continuousStart=None)
                if decision['offpost']=='walkback':p.update(walkbackSince=now,after=eng._arrive_outcome(p,now)[0],arriveAnchor=None)
                if decision['offpost']=='away':p['away']={'reason':'现场核实已出圈','until':None}
            eng.h.log('timeout_case_supplement',actor,pid,line['id'],None,{'case':id,'actualAt':times[pid],'verifiedAt':now})
        else:
            if not already_executed:original.update(status='superseded',resolvedCase=id)
            if decision.get('returned') is True:W.acknowledge_timeout(eng,p,now)
            else:p['entryHold']={'reason':'原任务已顺接，待本人返回确认','at':now,'task':r['task']['id']}
    c.update(status='resolved',resolvedAt=now,resolvedBy=getattr(actor,'name',str(actor)),revision=c['revision']+1,
             decisions=deepcopy(decisions),positions=deepcopy(positions),submissionHash=digest,resolvedByKey=actor_key(actor))
    queue=eng.d.setdefault('timeoutDemands',[]);previous=None
    for demand in demands:
        template=demand['template'];template.pop('taskId',None);template.pop('blocked',None)
        # Target-specific requests already fulfilled by current reality do not need a second push.
        if template['mode'] in {'chain','push7'} and (not eng.P.get(template.get('target')) or eng.P[template['target']].get('state')!='onpost'):continue
        if template['mode']=='opening':
            _,_,post=W.locate(eng,template.get('openingKey'))
            if post and post.get('occ'):continue
        owner=None
        while candidates:
            pid=candidates.pop(0)
            if can_receive(eng,eng.P[pid],template,now,id) is not None:owner=eng.P[pid];break
        if owner is None:
            pool=[(at,p['start'],p['pid'],p) for p in eng.P.values() if p['pid'] not in roots
                  and (at:=can_receive(eng,p,template,now,id)) is not None]
            if pool:owner=min(pool,key=lambda x:x[:3])[3]
        record={'id':uuid4().hex,'case':id,'line':line['id'],'template':template,'source':demand['source'],
                'owner':owner['pid'] if owner else None,'afterDemand':previous,'createdAt':now}
        queue.append(record);previous=record['id']
        if owner:owner['assign']={**deepcopy(template),'line':line['id'],'departAt':max(now,owner.get('readyAt') or now),'queueId':record['id'],'blocked':'前序待确认，暂不计超时'}
    fill(eng,now)
    c['waitingMatch']=sum(d['case']==id and not d.get('owner') for d in queue)
    eng.h.log('timeout_case_resolved',actor,None,line['id'],None,{'case':id,'people':c['people'],'waitingMatch':c['waitingMatch']})
    return {'case':id,'status':'resolved','waitingMatch':c['waitingMatch']}
