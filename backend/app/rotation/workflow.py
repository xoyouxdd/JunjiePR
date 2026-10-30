"""Versioned personal rotation tasks, planned together and executed independently.

Only ordinary JSON is stored in the day. Stable post identities survive names,
reordering and cross-line swaps; previews and writes use the same route builder.
"""
from copy import deepcopy
from uuid import uuid4

EPS = 1e-8
ACTIVE = {'assigned', 'waiting_transfer'}


def enabled(eng):
    return eng.d.get('taskWorkflow') == 1


def initialize(eng):
    changed = eng.d.get('taskWorkflow') != 1
    eng.d['taskWorkflow'] = 1
    eng.d['cooperativeEntry'] = False
    eng.d.setdefault('rotationTasks', {})
    for line in eng.d['lines']:
        for post in line['posts']:
            if not post.get('key'):
                post['key'] = uuid4().hex
                changed = True
            if 'hold101Role' not in post:
                name = post['name'].replace(' ', '')
                post['hold101Role'] = ('tower' if name == '塔台' else
                                       'tower_assist' if name in ('塔协', '塔台协助') else None)
                changed = True
    for p in eng.P.values():
        if p.get('state') == 'onpost' and p.get('continuousStart') is None:
            p['continuousStart'] = p.get('lineStart')
            changed = True
    if changed:
        for receipt in eng.d.get('confirmations', {}).values():
            if receipt.get('status') == 'waiting':
                receipt['status'] = 'superseded'
    return changed


def held(post):
    return bool(post.get('hold') or post.get('hold101'))


def locate(eng, key):
    for line in eng.d['lines']:
        for index, post in enumerate(line['posts']):
            if post.get('key') == key:
                return line, index, post
    return None, None, None


def task_for(eng, p):
    return eng.d.get('rotationTasks', {}).get((p.get('assign') or {}).get('taskId'))


def continuous_minutes(p, now):
    start = p.get('continuousStart')
    if start is None:
        start = p.get('lineStart')
    return max(0, now-start) if p.get('state') == 'onpost' and start is not None else 0


def finish_continuous(p, now):
    if p.get('continuousStart') is not None:
        p['lastContinuousMinutes'] = max(0, now-p['continuousStart'])
        p['continuousStart'] = None


def resources(task):
    return {'post:'+key for key in task.get('expected', {})} | {
        'person:'+move['pid'] for move in task.get('moves', [])}


def valid(eng, task):
    if task.get('status')=='exception_hold':return False
    owner=eng.P.get(task.get('owner'))
    if task.get('status')=='assigned' and (not owner or owner.get('state') not in ('ready','rest','meal') or owner.get('entryHold')):
        return False
    for key, expected in task['expected'].items():
        line, _, post = locate(eng, key)
        if not post or line['id'] != task['line'] or not line.get('active') or not available_at(post,task['allowedAt']) or held(post):
            return False
        if post.get('occ') != expected:
            return False
    return True


def available_at(post, at):
    from app.rotation.engine import _m
    if post.get('closeAt') and _m(post['closeAt'])<=at+EPS:return False
    return bool(post.get('open') or post.get('openAt') and not post.get('opened')
                and not post.get('manual') and _m(post['openAt'])<=at+EPS)


def route_view(eng, task):
    def label(key, fallback):
        _, _, post = locate(eng, key) if key else (None, None, None)
        return post['name'] if post else fallback
    return [{'pid': m['pid'], 'name': eng.P[m['pid']]['name'],
             'from': label(m['from'], '待上岗'),
             'to': label(m['to'], {'rest':'下岗，回休息室确认休息／吃饭', 'done':'下班', 'away':'出圈'}.get(m.get('outcome'), '下岗'))}
            for m in task['moves']]


