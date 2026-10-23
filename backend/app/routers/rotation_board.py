"""Formal rotation: authenticated employee identity, scoped data, real clock only."""
import asyncio
from datetime import datetime
from io import BytesIO
import zipfile
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user
from app.v2_database import SessionLocal
from app.v2_models import Employee
from app.rotation import service as S
from app.rotation import daily as D
from app.rotation.access import RotationActor
from app.rotation.namespace import runtime_for
from app.rotation.roster import parse_any
from app.date_utils import parse_iso_date

router = APIRouter(prefix='/rotation-board', tags=['rotation-board'])
MANAGERS = {'SUPERVISOR', 'TA_SUPERVISOR', 'GSM', 'TA_GSM'}
ROLES = MANAGERS | {'CM', 'TR'}


def capabilities(user):
    if getattr(user,'rotation_temporary',False):
        return dict.fromkeys(('can_operate','can_manage','can_records','can_settings'),False)
    codes = set(user.role_codes)
    manage = bool(codes & MANAGERS)
    return {'can_operate': manage or 'TR' in codes, 'can_manage': manage,
            'can_records': manage, 'can_settings': manage}


def can_enter(user):
    if getattr(user,'rotation_temporary',False): return True
    return bool(set(user.role_codes) & ROLES and user.employee.attraction_id
                and user.employee.attraction and user.employee.attraction.active
                and user.employee.attraction.employee_circle)


def staff(user: V2User = Depends(current_user)):
    if not can_enter(user): raise HTTPException(403, '没有本景点圈轮岗权限')
    return user


def manager(user: V2User = Depends(staff)):
    if not capabilities(user)['can_manage']: raise HTTPException(403, '仅主管、TA主管、经理和TA经理可使用此功能')
    return user


def operator(user: V2User = Depends(staff)):
    if not capabilities(user)['can_operate']: raise HTTPException(403, 'CM只可查看看板并确认自己的待办')
    return user


def formal_db():
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        yield db


def actor(user, *, personal=False):
    return RotationActor('member' if personal else 'supervisor', user.name,
                         member_employee_no=user.employee.employee_no,
                         entered_by=user.employee, formal=True, temporary=getattr(user,'rotation_temporary',False))


def identity(user):
    return {'kind':'supervisor' if capabilities(user)['can_operate'] else 'member',
            'kind_label':user.display_role_name,'name':user.name,'employee_no':user.employee.employee_no,
            'attraction_name':user.employee.attraction.name}


