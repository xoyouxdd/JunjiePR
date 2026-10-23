"""Daily plans and recorded history. Reads never simulate or advance a historical day."""
import base64
import calendar
from copy import deepcopy
from datetime import date
import hashlib
import json
import zlib
from app.rotation import service as S
from app.rotation.namespace import tables_for
from app.rotation.models import RotationDay, RotationRosterEntry, RotationSnapshot


def capture_snapshot(db,attraction_id,state,settings,minute):
    RotationSnapshot = tables_for(db).RotationSnapshot
    if state.get('status') not in ('live','ended'):return
    clean={k:v for k,v in state.items() if k not in ('initialDraft','initialSettings','confirmations','lastTick')}
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
    eng=S.E.Engine(state,settings or S.settings_of(cfg),None)
    taken=eng.targeted() if state.get('status')=='live' else {}
    persons=[S.person_view(eng,p,minute,taken) for p in state['persons'].values()]
    for p in persons:
        p.pop('confirmationTokens',None);p['canDepart']=False;p['canArrive']=False
    return {'date':work_date,'status':state['status'],'lines':S.lines_view(eng),'persons':persons,'off':state.get('off',[]),'closeAt':state.get('closeAt'),'plan':state.get('plan',{})}


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
    if latest:
        saved=json.loads(zlib.decompress(base64.b64decode(latest.payload)))
        result['day']=present(db,cfg,attraction_id,work_date,saved['state'],minute if minute is not None else latest.minute,saved['settings'])
    elif state and row.status!='draft' and minute is None:
        result['day']=present(db,cfg,attraction_id,work_date,state,float(state.get('lastTick',now)))
    return result


def slot(state,key):
    try:
        lid,num=key.split('#');i=int(num)
        line=next(l for l in state['lines'] if l['id']==lid)
        if i<0 or i>=len(line['posts']) or line.get('standby'):raise ValueError()
    except (ValueError,TypeError,AttributeError,StopIteration):raise S.ActionError('岗位不存在或不可安排')
    kind='seven' if line['posts'][i].get('seven') or key in state['plan']['seven'] else 'crew'
    return line,i,kind


def validate_plan(state):
    used=[]
    for kind in ('seven','crew'):
        for key,pid in state['plan'][kind].items():
            _,_,actual=slot(state,key)
            p=state['persons'].get(pid)
            if not p or p['role']!='rotation':raise S.ActionError('只能安排本日轮岗人员')
            if kind=='seven' and p['start']!=420:raise S.ActionError('7点岗位只能安排07:00班次人员')
            if actual!=kind:raise S.ActionError('岗位类型与安排不一致')
            used.append(pid)
    if len(used)!=len(set(used)):raise S.ActionError('同一人员不能安排两个岗位')


@S.transactional
def edit(db,attraction_id,actor,work_date,body):
    cfg=S.load_config(db,attraction_id);today,_=S.clock_now(cfg)
    if work_date<today:raise S.ActionError('往日班表只读，不能修改')
    row=S.day_row(db,attraction_id,work_date)
    revision=body.get('revision')
    if type(revision) is not int or revision!=(row.version if row else 0):raise S.ConfirmationConflict('班表已更新，请刷新后重新调整')
    action=body.get('action')
    if not isinstance(action,str):raise S.ActionError('操作必须是字符串')
    if action=='draft_generate':
        if body.get('force'):raise S.ActionError('每日班表不支持清空运行记录')
        S.do_draft_action(db,attraction_id,actor,{'action':action,'date':work_date})
        planned=S.day_row(db,attraction_id,work_date);state=S.day_state(planned);state['dailyAutoStart']=work_date>today;S.save_day(db,planned,state);return
    if not row or row.status!='draft':raise S.ActionError('仅未开始的预排可以修改，请到看板处理现场调整')
    state=S.day_state(row)
    upload=S.roster_upload_for(db,attraction_id,work_date)
    if action=='publish' and upload and state.get('rosterUploadId') and state['rosterUploadId']!=upload.id:
        raise S.ActionError('上传班表已更新，请重新生成并核对预排后启用')
    if action in ('draft_swap_people','draft_swap_posts'):
        source,target=body.get('source'),body.get('target')
        left,i,kind=slot(state,source);right,j,other=slot(state,target)
        if source==target:return
        if action=='draft_swap_posts':
            if left is not right:raise S.ActionError('岗位顺序仅可在同一条线内交换，人员可跨线交换')
            left['posts'][i],left['posts'][j]=left['posts'][j],left['posts'][i]
        if action=='draft_swap_posts':
            for k in ('seven','crew'):
                a=state['plan'][k].pop(source,None);b=state['plan'][k].pop(target,None)
                if a:state['plan'][k][target]=a
                if b:state['plan'][k][source]=b
        else:
            a=state['plan'][kind].get(source);b=state['plan'][other].get(target)
            for k in ('seven','crew'):
                state['plan'][k].pop(source,None);state['plan'][k].pop(target,None)
            if a:state['plan'][other][target]=a
            if b:state['plan'][kind][source]=b
        validate_plan(state)
        # Role changes and person swaps must not duplicate a push7 assignment.
        assigned=set(state['plan']['crew'].values())|set(state['plan']['seven'].values())
        state['plan']['push7']=[p for p in state['plan']['push7'] if p not in assigned]
        S.save_day(db,row,state)
        S.DbHooks(db,attraction_id,work_date).log(action,actor,None,None,None,{'source':source,'target':target})
        return
    allowed={'draft_set','draft_list','draft_close','draft_role','draft_discard','publish'}
    if action not in allowed:raise S.ActionError('不支持的班表操作')
    S.do_draft_action(db,attraction_id,actor,{**body,'date':work_date})
    updated=S.day_row(db,attraction_id,work_date)
    if updated and updated.status=='draft':validate_plan(S.day_state(updated))