def task_view(eng, p):
    task = task_for(eng, p)
    if not task:
        return None
    _, _, entry = locate(eng, task['moves'][0]['to'])
    line=eng.line(task['line'])
    before=[{'key':post['key'],'name':post['name'],'occ':post.get('occ'),
             'occName':eng.P.get(post.get('occ'),{}).get('name'),'open':post.get('open'),
             'hold101':bool(post.get('hold101')),'hold':bool(held(post)),
             'continuousStart':eng.P.get(post.get('occ'),{}).get('continuousStart')} for post in line['posts']]
    after=deepcopy(before)
    for move in task['moves']:
        for post in after:
            if post['key']==move['from']:
                post.update(occ=None,occName=None,continuousStart=None)
    for move in task['moves']:
        for post in after:
            if post['key']==move['to']:
                person=eng.P[move['pid']]
                post.update(occ=person['pid'],occName=person['name'],continuousStart=person.get('continuousStart'))
    return {'id':task['id'], 'revision':task['revision'], 'batch':task['batch'],
            'batchSize':task['batchSize'], 'line':line['id'] if line else task['line'],
            'entryPost':entry['name'] if entry else None, 'allowedAt':task['allowedAt'],
            'deadlineAt':task['deadlineAt'], 'status':task['status'],
            'transferredFrom':task.get('transferredFrom'),
            'transferredFromName':eng.P.get(task.get('transferredFrom'),{}).get('name'),
            'routes':route_view(eng, task), 'mode':task['template']['mode'], 'beforePosts':before,'afterPosts':after}


def bind(eng, p, task):
    p['assign'] = {**deepcopy(task['template']), 'line':task['line'],
                   'departAt':task['allowedAt'], 'taskId':task['id']}


def replan(eng, now, lines=None, reason='现场调整', affected=None):
    """Invalidate only affected pending routes; preserve their original deadline."""
    changed = False
    for task in list(eng.d.get('rotationTasks', {}).values()):
        if task['status'] not in ACTIVE or lines is not None and task['line'] not in lines:
            continue
        if affected is not None and not resources(task).intersection(affected):
            continue
        if lines is None and valid(eng, task):
            continue
        p = eng.P.get(task.get('owner'))
        task['status'] = 'superseded'
        if p and p.get('state') in ('ready','rest','meal') and (p.get('assign') or {}).get('taskId') == task['id']:
            p['assign'] = deepcopy(task['template'])
            p['_taskRefresh'] = {'id':task['id'], 'revision':task['revision']+1,
                                 'allowedAt':task['allowedAt'], 'deadlineAt':task['deadlineAt']}
            p['confirmationSequence'] = p.get('confirmationSequence', 0)+1
        eng.h.log('task_refresh', 'system', task.get('owner'), task['line'], None,
                  {'task':task['id'], 'reason':reason})
        changed = True
    return changed


def acknowledge_timeout(eng, p, now):
    """Release the timeout hold without starting another rest/meal period."""
    p.pop('entryHold', None)
    p['taskTimeout'].update(acknowledged=True, acknowledgedAt=now)
    p.update(state='ready', readyAt=now, readySince=now, breakKind='rest', assign=None)
    p.pop('_taskRefresh', None)


