"""Preview and execute room edits using the same engine functions."""
import hashlib
import json
from copy import deepcopy
from app.rotation.engine import Engine, ActionError

def fingerprint(state):
    value={'date':state['date'],'epoch':state.get('confirmationEpoch'),'closed':state.get('closed'),
           'lines':state['lines'],'persons':{pid:{k:p.get(k) for k in ('state','line','readyAt','assign','start','end')} for pid,p in state['persons'].items()}}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def apply(eng, body, now, actor):
    from app.rotation.service import action_boolean,action_time,_require
    act=body['action'];_require(body,'line','i')
    if not isinstance(body['line'],str):raise ActionError('线路选择不正确')
    if act=='move' and not isinstance(body.get('pid'),str):raise ActionError('工号必须是字符串')
    if act=='move':
        _require(body,'pid');eng.act_move(body['pid'],body['line'],body['i'],now,actor)
    elif act=='post_order':
        _require(body,'target_i');eng.act_post_order(body['line'],body['i'],body['target_i'],now,actor)
    elif act=='post':
        opened=action_boolean(body,'open')
        eng.act_post(body['line'],body['i'],opened,now,actor,
                     open_at=action_time(body['openAt']) if opened and body.get('openAt') else None,
                     close_at=action_time(body['closeAt']) if not opened and body.get('closeAt') else None,
                     close_mode=body.get('closeMode','direct'),cancel_close=action_boolean(body,'cancelClose') if 'cancelClose' in body else False)
    else:
        raise ActionError('该操作不支持现场调整预览')

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
        state=service.day_state(row);token=fingerprint(state);after=deepcopy(state)
        hooks=Hooks(service.week_minutes(db,attraction_id,day));eng=Engine(after,service.settings_of(cfg),hooks)
        apply(eng,body,now,'preview')
        def position(d,pid):
            for L in d['lines']:
                for x in L['posts']:
                    if x.get('occ')==pid:return L['id']+' · '+x['name']
            return {'walkback':'下线，待到休息室确认','rest':'休息','ready':'待出发','meal':'吃饭'}.get(d['persons'][pid]['state'],d['persons'][pid]['state'])
        changes=[p['name']+'：'+position(state,pid)+' → '+position(after,pid) for pid,p in state['persons'].items() if position(state,pid)!=position(after,pid)]
        changes += [f"{L['id']} · {x['name']}：{'开岗' if x.get('open') else '关闭'}" for L in after['lines'] for x in L['posts'] if next((old.get('open') for line in state['lines'] if line['id']==L['id'] for old in line['posts'] if old['name']==x['name']),None)!=x.get('open')]
        return {'expected_snapshot':token,'changes':changes,'summary':'取消待撤岗计划' if body.get('cancelClose') else '岗位顺序调整' if body['action']=='post_order' else '定时调整' if body.get('openAt') or body.get('closeAt') else '现场调整'}
