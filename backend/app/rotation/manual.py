"""Preview and execute room edits using the same engine functions."""
import hashlib
import json
from copy import deepcopy
from app.rotation.engine import Engine, ActionError

def fingerprint(state):
    value={'date':state['date'],'epoch':state.get('confirmationEpoch'),'closed':state.get('closed'),
           'lines':state['lines'],'persons':{pid:{k:p.get(k) for k in ('state','line','readyAt','assign','start','end','continuousStart','confirmationSequence')} for pid,p in state['persons'].items()},
           'tasks':{key:(task['revision'],task['status']) for key,task in state.get('rotationTasks',{}).items()}}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def apply(eng, body, now, actor):
    from app.rotation.service import action_boolean,action_time,_require
    act=body['action'];_require(body,'line','i')
    before={p.get('key'):(line['id'],i,deepcopy(p)) for line in eng.d['lines'] for i,p in enumerate(line['posts'])}
    if not isinstance(body['line'],str):raise ActionError('线路选择不正确')
    if act=='move' and not isinstance(body.get('pid'),str):raise ActionError('工号必须是字符串')
    if act=='move':
        _require(body,'pid');eng.act_move(body['pid'],body['line'],body['i'],now,actor,
            force=action_boolean(body,'force') if 'force' in body else False,reason=str(body.get('reason') or '').strip())
    elif act=='post_order':
        _require(body,'target_i');eng.act_post_order(body['line'],body['i'],body['target_i'],now,actor,swap_mode=body.get('swapMode'),target_lid=body.get('target_line'))
    elif act=='post':
        opened=action_boolean(body,'open')
        eng.act_post(body['line'],body['i'],opened,now,actor,
                     open_at=action_time(body['openAt']) if opened and body.get('openAt') else None,
                     close_at=action_time(body['closeAt']) if not opened and body.get('closeAt') else None,
                     close_mode=body.get('closeMode','direct'),cancel_close=action_boolean(body,'cancelClose') if 'cancelClose' in body else False)
    else:
        raise ActionError('该操作不支持现场调整预览')
    if eng.d.get('taskWorkflow'):
        from app.rotation import workflow as W
        after={p.get('key'):(line['id'],i,p) for line in eng.d['lines'] for i,p in enumerate(line['posts'])}
        keys={key for key in before.keys()|after.keys() if before.get(key)!=after.get(key)}
        if keys:
            lines={values[key][0] for values in (before,after) for key in keys if key in values}
            affected=None if act in ('post','post_order') else {'post:'+key for key in keys if key}
            W.replan(eng,now,lines,'现场拖拽／岗位调整',affected)
            W.plan(eng,now)
            recent=recent_related(eng,before,now)
            if recent and actor!='preview' and body.get('notified') is not True:
                raise ActionError('近3分钟有人已确认相关任务，请核实并确认可立即通知后再调整')


def recent_related(eng,before,now):
    from app.rotation.workflow import resources
    after={p.get('key'):(line['id'],i,p) for line in eng.d['lines'] for i,p in enumerate(line['posts'])}
    keys={key for key in before.keys()|after.keys() if before.get(key)!=after.get(key)}
    affected={'post:'+key for key in keys if key}
    result=[]
    for receipt in eng.d.get('confirmations',{}).values():
        if receipt.get('action')!='depart' or receipt.get('status') in ('reversed','superseded') or not now-3<=receipt.get('minute',-1)<=now:
            continue
        task=eng.d.get('rotationTasks',{}).get((receipt.get('assignment') or {}).get('taskId'))
        if task and resources(task).intersection(affected):
            result.append({'pid':receipt['pid'],'name':eng.P[receipt['pid']]['name'],
                           'minute':receipt['minute'],'line':receipt.get('line'),'post':receipt.get('post'),
                           'mode':task.get('template',{}).get('mode')})
    return result

class Hooks:
    def __init__(self,week): self.week=week;self.events=[]
    def log(self,*args): self.events.append(args)
    def segment(self,*args): pass
    def week_minutes(self): return self.week

def preview(db,attraction_id,body):
    from app.rotation import service
    with service.transaction(db):
        cfg=service.load_config(db,attraction_id);day,now=service.clock_now(cfg);row=service.day_row(db,attraction_id,day)
        if not row or row.status!='live': raise ActionError('今天的轮岗尚未开始')
        state=service.day_state(row)
        service.advance(db,cfg,row,state,now)
        token=fingerprint(state);after=deepcopy(state)
        hooks=Hooks(service.week_minutes(db,attraction_id,day));eng=Engine(after,service.settings_of(cfg),hooks)
        apply(eng,body,now,'preview')
        from app.rotation import workflow as W
        before_posts={p.get('key'):(line['id'],i,deepcopy(p)) for line in state['lines'] for i,p in enumerate(line['posts'])}
        recent=recent_related(eng,before_posts,now)
        refreshed=[]
        for pid,p in state['persons'].items():
            old=state.get('rotationTasks',{}).get((p.get('assign') or {}).get('taskId'))
            new=W.task_for(eng,eng.P[pid])
            if old and (not new or old['revision']!=new['revision']):
                refreshed.append({'pid':pid,'name':p['name'],'allowedAt':old['allowedAt'],
                                  'deadlineAt':old['deadlineAt'],'routes':W.route_view(eng,new) if new else []})
        def position(d,pid):
            for L in d['lines']:
                for x in L['posts']:
                    if x.get('occ')==pid:return L['id']+' · '+x['name']
            return {'walkback':'下线，待到休息室确认','rest':'休息','ready':'待出发','meal':'吃饭',
                    'notyet':'未到班次 · 待安排','done':'已离岗','away':'圈外人员','op':'OP待确认休息',
                    'pending':'待到大屏确认休息','excluded':'OJT/不轮岗'}.get(d['persons'][pid]['state'],'待核实')
        changes=[p['name']+'：'+position(state,pid)+' → '+position(after,pid) for pid,p in state['persons'].items() if position(state,pid)!=position(after,pid)]
        if body['action']=='post_order':
            original = Engine(state, service.settings_of(cfg), hooks)
            source, _, left = original.post(body['line'], body['i'])
            target, _, right = original.post(body.get('target_line') or body['line'], body['target_i'])
            changes.insert(0, f"{source['id']} · {left['name']} ↔ {target['id']} · {right['name']}")
        if body['action'] != 'post_order':
            changes += [f"{L['id']} · {x['name']}：{'开岗' if x.get('open') else '关闭'}" for L in after['lines'] for x in L['posts'] if next((old.get('open') for line in state['lines'] if line['id']==L['id'] for old in line['posts'] if old['name']==x['name']),None)!=x.get('open')]
        return {'expected_snapshot':token,'changes':changes,'recent':recent,'tasks':refreshed,'summary':'强制安排上岗' if body.get('force') else '取消待撤岗计划' if body.get('cancelClose') else '只交换岗位' if body.get('swapMode')=='posts' else '岗位和人员一起交换' if body.get('swapMode')=='with_people' else '岗位顺序调整' if body['action']=='post_order' else '定时调整' if body.get('openAt') or body.get('closeAt') else '现场调整'}