def tick(eng, now):
    from app.rotation import timeout_cases as C
    changed = C.discover(eng,now)
    protected=C.protected_tasks(eng)
    closed=sorted([t for t in eng.d.get('rotationTasks',{}).values() if t['status'] not in ACTIVE|{'exception_hold'} and t['id'] not in protected],
                  key=lambda t:t.get('executedAt',t.get('allowedAt',0)),reverse=True)
    for task in closed[100:]:
        eng.d['rotationTasks'].pop(task['id'],None)
    for task in list(eng.d.get('rotationTasks', {}).values()):
        if task['status'] == 'assigned' and now > task['deadlineAt']+EPS:
            p = eng.P.get(task['owner'])
            if p and (p.get('assign') or {}).get('taskId') == task['id']:
                p['assign'] = None
                p['entryHold'] = {'reason':'任务超时，请确认知晓后重新领取', 'at':now, 'task':task['id']}
                p['taskTimeout'] = {'task':task['id'], 'line':task['line'], 'at':now,
                                    'originalTask':deepcopy(task),
                                    'entryKey':task['moves'][0]['to'],'mode':task['template']['mode'],
                                    'transferredTo':None, 'acknowledged':False}
                task.setdefault('previousOwners', []).append(p['pid'])
                task['transferredFrom'] = p['pid']
                task['owner'] = None
                task['status'] = 'waiting_transfer'
                eng.h.log('task_timeout', 'system', p['pid'], task['line'], None, {'task':task['id']})
                changed = True
        if task['status'] == 'waiting_transfer' and valid(eng, task):
            candidates = []
            for p in eng.P.values():
                if p.get('entryHold') or (p.get('assign') or {}).get('taskId') or p.get('state') not in ('ready','rest','meal') or p.get('role') not in ('rotation','op'):
                    continue
                acknowledgement=p.get('taskTimeout') or {}
                if p['pid'] in task.get('previousOwners',[]) and not (acknowledgement.get('task')==task['id'] and acknowledgement.get('acknowledged')):
                    continue
                at = max(now, p.get('readyAt') or now, task['template'].get('notBefore', 0))
                if at > now+3+EPS or not eng.can_board(p, at, now):
                    continue
                candidates.append((at, p['start'], p['pid'], p))
            if candidates:
                at, _, _, p = min(candidates, key=lambda c:c[:3])
                task.update(owner=p['pid'], allowedAt=at, deadlineAt=at+3,
                            status='assigned', revision=task['revision']+1)
                if task.get('transferredFrom')==p['pid']:
                    task.pop('transferredFrom',None)
                task['moves'][0]['pid'] = p['pid']
                bind(eng,p,task)
                if task['template']['mode']=='push7':
                    for pid in task.get('previousOwners',[]):
                        eng.P[pid]['flags']=[flag for flag in eng.P[pid].get('flags',[]) if flag!='推7点']
                    if '推7点' not in p['flags']:p['flags'].append('推7点')
                for pid in task.get('previousOwners', []):
                    if eng.P[pid].get('taskTimeout',{}).get('task') == task['id']:
                        eng.P[pid]['taskTimeout']['transferredTo'] = p['pid'] if pid!=p['pid'] else None
                eng.h.log('task_transfer','system',p['pid'],task['line'],None,
                          {'task':task['id'],'from':task.get('transferredFrom'),'allowedAt':at,'deadlineAt':at+3})
                changed = True
    changed=C.fill(eng,now) or changed
    return replan(eng, now) or changed


def _outcome(eng, pid, now, special=None):
    if special in ('chain','push7'):
        if special == 'push7': return 'rest'
        _, kind, _ = eng.leave_at(eng.P[pid], now)
        return 'away' if kind == 'out' else 'done'
    return 'done' if now >= eng.P[pid]['end'] else 'rest'


