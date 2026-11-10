"""Daily plans and recorded history. Reads never simulate or advance a historical day."""
import base64
import calendar
from copy import deepcopy
from datetime import date
import hashlib
import json
import zlib
from uuid import uuid4
from app.rotation import service as S
from app.rotation.namespace import tables_for
from app.rotation.models import RotationDay, RotationRosterEntry, RotationSnapshot


def capture_snapshot(db,attraction_id,state,settings,minute):
    RotationSnapshot = tables_for(db).RotationSnapshot
    if state.get('status') not in ('live','ended'):return
    clean={k:v for k,v in state.items() if k not in ('initialDraft','initialSettings','confirmations','lastTick','dailyOpening','push7History')}
    encoded=json.dumps({'state':clean,'settings':settings},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    digest=hashlib.sha256(encoded).hexdigest()
    db.flush()
    prior=db.query(RotationSnapshot.fingerprint).filter_by(attraction_id=attraction_id,work_date=state['date']).order_by(RotationSnapshot.id.desc()).first()
    if prior and prior.fingerprint==digest:return
    db.add(RotationSnapshot(attraction_id=attraction_id,work_date=state['date'],minute=minute,
                            fingerprint=digest,payload=base64.b64encode(zlib.compress(encoded)).decode('ascii')))


def month_bounds(month):
    try:
        if len(month)!=7 or month[4]!='-':raise ValueError()
        year,m=map(int,month.split('-'));date(year,m,1)
        return f'{month}-01',f'{month}-{calendar.monthrange(year,m)[1]:02d}'
    except (ValueError,TypeError):raise S.ActionError('月份格式需为 YYYY-MM')


@S.transactional
def month_payload(db,attraction_id,month):
    RotationDay = tables_for(db).RotationDay
    RotationRosterEntry = tables_for(db).RotationRosterEntry
    first,last=month_bounds(month)
    cfg=S.load_config(db,attraction_id);today,_=S.clock_now(cfg)
    roster={r[0] for r in db.query(RotationRosterEntry.work_date).filter(RotationRosterEntry.attraction_id==attraction_id,RotationRosterEntry.work_date>=first,RotationRosterEntry.work_date<=last).distinct()}
    rows={r.work_date:r for r in db.query(RotationDay).filter(RotationDay.attraction_id==attraction_id,RotationDay.work_date>=first,RotationDay.work_date<=last)}
    return {'month':month,'today':today,'days':[{'date':f'{month}-{n:02d}','has_roster':f'{month}-{n:02d}' in roster,
        'status':rows[f'{month}-{n:02d}'].status if f'{month}-{n:02d}' in rows else None} for n in range(1,int(last[-2:])+1)]}


def present(db,cfg,attraction_id,work_date,state,minute,settings=None):
    # Private state and engine without hooks: historical reads cannot persist changes.
    state=deepcopy(state);state.setdefault('confirmationEpoch','historical-read-only')
    from app.rotation.cooperation import PreviewHooks
    class ReadHooks:
        def week_minutes(self): return {}
    eng=S.E.Engine(state,settings or S.settings_of(cfg),PreviewHooks(ReadHooks()))
    eng._read_projection={}
    taken=eng.targeted() if state.get('status')=='live' else {}
    persons=[S.person_view(eng,p,minute,taken) for p in state['persons'].values()]
    for p in persons:
        p.pop('confirmationTokens',None);p['canDepart']=False;p['canArrive']=False
    return {'date':work_date,'status':state['status'],'lines':S.lines_view(eng),'persons':persons,'off':state.get('off',[]),'closeAt':state.get('closeAt'),'rotationEnd':S.E.rotation_end(state),'plan':state.get('plan',{})}


def first_start(state):
    return min((p['start'] for p in state.get('persons', {}).values()), default=1440)


def prestart(state, now):
    return (state.get('status') == 'live' and not state.get('dailyOpening')
            and now < first_start(state)
            and all(p['state'] in ('notyet', 'excluded') for p in state['persons'].values()))


def draft_editable(state, work_date, today, now):
    return work_date > today or work_date == today and now < first_start(state)


def plan_revision(state):
    content = {'lines': state['lines'], 'plan': state['plan'], 'closeAt': state.get('closeAt'),
               'persons': {pid: {k: p.get(k) for k in ('role', 'start', 'end', 'state')} for pid, p in state['persons'].items()}}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def planned_view(db, cfg, attraction_id, work_date, state, settings=None):
    from app.rotation.manual import Hooks
    view = deepcopy(state)
    view.pop('dailyOpening', None)
    settings = settings or S.settings_of(cfg)
    eng = S.E.Engine(view, settings, Hooks({}))
    # Project only a pre-shift board. No writes, clock advance or confirmations.
    eng.publish(min(S.PREARRANGE_MINUTE, first_start(view) - 1), 'preview')
    return present(db, cfg, attraction_id, work_date, view, min(S.PREARRANGE_MINUTE, first_start(view) - 1), settings)


def fill_prestart_spares(state):
    """Append missing standard slots without moving any planned people/posts."""
    added = S.E.append_standard_spares(state['lines'])
    for line in state['lines']:
        count = added.get(line['id'], 0)
        if count:
            for post in line['posts'][-count:]:
                post.update(openAt=None, closeAt=None, seven=None, key=uuid4().hex, hold101Role=None,
                            open=False, opened=True, manual=bool(post.get('draftClosed')), occ=None, since=None)
    return added


@S.transactional
def day_payload(db,attraction_id,work_date,at=None):
    RotationSnapshot = tables_for(db).RotationSnapshot
    try:date.fromisoformat(work_date)
    except ValueError:raise S.ActionError('日期格式需为 YYYY-MM-DD')
    cfg=S.load_config(db,attraction_id);today,now=S.clock_now(cfg)
    minute=S.action_time(at) if at is not None else None
    row=S.day_row(db,attraction_id,work_date)
    roster=S.roster_summary(db,attraction_id,work_date)
    state=S.day_state(row) if row else None
    # Templates do not retrofit already-generated plans. Only upgrade editable
    # pre-start states; historical/frozen snapshots and existing assignments stay intact.
    editable = bool(state and at is None and
        (row.status == 'draft' and draft_editable(state,work_date,today,now)
         or work_date == today and prestart(state,now)))
    if editable:
        added = fill_prestart_spares(state)
        if added:
            S.save_day(db,row,state)
            S.DbHooks(db,attraction_id,work_date).log('draft_fill_spares','system',None,None,None,{'added':added})
    if state and work_date==today and row.status=='live':
        S.advance(db,cfg,row,state,now)
    query=db.query(RotationSnapshot).filter_by(attraction_id=attraction_id,work_date=work_date)
    snapshots=query.with_entities(RotationSnapshot.id,RotationSnapshot.minute).order_by(RotationSnapshot.minute,RotationSnapshot.id).all()
    latest=(query.filter(RotationSnapshot.minute<=minute) if minute is not None else query).order_by(RotationSnapshot.minute.desc(),RotationSnapshot.id.desc()).first()
    result={'date':work_date,'today':today,'clock':S.clock_payload(cfg),'roster':roster,'revision':row.version if row else 0,
            'can_edit':bool(row and row.status=='draft' and work_date>=today),
            'can_generate':bool(roster['available'] and work_date>=today and (not row or row.status=='draft')),
            'draft':state if row and row.status=='draft' else None,'day':None,
            'times':[{'id':s.id,'minute':s.minute} for s in snapshots], 'as_of':latest.minute if latest else None,'requested_time':at,
            'legacy_history':bool(row and row.status!='draft' and not snapshots),'roster_changed':False}
    if row and row.status=='draft':
        upload=S.roster_upload_for(db,attraction_id,work_date)
        result['roster_changed']=bool(upload and state.get('rosterUploadId') and state['rosterUploadId']!=upload.id)
    if latest and (at is not None or state is None):
        saved=json.loads(zlib.decompress(base64.b64decode(latest.payload)))
        result['day']=present(db,cfg,attraction_id,work_date,saved['state'],minute if minute is not None else latest.minute,saved['settings'])
    elif state and row.status!='draft' and minute is None and not state.get('dailyOpening') and not state.get('initialDraft'):
        result['day']=present(db,cfg,attraction_id,work_date,state,float(state.get('lastTick',now)))
    result['first_start'] = first_start(state) if state else None
    result['plan_revision'] = plan_revision(state) if state else None
    result['frozen'] = False
    result['opening_source'] = None
    if at is None and state:
        editable_live = work_date == today and prestart(state, now)
        result['can_edit'] = bool(row.status == 'draft' and draft_editable(state, work_date, today, now) or editable_live)
        result['can_generate'] = result['can_generate'] and result['can_edit']
        result['can_publish'] = row.status == 'draft' and work_date == today
        if result['can_edit']:
            result['draft'] = deepcopy(state)
            result['day'] = planned_view(db, cfg, attraction_id, work_date, state) if row.status == 'draft' else present(db, cfg, attraction_id, work_date, state, now)
            result['prestart_live'] = editable_live
        else:
            result['frozen'] = True
            opening = state.get('dailyOpening')
            if opening:
                result['day'] = present(db, cfg, attraction_id, work_date, opening['state'], opening['minute'], opening['settings'])
                result['as_of'] = opening.get('captured_at', opening['minute']) if opening.get('late_publish') else opening['minute']
                result['opening_source'] = 'late_publish' if opening.get('late_publish') else 'opening'
            elif row.status=='draft':
                result['day'] = planned_view(db,cfg,attraction_id,work_date,state)
                result['opening_source'] = 'unstarted_draft'
            elif state.get('initialDraft'):
                result['day'] = planned_view(db, cfg, attraction_id, work_date, state['initialDraft'], state.get('initialSettings'))
                result['as_of'] = None
                result['opening_source'] = 'initial_draft'
            else:
                earliest = query.order_by(RotationSnapshot.minute, RotationSnapshot.id).first()
                if earliest:
                    saved = json.loads(zlib.decompress(base64.b64decode(earliest.payload)))
                    result['day'] = present(db, cfg, attraction_id, work_date, saved['state'], earliest.minute, saved['settings'])
                    result['as_of'] = earliest.minute
                    result['opening_source'] = 'earliest_record'
                else:
                    result['opening_source'] = 'legacy_last'
    return result


def slot(state,key):
    try:
        lid,num=key.split('#');i=int(num)
        line=next(l for l in state['lines'] if l['id']==lid)
        if i<0 or i>=len(line['posts']) or line.get('standby'):raise ValueError()
    except (ValueError,TypeError,AttributeError,StopIteration):raise S.ActionError('岗位不存在或不可安排')
    kind='seven' if line['posts'][i].get('seven') or key in state['plan']['seven'] else 'crew'
    return line,i,kind


def seven_relief_slots(state):
    plan=state['plan']
    if 'push7Targets' in plan:
        positions={pid:key for key,pid in plan['seven'].items()}
        return {positions[target]:pid for pid,target in plan['push7Targets'].items() if target in positions and pid in plan['push7']}
    order=seven_slot_order(state)
    return dict(zip(order,plan['push7']))


def seven_slot_order(state):
    return sorted(state['plan']['seven'],key=lambda key:slot(state,key)[0]['posts'][slot(state,key)[1]].get('seven') or 9)


def normalize_seven_assignments(state, previous_slots=None):
    """The slot retains its early-post marker; a later worker remains regular crew."""
    plan=state['plan']
    removed=[key for key,pid in plan['seven'].items() if state['persons'].get(pid,{}).get('start')!=420]
    lost_slot = previous_slots is not None and any(key not in plan['seven'] for key in previous_slots)
    if not removed and not lost_slot and 'push7Targets' not in plan:return False
    before=deepcopy(plan)
    previous_slots=seven_relief_slots(state) if previous_slots is None else previous_slots
    for key in removed:plan['crew'][key]=plan['seven'].pop(key)
    # Bind remaining duties to their own early posts instead of shifting the
    # remaining list onto a different target when an intermediate post changes.
    assigned=set(plan['seven'].values())|set(plan['crew'].values())
    targets={previous_slots[key]:target for key,target in plan['seven'].items() if key in previous_slots}
    plan['push7']=[pid for pid in plan['push7'] if pid not in assigned and pid in targets]
    plan['push7Targets']={pid:targets[pid] for pid in plan['push7']}
    return plan!=before


def fill_new_seven_slots(state, previous_slots, settings, read_history=None):
    if state.get('ruleVersion') != 2:
        return
    early_slots = S.E.open_seven_slots(state['lines'])
    plan = state['plan']
    assigned = set(plan['crew'].values()) | set(plan['seven'].values())
    early = [p for p in state['persons'].values() if p['role'] == 'rotation' and p['start'] == 420
             and not p.get('initialPool')]
    S.E.refresh_seven_configuration_alert(state.setdefault('draftAlerts', []), early_slots, len(early))
    available = sorted((p for p in early if p['pid'] not in assigned),
                       key=lambda p: (-(p['end'] - p['start']), p['name'], p['pid']))
    free = [(rank, lid, i) for rank, lid, i in early_slots if (rank, lid, i) not in previous_slots
            and f'{lid}#{i}' not in plan['crew'] and f'{lid}#{i}' not in plan['seven']]
    if not available or not free:
        return
    prior_relief = seven_relief_slots(state)
    added = []
    for (rank, lid, i), p in zip(free, available):
        line = next(line for line in state['lines'] if line['id'] == lid)
        line['posts'][i]['seven'] = rank
        pid = p['pid']
        plan['seven'][f'{lid}#{i}'] = pid
        added.append(pid)
    assigned.update(added)
    history = read_history() if read_history else {}
    candidates = sorted((p for p in state['persons'].values() if p['role'] == 'rotation'
                         and p['start'] == S.E._m(settings['lineOpenAt']) and not p.get('absences')
                         and not p.get('initialPool') and p['pid'] not in assigned and p['pid'] not in plan['push7']),
                        key=lambda p: (S.E.push7_rank(history, p['pid']), p['name'], p['pid']))
    bindings = {pid: plan['seven'][key] for key, pid in prior_relief.items()}
    for target, p in zip(added, candidates):
        plan['push7'].append(p['pid'])
        bindings[p['pid']] = target
    plan['push7Targets'] = bindings


def set_prestart_line(state,lid,active, *, settings=None, read_history=None, now=None):
    if not isinstance(lid,str) or type(active) is not bool:raise S.ActionError('线路或启用状态无效')
    line=next((line for line in state['lines'] if line['id']==lid),None)
    if line is None:raise S.ActionError('线路不存在')
    if not active and any(post.get('hold') or post.get('hold101') or post.get('draftHold') for post in line['posts']):
        raise S.ActionError('本线还有定岗人员，请先取消定岗再停用')
    if (not line.get('standby',False))==active and line.get('active',active)==active:return False
    previous=seven_relief_slots(state)
    previous_open=S.E.open_seven_slots(state['lines']) if state.get('ruleVersion') == 2 else ()
    if state.get('ruleVersion') == 2 and active and now is not None:
        from app.rotation.forecast import expire_closed_line_opening
        for post in line['posts']:
            expire_closed_line_opening(line, post, now)
    line.update(standby=not active,active=active)
    if not active:
        had_seven=any(key.startswith(lid+'#') for key in state['plan']['seven'])
        for kind in ('crew','seven'):
            for key in list(state['plan'][kind]):
                if key.startswith(lid+'#'):state['plan'][kind].pop(key)
        if had_seven:state['plan'].setdefault('push7Targets',{})
        normalize_seven_assignments(state,previous)
    for post in line['posts']:
        post.update(open=active and not post.get('openAt') and not post.get('draftClosed'),
                    opened=bool(post.get('openExpired') or not post.get('openAt')),
                    occ=None,since=None,manual=bool(post.get('draftClosed')))
    fill_new_seven_slots(state, previous_open, settings or S.E.DEFAULT_SETTINGS, read_history)
    sync_prestart(state);validate_plan(state)
    return True


def validate_plan(state):
    used=[];posts=[]
    for kind in ('seven','crew'):
        for key,pid in state['plan'][kind].items():
            line,index,actual=slot(state,key)
            if line['posts'][index].get('draftClosed'):
                raise S.ActionError('该岗位已在预排中撤岗，请先加岗')
            p=state['persons'].get(pid)
            if not p or p['role']!='rotation':raise S.ActionError('只能安排本日轮岗人员')
            if kind=='seven' and p['start']!=420:raise S.ActionError('7点岗位只能安排07:00班次人员')
            if actual!=kind and not(kind=='crew' and actual=='seven' and p['start']!=420):raise S.ActionError('岗位类型与安排不一致')
            used.append(pid)
            posts.append(key)
    if len(used)!=len(set(used)):raise S.ActionError('同一人员不能安排两个岗位')
    if len(posts)!=len(set(posts)):raise S.ActionError('同一岗位不能安排两个人员')
    for line in state['lines']:
        for index, post in enumerate(line['posts']):
            hold = post.get('draftHold')
            if hold:
                key = f"{line['id']}#{index}"
                pid = state['plan']['seven'].get(key) or state['plan']['crew'].get(key)
                if pid != hold['pid']:
                    raise S.ActionError('该岗位已有预予定岗，请先取消后再调整人员')


def configure_post(state, body, now, actor):
    key = body.get('post')
    line, index, _ = slot(state, key)
    if key != f"{line['id']}#{index}":
        raise S.ActionError('岗位标识格式不正确')
    post = line['posts'][index]
    change = body.get('change')
    pid = state['plan']['seven'].get(key) or state['plan']['crew'].get(key)
    is_open = not post.get('draftClosed') and (post.get('open') if state['status'] == 'live' else not post.get('openAt'))
    if change in ('hold', 'resume'):
        if change == 'resume':
            post.pop('draftHold', None)
        else:
            reason = body.get('reason', '')
            if not isinstance(reason, str) or len(reason.strip()) > 100:
                raise S.ActionError('定岗说明须在100字以内')
            if not pid or not is_open or post.get('closeAt'):
                raise S.ActionError('请在已有预排人员且无撤岗计划的开放岗位设置定岗')
            post['draftHold'] = {'pid': pid, 'reason': reason.strip() or '现场任务',
                                 'by': getattr(actor, 'name', str(actor))}
    elif change in ('open', 'close', 'cancel_close'):
        if post.get('draftHold') or post.get('hold') or post.get('hold101'):
            raise S.ActionError('请先取消定岗再调整开撤岗')
        if change == 'cancel_close':
            post.update(closeAt=None, closeMode='direct', closedAuto=False)
            return line, post
        at = S.action_time(body['time']) if body.get('time') else None
        if at is not None and (at < now or at >= S.E.rotation_end(state)):
            raise S.ActionError('计划时间须在当前时间之后、轮岗结束之前')
        if change == 'open':
            if at is not None:
                if pid and at > state['persons'][pid]['start']:
                    raise S.ActionError('加岗时间不能晚于预排人员的班次开始时间')
                if post.get('closeAt') and at >= S.action_time(post['closeAt']):
                    raise S.ActionError('加岗时间须早于撤岗时间')
            post.update(open=at is None, opened=at is None, openAt=S.E.fmt(at) if at is not None else None,
                        openRequest=at is not None, manual=False, draftClosed=False)
            post.pop('openExpired', None)
            post.pop('resumeOpen', None)
            post.pop('resumeScheduled', None)
        else:
            if at is not None:
                if not is_open and (not post.get('openAt') or at <= S.action_time(post['openAt'])):
                    raise S.ActionError('撤岗时间须晚于加岗时间')
                post.update(closeAt=S.E.fmt(at), closeMode='direct', closedAuto=False)
            else:
                if key in state['plan']['seven']:
                    state['plan'].setdefault('push7Targets', {})
                for kind in ('crew', 'seven'):
                    state['plan'][kind].pop(key, None)
                post.update(open=False, opened=True, openAt=None, closeAt=None, closeMode='direct',
                            closedAuto=False, openRequest=False, manual=True, draftClosed=True)
    else:
        raise S.ActionError('不支持的预排岗位操作')
    return line, post


@S.transactional
def edit(db,attraction_id,actor,work_date,body):
    cfg=S.load_config(db,attraction_id);today,now=S.clock_now(cfg)
    if work_date<today:raise S.ActionError('往日班表只读，不能修改')
    row=S.day_row(db,attraction_id,work_date)
    state=S.day_state(row) if row else None
    live_edit=bool(state and work_date==today and prestart(state,now))
    revision=body.get('revision')
    valid_live=live_edit and body.get('plan_revision')==plan_revision(state)
    if not valid_live and (type(revision) is not int or revision!=(row.version if row else 0)):raise S.ConfirmationConflict('班表已更新，请刷新后重新调整')
    action=body.get('action')
    if not isinstance(action,str):raise S.ActionError('操作必须是字符串')
    if state and row.status=='draft' and not draft_editable(state,work_date,today,now) and action!='publish':
        raise S.ActionError('首个班次已开始，每日班表已固定，请到看板处理现场调整')
    if action=='draft_generate':
        if body.get('force'):raise S.ActionError('每日班表不支持清空运行记录')
        S.do_draft_action(db,attraction_id,actor,{'action':action,'date':work_date})
        planned=S.day_row(db,attraction_id,work_date);state=S.day_state(planned);state['dailyAutoStart']=work_date>today;S.save_day(db,planned,state);return
    if not row or (row.status!='draft' and not live_edit):raise S.ActionError('首个班次已开始，每日班表已固定，请到看板处理现场调整')
    state=S.day_state(row)
    previous_slots=seven_relief_slots(state)
    previous_open=S.E.open_seven_slots(state['lines']) if state.get('ruleVersion') == 2 and action in (
        'draft_rename_post', 'draft_post') else ()
    def read_push7_history():
        return S.completed_push7(db, attraction_id, work_date)
    upload=S.roster_upload_for(db,attraction_id,work_date)
    if action=='publish' and upload and state.get('rosterUploadId') and state['rosterUploadId']!=upload.id:
        raise S.ActionError('上传班表已更新，请重新生成并核对预排后启用')
    if action == 'draft_person':
        from app.rotation.forecast_service import shift_values
        pid = body.get('pid')
        if pid not in state['persons']:
            raise S.ActionError('班表中没有此人')
        p = state['persons'][pid]
        operation = body.get('operation')
        if operation == 'shift':
            start, end = shift_values(body)
            p.update(start=start, end=end)
        elif operation in ('sick', 'away', 'back'):
            p['initialPool'] = {'sick': 'sick', 'away': 'away', 'back': 'rest'}[operation]
            for kind in ('crew', 'seven'):
                state['plan'][kind] = {key: value for key, value in state['plan'][kind].items() if value != pid}
            state['plan']['push7'] = [value for value in state['plan']['push7'] if value != pid]
        else:
            raise S.ActionError('不支持的人员调整')
        normalize_seven_assignments(state, previous_slots)
        validate_plan(state)
        if live_edit:
            sync_prestart(state)
        S.save_day(db, row, state)
        hooks = S.DbHooks(db, attraction_id, work_date)
        hooks.now = now
        hooks.log('draft_person', actor, pid, None, None,
                  {'operation': operation, 'start': p['start'], 'end': p['end']})
        return
    if action=='draft_rename_post':
        engine=S.E.Engine(state,S.settings_of(cfg),None)
        line,index,post=engine.plan_post(body.get('post'))
        name=body.get('name')
        if not isinstance(name,str) or not 1<=len(name.strip())<=40:
            raise S.ActionError('岗位名称须为1至40字')
        name=name.strip()
        if body.get('expected_name')!=post['name']:
            raise S.ConfirmationConflict('岗位名称或位置已变化，请刷新后重新修改')
        if any(i!=index and p['name']==name for i,p in enumerate(line['posts'])):
            raise S.ActionError('本线路已有同名岗位')
        old=post['name']
        if old==name:return
        from app.rotation.workflow import initialize
        initialize(engine)  # Bind 101 identity before a first prearrangement rename.
        post['name']=name
        fill_new_seven_slots(state, previous_open, engine.S, read_push7_history)
        if live_edit:
            sync_prestart(state)
        if state.get('ruleVersion') == 2:
            validate_plan(state)
        S.save_day(db,row,state)
        S.DbHooks(db,attraction_id,work_date).log(action,actor,None,line['id'],name,{'from':old,'to':name,'post':body['post']})
        return
    if action=='draft_line':
        lid=body.get('line');active=body.get('active')
        if not set_prestart_line(state,lid,active,settings=S.settings_of(cfg),read_history=read_push7_history,
                                 now=now if live_edit else None):return
        if live_edit:
            sync_prestart(state);S.write_duties(db,attraction_id,work_date,state['plan'],record_push7=state.get('ruleVersion')!=2)
        S.save_day(db,row,state)
        S.DbHooks(db,attraction_id,work_date).log(action,actor,None,lid,None,{'active':active})
        return
    if action == 'draft_post':
        line, post = configure_post(state, body, now if work_date == today else 0, actor)
        normalize_seven_assignments(state, previous_slots)
        fill_new_seven_slots(state, previous_open, S.settings_of(cfg), read_push7_history)
        validate_plan(state)
        if live_edit:
            sync_prestart(state)
            S.write_duties(db, attraction_id, work_date, state['plan'], record_push7=state.get('ruleVersion')!=2)
        S.save_day(db, row, state)
        S.DbHooks(db, attraction_id, work_date).log(action, actor, None, line['id'], post['name'],
                                                   {'change': body['change'], 'post': body['post']})
        return
    if action in ('draft_swap_people','draft_swap_posts'):
        source,target=body.get('source'),body.get('target')
        if action=='draft_swap_people' and isinstance(source,str) and source.startswith('person:'):
            pid=source[7:];right,j,other=slot(state,target)
            if pid not in state['persons'] or state['persons'][pid]['role']!='rotation':raise S.ActionError('只能安排本日轮岗人员')
            if any(pid in state['plan'][k].values() for k in ('crew','seven')):raise S.ActionError('人员安排已变化，请刷新后重试')
            for k in ('crew','seven'):state['plan'][k].pop(target,None)
            state['plan'][other][target]=pid
            normalize_seven_assignments(state,previous_slots)
            validate_plan(state)
            state['plan']['push7']=[p for p in state['plan']['push7'] if p!=pid]
            if live_edit:
                sync_prestart(state);S.write_duties(db,attraction_id,work_date,state['plan'],record_push7=state.get('ruleVersion')!=2)
            S.save_day(db,row,state)
            S.DbHooks(db,attraction_id,work_date).log(action,actor,pid,None,None,{'source':source,'target':target})
            return
        left,i,kind=slot(state,source);right,j,other=slot(state,target)
        if source==target:return
        if action=='draft_swap_posts':
            if left is not right and (any(n!=i and x['name']==right['posts'][j]['name'] for n,x in enumerate(left['posts'])) or any(n!=j and x['name']==left['posts'][i]['name'] for n,x in enumerate(right['posts']))):
                raise S.ActionError('交换后同一线路岗位名称重复')
            left['posts'][i],right['posts'][j]=right['posts'][j],left['posts'][i]
            a=previous_slots.pop(source,None);b=previous_slots.pop(target,None)
            if a:previous_slots[target]=a
            if b:previous_slots[source]=b
        if action=='draft_swap_posts':
            for k in ('seven','crew'):
                a=state['plan'][k].pop(source,None);b=state['plan'][k].pop(target,None)
                if a:state['plan'][k][target]=a
                if b:state['plan'][k][source]=b
        else:
            a=state['plan']['seven'].get(source) or state['plan']['crew'].get(source)
            b=state['plan']['seven'].get(target) or state['plan']['crew'].get(target)
            for k in ('seven','crew'):
                state['plan'][k].pop(source,None);state['plan'][k].pop(target,None)
            if a:state['plan'][other][target]=a
            if b:state['plan'][kind][source]=b
        normalize_seven_assignments(state,previous_slots)
        validate_plan(state)
        # Role changes and person swaps must not duplicate a push7 assignment.
        assigned=set(state['plan']['crew'].values())|set(state['plan']['seven'].values())
        state['plan']['push7']=[p for p in state['plan']['push7'] if p not in assigned]
        if live_edit:
            sync_prestart(state);S.write_duties(db,attraction_id,work_date,state['plan'],record_push7=state.get('ruleVersion')!=2)
        S.save_day(db,row,state)
        S.DbHooks(db,attraction_id,work_date).log(action,actor,None,None,None,{'source':source,'target':target})
        return
    allowed={'draft_set','draft_list','draft_close','draft_role','draft_discard','publish'}
    if action not in allowed:raise S.ActionError('不支持的班表操作')
    if live_edit:
        if action in ('publish','draft_discard'):raise S.ActionError('今日看板已启用，请直接调整预排')
        state['status']='draft'
        S.save_day(db,row,state,notify=False)
    S.do_draft_action(db,attraction_id,actor,{**body,'date':work_date})
    updated=S.day_row(db,attraction_id,work_date)
    if updated and updated.status=='draft':
        state=S.day_state(updated)
        normalized=normalize_seven_assignments(state,None if action=='draft_list' and body.get('kind')=='push7' else previous_slots);validate_plan(state)
        if normalized:S.save_day(db,updated,state)
        if live_edit:
            state['status']='live';sync_prestart(state);S.write_duties(db,attraction_id,work_date,state['plan'],record_push7=state.get('ruleVersion')!=2);S.save_day(db,updated,state)


def sync_prestart(state):
    for line in state['lines']:
        for i, post in enumerate(line['posts']):
            key = f"{line['id']}#{i}"
            pid = state['plan']['seven'].get(key) or state['plan']['crew'].get(key)
            post.update(occ=pid if line.get('active') and post.get('open') else None, since=None)
    for p in state['persons'].values():
        p['state'] = 'excluded' if p['role'] == 'excluded' else 'notyet'
