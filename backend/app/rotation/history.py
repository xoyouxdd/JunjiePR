"""Undo previews from audited, dependency-scoped patches, never whole-day rollback."""
from bisect import bisect_right
from copy import deepcopy
import json


def snapshot(state):
    return deepcopy({'persons':state['persons'],'lines':state['lines'],
                     'hold101Active':state.get('hold101Active',False),'closed':state.get('closed',False)})


def _posts(state):
    return {p['key']:{'line':l['id'],'index':i,'post':p} for l in state['lines']
            for i,p in enumerate(l['posts']) if p.get('key')}


def existing_segment_ids(db, eng, now):
    from app.rotation.namespace import tables_for
    model = tables_for(db).RotationSegment
    return [row.id for row in db.query(model.id).filter(
        model.attraction_id == eng.h.attraction_id, model.work_date == eng.h.day, model.end_min >= now)]


def reverted_legacy_push7(db, attraction_id, events, restored):
    """Find old successful events from before executions carried explicit IDs."""
    from app.rotation.namespace import tables_for

    days = {event.work_date for event, detail in events if event.line and not detail.get('push7_execution')}
    days.intersection_update(restored)
    if not days:
        return set()
    model = tables_for(db).RotationEvent
    checkpoints = {day: [] for day in days}
    ids = {day: [] for day in days}
    for row in db.query(model.id, model.work_date, model.minute, model.detail_json).filter(
            model.attraction_id == attraction_id, model.work_date.in_(days),
            model.event_type == 'workflow_checkpoint').order_by(model.id):
        ids[row.work_date].append(row.id)
        checkpoints[row.work_date].append(row)
    reverted = set()
    for event, detail in events:
        if event.work_date not in days or detail.get('push7_execution'):
            continue
        # The checkpoint follows the action's event in the same transaction.
        i = bisect_right(ids[event.work_date], event.id)
        rows = checkpoints[event.work_date]
        if i == len(rows) or rows[i].id not in restored[event.work_date] or rows[i].minute != event.minute:
            continue
        target = detail.get('target') or next((m['pid'] for m in detail.get('moves', []) if m['to'] is None), None)
        entry = json.loads(rows[i].detail_json)
        people = entry['beforePeople']
        before = people.get(target) or {}
        if (event.line in entry['lines'] and event.employee_no in people and before.get('state') == 'onpost'
                and not before.get('sevenReplaced')):
            reverted.add(event.id)
    return reverted


def record(db, eng, before, action, actor, now):
    from app.rotation.namespace import tables_for
    after=snapshot(eng.d);left,right=_posts(before),_posts(after)
    keys={key for key in left.keys()|right.keys() if left.get(key)!=right.get(key)}
    if not keys:return
    people={value['post'].get('occ') for values in (left,right) for key,value in values.items() if key in keys}
    people.discard(None)
    # A scheduled post change may alter its occupant only on a later clock tick.
    # Keep that person's original state even when scheduling itself didn't move them.
    lines={values[key]['line'] for values in (left,right) for key in keys if key in values}
    payload={'action':action,'lines':sorted(lines),'minute':now,
             'resources':sorted({'post:'+key for key in keys}|{'person:'+pid for pid in people}),
             'beforePeople':{pid:before['persons'].get(pid) for pid in people},
             'beforePosts':{key:left.get(key) for key in keys},
             'beforeLines':{l['id']:{k:v for k,v in l.items() if k!='posts'} for l in before['lines'] if l['id'] in lines},
             'before101':before.get('hold101Active',False),'beforeClosed':before.get('closed',False)}
    if 'existingSegmentIds' in before:
        payload['existingSegmentIds'] = before['existingSegmentIds']
    model=tables_for(db).RotationEvent
    db.add(model(attraction_id=eng.h.attraction_id,work_date=eng.h.day,minute=now,
                 event_type='workflow_checkpoint',actor_type='system',actor_name=getattr(actor,'name',str(actor)),
                 line=sorted(lines)[0],detail_json=json.dumps(payload,ensure_ascii=False,separators=(',',':'))))


def plan(db, eng, line):
    from app.rotation.namespace import tables_for
    from app.rotation.engine import ActionError
    eng.line(line)
    rows=db.query(tables_for(db).RotationEvent).filter_by(attraction_id=eng.h.attraction_id,
            work_date=eng.h.day,event_type='workflow_checkpoint').order_by(tables_for(db).RotationEvent.id.asc()).all()
    undone=set(eng.d.get('reversedCheckpoints',[]))
    entries=[(r.id,json.loads(r.detail_json)) for r in rows if r.id not in undone]
    anchor=next(((index,rid,payload) for index,(rid,payload) in reversed(list(enumerate(entries))) if line in payload['lines']),None)
    if not anchor:raise ActionError('该线路暂无可返回的上一步')
    index,rid,payload=anchor
    affected=set(payload['resources']);selected=[(rid,payload)]
    for other_id,other in entries[index+1:]:
        if affected.intersection(other['resources']):
            affected.update(other['resources']);selected.append((other_id,other))
    return {'id':rid,'entries':selected,'resources':affected,'minute':payload['minute'],'action':payload['action']}