def _build_batch(eng, line, people, at, now):
    """Produce a full mapping, then split it into disjoint paths from newcomers."""
    from app.rotation.engine import ActionError
    tasks = eng.d['rotationTasks']
    from app.rotation import timeout_cases as C
    reserved = {key for t in tasks.values() if t['status'] in ACTIVE|{'exception_hold'} for key in t['expected']} | C.reserved(eng)
    indices = [i for i,post in enumerate(line['posts']) if available_at(post,at)
               and not held(post) and post['key'] not in reserved]
    initial = {i:line['posts'][i].get('occ') for i in indices}
    final = dict(initial)
    protected, outcomes, selected = set(), {}, []
    specials = [p for p in people if p['assign']['mode'] in ('push7','chain','opening')]
    for p in sorted(specials, key=lambda p:({'push7':0,'chain':1,'opening':2}[p['assign']['mode']],p['pid'])):
        a = p['assign']; available = [i for i in indices if i not in protected]
        if not available: continue
        if a['mode'] in ('chain','push7'):
            target = next((i for i in available if final[i] == a.get('target')), None)
        else:
            target = next((i for i in available if line['posts'][i]['key']==a.get('openingKey')),a.get('post'))
            if target not in available or final[target] is not None: target = None
        if target is None: continue
        path = [target] if a['mode']=='push7' else [i for i in available if i<=target]
        if a['mode']=='opening':
            # Adding a post must enter from the line's first eligible post.
            # A pending route may reserve that prefix; wait instead of entering
            # halfway down the line or directly at the new post.
            path=[i for i,post in enumerate(line['posts']) if i<=target
                  and available_at(post,at) and not held(post)]
            if any(i not in available for i in path):continue
        target_pid = final[target]
        old = [final[i] for i in path if final[i] and final[i]!=target_pid]
        replacement = [p['pid']]+old
        for index,i in enumerate(path): final[i] = replacement[index] if index<len(replacement) else None
        if target_pid: outcomes[target_pid] = _outcome(eng,target_pid,at,a['mode'])
        protected.update(path);selected.append(p)
    available = [i for i in indices if i not in protected]
    ordinary = sorted([p for p in people if p not in specials],
                      key=lambda p:(p['assign']['departAt'],p['start'],p['pid']),reverse=True)[:len(available)]
    occupants = [final[i] for i in available if final[i]]
    leave_count = max(0,len(occupants)+len(ordinary)-len(available))
    longest = sorted(occupants,key=lambda pid:(-continuous_minutes(eng.P[pid],now),
                     -next(i for i in available if final[i]==pid),pid))[:leave_count]
    retained = [pid for pid in occupants if pid not in longest]
    sequence = [p['pid'] for p in ordinary]+retained
    for index,i in enumerate(available): final[i] = sequence[index] if index<len(sequence) else None
    for pid in longest: outcomes[pid] = _outcome(eng,pid,at)
    selected += ordinary
    batch = uuid4().hex
    for p in selected:
        entry = next(i for i,pid in final.items() if pid==p['pid'])
        positions = {pid:i for i,pid in final.items() if pid}
        moves = [{'pid':p['pid'],'from':None,'to':line['posts'][entry]['key']}]
        expected = {}; cursor=entry;seen=set()
        while True:
            if cursor in seen: raise ActionError('接替路线出现循环，请调整岗位')
            seen.add(cursor); key=line['posts'][cursor]['key'];expected[key]=initial[cursor]
            pid=initial[cursor]
            if not pid: break
            destination=positions.get(pid)
            moves.append({'pid':pid,'from':key,'to':line['posts'][destination]['key'] if destination is not None else None,
                          'outcome':outcomes.get(pid,'rest') if destination is None else None})
            if destination is None: break
            cursor=destination
        refresh=p.pop('_taskRefresh',None) or {}
        allowed=refresh.get('allowedAt',at)
        task={'id':refresh.get('id') or uuid4().hex,'revision':refresh.get('revision',1),
              'owner':p['pid'],'line':line['id'],'batch':batch,'batchSize':len(selected),
              'allowedAt':allowed,'deadlineAt':refresh.get('deadlineAt',allowed+3),
              'template':{k:deepcopy(v) for k,v in p['assign'].items() if k not in ('taskId','blocked')},
              'moves':moves,'expected':expected,'status':'assigned'}
        tasks[task['id']]=task;bind(eng,p,task)
    return bool(selected)


def plan(eng, now):
    from app.rotation import timeout_cases as C
    changed=C.discover(eng,now)
    changed=C.fill(eng,now) or changed
    for line in eng.d['lines']:
        if not line.get('active'): continue
        candidates=[p for p in eng.P.values() if p.get('assign') and not p['assign'].get('taskId') and not p['assign'].get('queueId')
                    and p['assign']['line']==line['id'] and not p.get('entryHold')
                    and p.get('state') in ('rest','meal','ready')]
        candidates.sort(key=lambda p:(max(p['assign']['departAt'],p.get('readyAt') or now,p['assign'].get('notBefore',0)),p['pid']))
        while candidates:
            first=candidates[0]
            first_at=max(first['assign']['departAt'],first.get('readyAt') or now,first['assign'].get('notBefore',0))
            if first_at>now+3+EPS: break
            cohort=[p for p in candidates if max(p['assign']['departAt'],p.get('readyAt') or now,p['assign'].get('notBefore',0))-first_at<=1+EPS]
            at=max(max(p['assign']['departAt'],p.get('readyAt') or now,p['assign'].get('notBefore',0)) for p in cohort)
            if at>now+3+EPS:break  # Issue the whole round only three minutes before its latest ready time.
            changed=_build_batch(eng,line,cohort,at,now) or changed
            for p in cohort:
                candidates.remove(p)
                if not (p.get('assign') or {}).get('taskId'):
                    blocked_by_task=any(t['status'] in ACTIVE|{'exception_hold'} and t['line']==line['id'] for t in eng.d['rotationTasks'].values())
                    possible=C.nominal(eng,p,now) if blocked_by_task else None
                    if possible and any(t['status'] in ACTIVE|{'exception_hold'} and t['line']==line['id']
                                        and resources(t)&resources(possible) for t in eng.d['rotationTasks'].values()):
                        eng.d.setdefault('timeoutBlockedNeeds',{})[p['pid']]={'template':deepcopy(possible['template']),'at':now}
                    p['assign']=None
                    p.pop('_taskRefresh',None)
    return changed