def call(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except S.ActionError as exc:
        raise HTTPException(409 if isinstance(exc, S.ConfirmationConflict) else 400, str(exc)) from exc


@router.get('/whoami')
def whoami(user: V2User = Depends(staff)):
    return {'kind': 'supervisor' if capabilities(user)['can_operate'] else 'member',
            'kind_label': user.display_role_name, 'name': user.name, 'employee_no': user.employee.employee_no,
            'attraction_name': user.employee.attraction.name, 'test_mode': False, 'formal': True,
            'adjustment': False, 'can_authorize': False, 'capabilities': capabilities(user)}


@router.get('/board')
def board(db: Session = Depends(formal_db), user: V2User = Depends(staff)):
    S.tick_attraction(user.employee.attraction_id, formal=True)
    data = call(S.board_payload, db, user.employee.attraction_id)
    data.update(capabilities=capabilities(user),identity=identity(user))
    if not capabilities(user)['can_manage']:
        for key in ('roster','rosterDates','days','draft'): data.pop(key, None)
        data['settings']={'departEarly':data['settings']['departEarly']}
    if data['day'] and not capabilities(user)['can_operate']:
        for p in data['day']['persons']: p.pop('confirmationTokens', None)
    return data


@router.get('/me')
def me(db: Session = Depends(formal_db), user: V2User = Depends(staff)):
    S.tick_attraction(user.employee.attraction_id, formal=True)
    with S.transaction(db):
        data=call(S.member_payload, db, user.employee.attraction_id, user.employee.employee_no)
        data.update(capabilities=capabilities(user),identity=identity(user))
        data.pop('week_minutes',None)
        data.pop('today',None)
        if data.get('line'):
            row=S.day_row(db,user.employee.attraction_id,data['clock']['date'])
            state=S.day_state(row)
            eng,_=S.engine_for(db,S.load_config(db,user.employee.attraction_id),row,state)
            data['line']=next((line for line in S.lines_view(eng) if line['id']==data['line']['id']),None)
    return data


@router.post('/me/act')
def me_act(payload: dict, request: Request, db: Session = Depends(formal_db), user: V2User = Depends(staff)):
    if payload.get('pid',user.employee.employee_no) != user.employee.employee_no:
        raise HTTPException(403, '只能确认本人待办')
    if payload.get('action') not in ('arrive','depart','entry_return','entry_adjust'): raise HTTPException(403, '只可确认本人轮岗待办')
    if not payload.get('confirmation_token'): raise HTTPException(400, '请刷新待办后重新确认')
    with runtime_for(db).lock:
        try:
            if getattr(user,'rotation_temporary',False):
                from sqlalchemy import text
                from app.rotation.temporary import current
                db.rollback();db.execute(text('BEGIN IMMEDIATE'))
                user=current(db,request)
            return call(S.do_live_action, db, user.employee.attraction_id, actor(user, personal=True),
                        {**payload, 'pid': user.employee.employee_no})
        except Exception:
            db.rollback()
            raise


@router.post('/act')
def act(payload: dict, db: Session = Depends(formal_db), user: V2User = Depends(operator)):
    if not capabilities(user)['can_manage'] and payload.get('action') in ('add_person','set_close'):
        raise HTTPException(403, '此操作需要主管或经理权限')
    if payload.get('action') in ('arrive','depart','entry_return','entry_adjust') and not payload.get('confirmation_token'):
        raise HTTPException(400, '请刷新看板后重新确认')
    if payload.get('action') in ('post','move','post_order') and not payload.get('expected_snapshot'):
        raise HTTPException(400,'请先预览当日调整影响')
    return call(S.do_live_action, db, user.employee.attraction_id, actor(user), payload)


@router.post('/me/entry-preview')
def entry_preview(payload: dict, db: Session = Depends(formal_db), user: V2User = Depends(staff)):
    from app.rotation.cooperation import adjustment
    S.tick_attraction(user.employee.attraction_id, formal=True)
    with S.transaction(db):
        cfg=S.load_config(db,user.employee.attraction_id)
        day,now=S.clock_now(cfg)
        row=S.day_row(db,user.employee.attraction_id,day)
        if not row or row.status!='live': raise HTTPException(400,'当日轮岗尚未运行')
        eng,_=S.engine_for(db,cfg,row,S.day_state(row))
        p=eng.P.get(user.employee.employee_no)
        if not p: raise HTTPException(400,'当日名单没有本人')
        if payload.get('confirmation_token')!=S.confirmation_token(eng,p,'entry_adjust'):
            raise HTTPException(409,'同行任务已变化，请刷新')
        return call(adjustment,eng,p['pid'],now,payload.get('missing'))


@router.post('/adjust/preview')
def preview(payload: dict,db: Session = Depends(formal_db),user: V2User = Depends(operator)):
    from app.rotation.manual import preview as preview_change
    return call(preview_change,db,user.employee.attraction_id,payload)


@router.post('/adjust/act')
def execute_preview(payload: dict,db: Session = Depends(formal_db),user: V2User = Depends(operator)):
    if not payload.get('expected_snapshot') or payload.get('action') not in ('post','move','post_order'):
        raise HTTPException(400,'请先预览当日调整影响')
    call(S.do_live_action,db,user.employee.attraction_id,actor(user),payload)
    return {'ok':True}


@router.get('/confirmation')
def confirmation(token: str, date: str, db: Session = Depends(formal_db), user: V2User = Depends(staff)):
    if len(token)!=64: raise HTTPException(400, '确认标识无效')
    if parse_iso_date(date).isoformat()!=datetime.now().date().isoformat(): raise HTTPException(400, '仅可核对当日确认')
    return call(S.confirmation_status, db, user.employee.attraction_id, date, token,
                None if capabilities(user)['can_operate'] else user.employee.employee_no)


@router.get('/daily')
def month(month: str, db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    return call(D.month_payload, db, user.employee.attraction_id, month)


@router.get('/daily/{day}')
def daily(day: str, at: str | None = None, db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    return call(D.day_payload, db, user.employee.attraction_id, parse_iso_date(day).isoformat(), at)


@router.post('/daily/{day}')
def daily_edit(day: str, payload: dict, db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    call(D.edit, db, user.employee.attraction_id, actor(user), parse_iso_date(day).isoformat(), payload)
    return {'ok': True}


@router.post('/draft')
def draft(payload: dict, db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    if payload.get('force') or payload.get('restore_initial'): raise HTTPException(403, '正式轮岗不支持测试重置')
    raise HTTPException(400, '请通过每日班表并携带版本调整预排')


@router.get('/config')
def config(db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    with S.transaction(db):
        cfg=S.load_config(db,user.employee.attraction_id)
        return {'lines':S.lines_of(cfg),'settings':S.settings_of(cfg),'defaults':S.E.DEFAULT_SETTINGS}


@router.put('/config')
def save_config(payload: dict, db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    try:
        with S.transaction(db):
            cfg=S.load_config(db,user.employee.attraction_id)
            S.update_config(cfg,payload.get('lines'),payload.get('settings'),user.id)
            S.mark_changed(db)
    except S.ActionError as exc: raise HTTPException(400,str(exc)) from exc
    return {'ok':True}


@router.get('/roster')
def roster(date: str, db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    return S.roster_summary(db,user.employee.attraction_id,parse_iso_date(date).isoformat())


@router.post('/roster/upload')
async def upload(file: UploadFile = File(...), scope: str = Form('week'), date: str = Form(''),
                 preview: bool = Form(False), expected_digest: str = Form(''),
                 db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    if not (file.filename or '').lower().endswith('.xlsx'): raise HTTPException(400,'请上传xlsx班表')
    content=await file.read(10*1024*1024+1)
    if not content or len(content)>10*1024*1024: raise HTTPException(400,'班表不能为空且不能超过10MB')
    try: parsed=parse_any(BytesIO(content))
    except (ValueError,KeyError,StopIteration,zipfile.BadZipFile) as exc: raise HTTPException(400,'班表无法识别') from exc
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_save_uploaded_roster, parsed, scope, date, preview, expected_digest, file.filename, db, user)


def _save_uploaded_roster(parsed, scope, date, preview, expected_digest, filename, db, user):
    from app.rotation.temporary import preflight, provision
    from app.v2_crypto import hash_password
    preliminary=preflight(db,parsed,user.employee.attraction_id,scope,date or None)
    if preview: return {'ok':True,'preview':True,**preliminary}
    if expected_digest and expected_digest!=preliminary['digest']:
        raise HTTPException(409,'班表或人员身份已变化，请重新预检')
    if preliminary['new'] and not expected_digest:
        raise HTTPException(409,'含未注册人员，请预检并确认创建轮岗临时账号')
    # Password derivation is intentionally outside the rotation/database write locks.
    db.rollback()
    passwords={p['employee_no']:hash_password(p['employee_no'][-4:]) for p in preliminary['new']}
    with runtime_for(db).lock:
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        try:
            # A writer reservation serializes roster provisioning against HR registration.
            db.rollback()
            db.execute(text('BEGIN IMMEDIATE'))
            batch=preflight(db,parsed,user.employee.attraction_id,scope,date or None)
            if preliminary['digest']!=batch['digest']:
                raise HTTPException(409,'班表或人员身份已变化，请重新预检')
            with S.transaction(db):
                provision(db,parsed,user.employee.attraction_id,batch,passwords)
                row=call(S.save_roster,db,user.employee.attraction_id,parsed,scope=scope,file_name=filename,
                         uploader_id=user.id,uploader_name=user.name,only_date=date or None)
            return {'ok':True,'scope':row.scope,'start_date':row.start_date,'end_date':row.end_date,
                    'entry_count':row.entry_count,'temporary_created':len(batch['new'])}
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409,'人员身份刚刚变化，请重新预检班表') from exc
        except Exception:
            db.rollback()
            raise


@router.get('/log')
def log(date: str, db: Session = Depends(formal_db), user: V2User = Depends(manager)):
    return {'items':S.day_log(db,user.employee.attraction_id,parse_iso_date(date).isoformat())}


@router.get('/person')
def person(employee_no: str,date: str,db: Session = Depends(formal_db),user: V2User = Depends(manager)):
    return S.person_record(db,user.employee.attraction_id,employee_no,parse_iso_date(date).isoformat())


@router.get('/stream')
async def stream(request: Request):
    from starlette.concurrency import run_in_threadpool

    def check_access():
        # Release each short authentication session before yielding a long-lived stream.
        with SessionLocal() as db:
            fresh=current_user(request,db)
            if not can_enter(fresh): raise HTTPException(403,'没有轮岗权限')
            return fresh.id,fresh.employee.attraction_id

    identity=await run_in_threadpool(check_access)
    async def events():
        last=None;idle=0
        while not await request.is_disconnected():
            try:
                if await run_in_threadpool(check_access)!=identity: break
            except HTTPException: break
            version=runtime_for(formal=True).version
            if last!=version: last=version;idle=0;yield f'data: {version}\n\n'
            elif idle>=15: idle=0;yield ': ping\n\n'
            await asyncio.sleep(1);idle+=1
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
