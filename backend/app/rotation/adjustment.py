"""Employee-bound room adjustment sessions; grants use wall time, never test time."""
from datetime import date, datetime, timedelta
import hashlib
import hmac
import secrets
from fastapi import HTTPException
from sqlalchemy import or_
from app.database.connection import DATA_DIR
from app.rotation.models import RotationAssistGrant, RotationAdjustSession
from app.v2_models import Employee, UserAccount, EmployeeLOAPeriod
from app.v2_crypto import new_session_token, token_hash, verify_password
from app.v2_services import base_role_at, duties_at, managed_attraction_ids, write_audit
from app.security import request_is_https
from app.employee_numbers import employee_number_aliases

COOKIE = "rc_rotation_adjust"
MANAGERS = {"TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "AM"}
MINUTES = {5, 10, 15, 20, 25, 30, 35, 40, 45}

def qualified(db, employee, *, trainer=False):
    from app.rotation.access import rotation_attraction_id
    if not employee or not employee.is_active:
        return False
    today = date.today().isoformat()
    account = db.query(UserAccount).filter_by(employee_id=employee.id, enabled=True).first()
    if not account or account.must_change_password or employee.hired_on and employee.hired_on > today or employee.terminated_on and employee.terminated_on <= today:
        return False
    if db.query(EmployeeLOAPeriod).filter(EmployeeLOAPeriod.employee_id==employee.id, EmployeeLOAPeriod.status=='active', EmployeeLOAPeriod.starts_on<=today, or_(EmployeeLOAPeriod.ends_on.is_(None),EmployeeLOAPeriod.ends_on>=today)).first():
        return False
    base = base_role_at(db, employee.id)
    codes = {base.code if base else '', *(r.code for r in duties_at(db, employee.id))}
    circle = rotation_attraction_id(db)
    if trainer:
        return bool(base and base.code=='TR' and employee.attraction_id==circle)
    return bool(codes & MANAGERS and (employee.attraction_id==circle or codes & {'TA_GSM','GSM','AM'} and circle in managed_attraction_ids(db, employee.id)))

def grant_code(grant):
    path = DATA_DIR / 'rotation-assist.key'
    if not path.exists():
        try:
            with path.open('xb') as handle:
                handle.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
    raw = hmac.new(path.read_bytes(),f'{grant.id}:{grant.employee_id}:{grant.issued_at.isoformat()}'.encode(),hashlib.sha256).digest()
    return f'{int.from_bytes(raw[:8],"big") % 10**12:012d}'

def live_grant(db, grant):
    return bool(grant and not grant.revoked and grant.expires_at>datetime.now() and qualified(db,db.get(Employee,grant.employee_id),trainer=True))

def actor_info(actor):
    return {'name':actor.name,'employee_no':actor.entered_by.employee_no if actor.entered_by else None,
            'temporary':actor.temporary,'expires_at':actor.expires_at.isoformat() if actor.expires_at else None,
            'can_authorize':actor.can_authorize,'adjustment':actor.adjustment}

def staff_actor(request, db):
    from app.rotation.access import RotationActor
    raw=request.cookies.get(COOKIE)
    row=db.query(RotationAdjustSession).filter_by(token_hash=token_hash(raw)).first() if raw else None
    if not row or row.expires_at<=datetime.now():
        return None
    employee=db.get(Employee,row.employee_id)
    grant=db.get(RotationAssistGrant,row.grant_id) if row.grant_id else None
    if row.grant_id:
        if not live_grant(db,grant):
            return None
    elif not qualified(db,employee):
        return None
    return RotationActor('supervisor',employee.name+(' · TR临时授权' if grant else ' · 现场调整'),entered_by=employee,
                         adjustment=True,temporary=bool(grant),expires_at=min(row.expires_at,grant.expires_at) if grant else row.expires_at,can_authorize=not bool(grant),adjustment_session_id=row.id)

def start_staff(db, request, response, employee, grant=None):
    old=request.cookies.get(COOKIE)
    if old:
        db.query(RotationAdjustSession).filter_by(token_hash=token_hash(old)).delete()
    raw=new_session_token();expiry=grant.expires_at if grant else datetime.now()+timedelta(minutes=30)
    db.add(RotationAdjustSession(employee_id=employee.id,grant_id=grant.id if grant else None,token_hash=token_hash(raw),expires_at=expiry))
    response.set_cookie(COOKIE,raw,httponly=True,samesite='lax',secure=request_is_https(request),max_age=max(1,int((expiry-datetime.now()).total_seconds())))
    write_audit(db,employee,'轮岗调整登录','rotation_adjust_session',after={'temporary':bool(grant),'grant_id':grant.id if grant else None})
    db.commit()
    return {'ok':True,'name':employee.name,'temporary':bool(grant),'expires_at':expiry.isoformat(),'can_authorize':not bool(grant)}

def login_staff(db,request,response,body):
    logins=employee_number_aliases(body.get('account'));credential=str(body.get('credential') or '')
    accounts=db.query(UserAccount).join(Employee,Employee.id==UserAccount.employee_id).filter(or_(UserAccount.login_account.in_(logins),Employee.employee_no.in_(logins))).limit(2).all()
    account=accounts[0] if len(accounts)==1 else None
    if not account or not account.enabled:
        raise HTTPException(401,'账号或凭据不正确')
    if account.locked_until and account.locked_until>datetime.now():
        raise HTTPException(423,'登录失败次数过多，请稍后再试')
    employee=db.get(Employee,account.employee_id);grant=None
    if body.get('mode')=='tr':
        candidates=db.query(RotationAssistGrant).filter_by(employee_id=employee.id,revoked=False).filter(RotationAssistGrant.expires_at>datetime.now()).all()
        grant=next((g for g in candidates if hmac.compare_digest(grant_code(g),credential)),None)
        valid=bool(grant and live_grant(db,grant))
    else:
        valid=verify_password(credential,account.password_hash) and qualified(db,employee)
    if not valid:
        account.failed_attempts+=1
        if account.failed_attempts>=5:
            account.failed_attempts=0;account.locked_until=datetime.now()+timedelta(minutes=15)
        db.commit();raise HTTPException(401,'账号或凭据不正确，或没有本圈调整权限')
    account.failed_attempts=0;account.locked_until=None
    return start_staff(db,request,response,employee,grant)

def mine(db, employee):
    if not qualified(db,employee,trainer=True):
        return []
    return [{'id':g.id,'code':grant_code(g),'issuer':g.issuer_name,'expires_at':g.expires_at.isoformat()} for g in db.query(RotationAssistGrant).filter_by(employee_id=employee.id,revoked=False).filter(RotationAssistGrant.expires_at>datetime.now()).order_by(RotationAssistGrant.id.desc())]


def revalidate_actor(db, actor):
    if not getattr(actor, 'adjustment', False):
        return
    row=db.get(RotationAdjustSession,actor.adjustment_session_id,populate_existing=True)
    grant=db.get(RotationAssistGrant,row.grant_id,populate_existing=True) if row and row.grant_id else None
    if not row or row.expires_at<=datetime.now() or (row.grant_id and not live_grant(db,grant)) or (not row.grant_id and not qualified(db,db.get(Employee,row.employee_id))):
        raise HTTPException(401,'调整授权已到期或撤销，请退出调整')