def execute(eng, p, now, actor):
    from app.rotation.engine import ActionError
    task=task_for(eng,p)
    if not task or task['status']!='assigned' or task['owner']!=p['pid']:
        raise ActionError('任务已变化，请查看最新安排')
    if now<task['allowedAt']-EPS or now<(p.get('readyAt') or now)-EPS:
        raise ActionError('尚未到允许出发时间')
    if now>task['deadlineAt']+EPS:
        raise ActionError('任务已超时，请刷新查看转接情况')
    if p['state']!='ready' or p.get('entryHold') or not valid(eng,task):
        raise ActionError('岗位或人员安排已变化，请刷新任务')
    moves=deepcopy(task['moves'])
    for move in moves:
        q=eng.P[move['pid']]
        if move['from']:
            line,index,post=locate(eng,move['from'])
            if move['to']:
                eng._leave_line(q,line,index,now);post['occ']=None
            elif move.get('outcome') in ('done','away'):
                eng._remove_leaving(q['pid'],line,index,now,'推出圈' if move['outcome']=='away' else '推下班')
                post['occ']=None
            else:
                eng._push_off(q['pid'],line,index,now,'正常轮岗');post['occ']=None
    for move in moves:
        if not move['to']: continue
        q=eng.P[move['pid']];line,_,post=locate(eng,move['to'])
        post.update(occ=q['pid'],since=now)
        if q['pid']==p['pid']:q['departedAt']=now
        eng._enter(q,line,now)
    task.update(status='executed',executedAt=now)
    if task['template']['mode']=='push7':
        target=eng.P.get(task['template'].get('target'))
        if target:
            target['flags']=[flag for flag in target.get('flags',[]) if flag not in ('7点岗','推7点下来')]
            target['sevenReplaced']=True
        p['flags']=[flag for flag in p.get('flags',[]) if flag!='推7点']
        eng.h.log('push7',actor,p['pid'],task['line'],None,{'target':task['template'].get('target'),'task':task['id']})
    eng.h.log('depart',actor,p['pid'],task['line'],None,
              {'task':task['id'],'routes':route_view(eng,task),'batch':task['batch']})


def toggle_101(eng, on, now, actor):
    targets=[(line,post) for line in eng.d['lines'] for post in line['posts'] if post.get('hold101Role')]
    affected={'post:'+post['key'] for _,post in targets}
    recent=[r for r in eng.d.get('confirmations',{}).values() if r.get('action')=='depart'
            and r.get('status') not in ('reversed','superseded') and now-3-EPS<=r.get('minute',-100)<=now
            and resources(eng.d.get('rotationTasks',{}).get((r.get('assignment') or {}).get('taskId'),{})).intersection(affected)]
    for line,post in targets:
        if on: post['hold101']={'pid':post.get('occ'),'since':now,'reason':'101定岗','by':getattr(actor,'name',str(actor))}
        else: post.pop('hold101',None)
    eng.d['hold101Active']=on
    if on:
        replan(eng,now,{line['id'] for line,_ in targets},'101定岗',{'post:'+post['key'] for _,post in targets})
        plan(eng,now)
    eng.h.log('hold101' if on else 'resume101',actor,None,None,None,{'recent':recent,'posts':[p['key'] for _,p in targets]})
    return recent
