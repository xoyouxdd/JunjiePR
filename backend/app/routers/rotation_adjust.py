from datetime import datetime, timedelta
from functools import wraps
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from app.rotation import adjustment as auth, manual, service
from app.rotation.access import require_manager, require_actor, rotation_attraction_id, session_actor
from app.rotation.models import RotationAssistGrant, RotationAdjustSession
from app.v2_database import get_db
from app.v2_auth import current_user
from app.v2_models import Employee
from app.v2_crypto import token_hash
from app.security import request_is_https
from app.v2_services import write_audit

router=APIRouter(prefix='/adjust')

def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with service.transaction(kwargs['db']):
            return fn(*args, **kwargs)
    return wrapped

@router.post('/login')
def login(body:dict,request:Request,response:Response,db:Session=Depends(get_db)):
    if not session_actor(request,db):
        raise HTTPException(401,'请先进入轮岗或登录休息室大屏')
    return auth.login_staff(db,request,response,body)

@router.get('/status')
def status(request:Request,db:Session=Depends(get_db)):
    if not session_actor(request,db):
        raise HTTPException(401,'请先进入轮岗')
    actor=auth.staff_actor(request,db)
    return {'active':bool(actor),**(auth.actor_info(actor) if actor else {})}

@router.post('/logout')
def logout(request:Request,response:Response,db:Session=Depends(get_db)):
    actor=auth.staff_actor(request,db);raw=request.cookies.get(auth.COOKIE)
    if raw:
        db.query(RotationAdjustSession).filter_by(token_hash=token_hash(raw)).delete()
        if actor:write_audit(db,actor.entered_by,'轮岗退出调整','rotation_adjust_session')
        db.commit()
    response.delete_cookie(auth.COOKIE,httponly=True,samesite='lax',secure=request_is_https(request))
    return {'ok':True}

@router.get('/mine')
def mine(db:Session=Depends(get_db),user=Depends(current_user)):
    return {'items':auth.mine(db,user.employee)}

@router.get('/grants')
def grants(db:Session=Depends(get_db),actor=Depends(require_manager)):
    auth.revalidate_actor(db,actor)
    if not actor.can_authorize:raise HTTPException(403,'临时授权TR不能再授权')
    employees=[e for e in db.query(Employee).order_by(Employee.employee_no) if auth.qualified(db,e,trainer=True)]
    rows=db.query(RotationAssistGrant).filter_by(attraction_id=rotation_attraction_id(db),revoked=False).filter(RotationAssistGrant.expires_at>datetime.now()).all()
    return {'trainers':[{'id':e.id,'name':e.name,'employee_no':e.employee_no} for e in employees],
            'items':[{'id':g.id,'employee_id':g.employee_id,'name':db.get(Employee,g.employee_id).name,'expires_at':g.expires_at.isoformat(),'issuer':g.issuer_name} for g in rows]}

@router.post('/grants')
@serialized
def issue(body:dict,db:Session=Depends(get_db),actor=Depends(require_manager)):
    auth.revalidate_actor(db,actor)
    if not actor.can_authorize:raise HTTPException(403,'临时授权TR不能再授权')
    minutes=body.get('minutes')
    if type(minutes)!=int or minutes not in auth.MINUTES:raise HTTPException(400,'授权时长仅支持5、10及15至45分钟（每5分钟）')
    ids=body.get('employee_ids',[])
    if not isinstance(ids,list) or any(type(i)!=int for i in ids):raise HTTPException(400,'培训师选择不正确')
    eligible=[e for e in db.query(Employee).order_by(Employee.employee_no) if auth.qualified(db,e,trainer=True)]
    chosen=eligible if body.get('all') is True else [e for e in eligible if e.id in ids]
    if not chosen or body.get('all') is not True and set(ids)!={e.id for e in chosen}:raise HTTPException(400,'请选择本圈有效TR')
    now=datetime.now();out=[]
    for employee in chosen:
        db.query(RotationAssistGrant).filter_by(employee_id=employee.id,revoked=False).update({'revoked':True})
        g=RotationAssistGrant(employee_id=employee.id,issuer_id=actor.entered_by.id if actor.entered_by else None,issuer_name=actor.name,attraction_id=rotation_attraction_id(db),issued_at=now,expires_at=now+timedelta(minutes=minutes))
        db.add(g);db.flush();out.append({'id':g.id,'name':employee.name,'expires_at':g.expires_at.isoformat()})
    write_audit(db,actor.entered_by,'轮岗TR临时授权','rotation_assist_grant',after={'employee_ids':[e.id for e in chosen],'minutes':minutes,'operator':actor.name})
    db.commit();return {'ok':True,'items':out}

@router.post('/grants/{grant_id}/revoke')
@serialized
def revoke(grant_id:int,db:Session=Depends(get_db),actor=Depends(require_manager)):
    auth.revalidate_actor(db,actor)
    if not actor.can_authorize:raise HTTPException(403,'临时授权TR不能再授权')
    grant=db.get(RotationAssistGrant,grant_id)
    if not grant or grant.attraction_id!=rotation_attraction_id(db):raise HTTPException(404,'授权不存在')
    grant.revoked=True;write_audit(db,actor.entered_by,'撤销轮岗TR授权','rotation_assist_grant',grant.id);db.commit()
    return {'ok':True}

@router.post('/preview')
def preview(body:dict,db:Session=Depends(get_db),actor=Depends(require_manager)):
    try:return manual.preview(db,rotation_attraction_id(db),body)
    except (service.ActionError,KeyError) as exc:raise HTTPException(400,str(exc)) from exc

@router.post('/act')
def act(body:dict,db:Session=Depends(get_db),actor=Depends(require_manager)):
    if not body.get('expected_snapshot'):raise HTTPException(400,'请先预览调整影响')
    if body.get('action') not in ('post','move','post_order'):raise HTTPException(400,'不支持该现场调整')
    try:service.do_live_action(db,rotation_attraction_id(db),actor,body)
    except service.ActionError as exc:raise HTTPException(409 if isinstance(exc,service.ConfirmationConflict) else 400,str(exc)) from exc
    return {'ok':True}