def apply(eng, undo, now):
    affected={v.split(':',1)[1] for v in undo['resources'] if v.startswith('person:')}
    prior_sequences={}
    current_sequences = {pid: eng.P[pid].get('confirmationSequence', 0) for pid in affected}
    for _, entry in undo['entries']:
        for pid, person in entry['beforePeople'].items():
            if person is not None:
                prior_sequences.setdefault(pid, person.get('confirmationSequence', 0))
    for _,entry in reversed(undo['entries']):
        for pid,person in entry['beforePeople'].items():
            if person is not None:eng.P[pid]=deepcopy(person)
        restored=entry['beforePosts']
        for line in eng.d['lines']:
            line['posts']=[p for p in line['posts'] if p.get('key') not in restored]
            if line['id'] in entry['beforeLines']:
                line.update(deepcopy(entry['beforeLines'][line['id']]))
        for item in sorted((p for p in restored.values() if p),key=lambda p:(p['line'],p['index'])):
            eng.line(item['line'])['posts'].insert(item['index'],deepcopy(item['post']))
        if entry['action'] in ('hold101','resume101'):eng.d['hold101Active']=entry['before101']
        if entry['action']=='set_close':eng.d['closed']=entry['beforeClosed']
    for pid in affected:
        p=eng.P[pid]
        p['confirmationSequence']=max(p.get('confirmationSequence',0),current_sequences.get(pid,0))+1
        p['assign']=None;p.pop('_taskRefresh',None)
    for receipt in eng.d.get('confirmations',{}).values():
        pid = receipt.get('pid')
        if pid not in affected:
            continue
        sequence = receipt.get('sequence')
        if pid in prior_sequences and sequence is not None:
            reversed_by_undo = sequence > prior_sequences[pid]
        else:
            reversed_by_undo = receipt.get('minute', -1) >= undo['minute']
        if reversed_by_undo:
            receipt['status']='reversed'
    eng.d.setdefault('reversedCheckpoints',[]).extend(rid for rid,_ in undo['entries'])
    return affected


def preview(db, attraction_id, line):
    from app.rotation import service as S
    from app.rotation.manual import fingerprint, Hooks
    from app.rotation.engine import Engine, ActionError
    with S.transaction(db):
        cfg=S.load_config(db,attraction_id);day,now=S.clock_now(cfg);row=S.day_row(db,attraction_id,day)
        if not row or row.status!='live':raise ActionError('今天尚未生成轮岗安排')
        state=S.day_state(row);S.advance(db,cfg,row,state,now);eng,_=S.engine_for(db,cfg,row,state)
        undo=plan(db,eng,line);after=deepcopy(state)
        trial=Engine(after,S.settings_of(cfg),Hooks(S.week_minutes(db,attraction_id,day)))
        apply(trial,undo,now)
        def position(engine,pid):
            l,i=engine.find_post(pid)
            return l['id']+' · '+l['posts'][i]['name'] if l else {'ready':'待重新安排','rest':'休息','meal':'吃饭','done':'已下班','walkback':'下线途中'}.get(engine.P[pid]['state'],engine.P[pid]['state'])
        pids=sorted({v.split(':',1)[1] for v in undo['resources'] if v.startswith('person:')})
        return {'checkpoint':undo['id'],'expected_snapshot':fingerprint(state),
                'action':undo['action'],'minute':undo['minute'],
                'changes':[f"{eng.P[pid]['name']}：{position(eng,pid)} → {position(trial,pid)}" for pid in pids],
                'related_lines':sorted({l for _,entry in undo['entries'] for l in entry['lines']}),
                'before_lines':S.lines_view(eng),'after_lines':S.lines_view(trial),
                'notice':'相关人员后续休息／吃饭作废且不计时；跨线关联操作一并恢复，已核实或可立即通知后再确认。'}


def execute(db, eng, line, checkpoint, now, actor):
    from app.rotation.namespace import tables_for
    from app.rotation.engine import ActionError
    undo=plan(db,eng,line)
    if undo['id']!=checkpoint:raise ActionError('该线路上一步已变化，请重新预览')
    completed={p['push7Completion']['id'] for p in eng.P.values() if p.get('push7Completion')}
    affected=apply(eng,undo,now)
    reverted_push7=sorted(completed-{p['push7Completion']['id'] for p in eng.P.values() if p.get('push7Completion')})
    model=tables_for(db).RotationSegment
    segments=db.query(model).filter(model.attraction_id==eng.h.attraction_id,model.work_date==eng.h.day,
            model.employee_no.in_(affected),model.end_min>=undo['minute']).all()
    first_checkpoints={}
    for _, entry in undo['entries']:
        for pid in entry['beforePeople']:
            first_checkpoints.setdefault(pid, entry)
    def belongs_to_undo(segment):
        entry = first_checkpoints.get(segment.employee_no, {})
        if 'existingSegmentIds' in entry:
            return segment.id not in entry['existingSegmentIds']
        if 'segmentIdsAtAction' not in entry:
            return True
        return (segment.end_min > entry['minute'] or
                (segment.end_min == entry['minute'] and segment.id not in entry['segmentIdsAtAction']))
    segments = [segment for segment in segments if belongs_to_undo(segment)]
    cancelled=[{'employee_no':s.employee_no,'line':s.line,'start':s.start_min,'end':s.end_min,'minutes':s.minutes} for s in segments]
    for segment in segments:db.delete(segment)
    db.flush()
    eng.h._week=None
    eng.h.log('workflow_restore',actor,None,line,None,
              {'checkpoints':[rid for rid,_ in undo['entries']],'cancelled_segments':cancelled,
               'affected':sorted(affected),'reason':'现场核实后恢复',
               **({'reverted_push7':reverted_push7} if reverted_push7 else {})})
    # The current day may still have pre-ID executions in the cached totals.
    db.flush()
    eng.d.pop('push7History', None)
