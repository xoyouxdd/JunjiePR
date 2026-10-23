"""Rotation-only identities. Never create an Employee, role or workbench account."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base
from app.v2_crypto import hash_password, verify_password, new_session_token, token_hash
from app.v2_models import Employee, UserAccount, EmployeeNumberHistory, Attraction
from app.rotation.roster import classify
from app.security import request_is_https

COOKIE = 'rc_rotation_temporary'


class TemporaryMember(Base):
    __tablename__ = 'formal_rotation_temporary_members'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_no: Mapped[str] = mapped_column(String(7), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    attraction_id: Mapped[int] = mapped_column(ForeignKey('attractions.id'), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    adopted_employee_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TemporarySession(Base):
    __tablename__ = 'formal_rotation_temporary_sessions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey('formal_rotation_temporary_members.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


def pending(db, no, name, attraction_id):
    """Validate a formal-account takeover; caller commits it with employee creation."""
    member = db.query(TemporaryMember).filter_by(employee_no=no).first()
    if member and not member.adopted_at:
        if member.name != name.strip() or member.attraction_id != attraction_id:
            raise HTTPException(409, '该工号已有轮岗临时身份，姓名或景点圈不一致，请先核对同一人员')
        return member
    return None


def adopt(db, employee):
    member = pending(db, employee.employee_no, employee.name, employee.attraction_id)
    if member:
        member.adopted_at = datetime.now()
        member.adopted_employee_id = employee.id
        member.enabled = False
        db.query(TemporarySession).filter_by(member_id=member.id).delete(synchronize_session=False)
        from app.v2_services import write_audit
        write_audit(db, employee, '正式账号接管轮岗临时身份', 'employee', employee.id,
                    after={'temporary_member_id':member.id, 'history_retained':True})


def preflight(db, parsed, attraction_id, scope, only_date):
    dates = sorted(parsed['days'])
    if scope not in ('week', 'day'): raise HTTPException(400, '上传范围只能是按周或按天')
    if scope == 'day':
        selected = only_date or (dates[0] if len(dates)==1 else None)
        if not selected or selected not in dates: raise HTTPException(400, '按天上传请选择名单中存在的日期')
        dates = [selected]
    if not dates: raise HTTPException(400, '名单里没有任何日期')
    numbers = sorted({no for d in dates for no in parsed['days'][d]})
    if not numbers: raise HTTPException(400, '名单没有人员')
    existing = {e.employee_no:e for e in db.query(Employee).filter(Employee.employee_no.in_(numbers))}
    accounts = {a.employee_id:a for a in db.query(UserAccount).filter(UserAccount.employee_id.in_([e.id for e in existing.values()]))}
    temporary = {m.employee_no:m for m in db.query(TemporaryMember).filter(TemporaryMember.employee_no.in_(numbers))}
    blocked = {r.old_employee_no for r in db.query(EmployeeNumberHistory).filter(EmployeeNumberHistory.old_employee_no.in_(numbers))}
    from app.rotation.models import RotationAccount
    reserved = {r.login_account for r in db.query(RotationAccount).filter(RotationAccount.login_account.in_(numbers))}
    reserved |= {a.login_account for a in db.query(UserAccount).filter(UserAccount.login_account.in_(numbers))}
    new=[];reuse=[];signature=[]
    for no in numbers:
        person=parsed['people'].get(no,{})
        name=str(person.get('name') or '').strip()
        if not re.fullmatch(r'[0-9]{7}',no) or not name or len(name)>100:
            raise HTTPException(400,'班表工号须为7位数字，姓名不能为空或超过100字')
        e=existing.get(no); m=temporary.get(no)
        if e:
            account=accounts.get(e.id)
            if e.attraction_id!=attraction_id or not e.is_active or e.account_deleted_at or not account or not account.enabled:
                raise HTTPException(400,f'工号{no}不是本圈可用正式账号，请先核对员工状态')
            from app.services.employee_types import is_due
            if is_due(e.employee_type,e.internship_ends_on): raise HTTPException(400,f'工号{no}实习已到期，请先核对员工状态')
            # Preserve existing roster behavior: formal identities use their canonical name.
            person['name']=e.name
            signature.append([no,e.id,e.name,e.attraction_id,account.enabled])
        else:
            if no in blocked or no in reserved or m and (m.attraction_id!=attraction_id or m.adopted_at):
                raise HTTPException(409,f'工号{no}已被占用或属于历史身份，请核对')
            if m and m.name!=name: raise HTTPException(409,f'工号{no}与临时身份姓名不一致，请核对')
            (reuse if m else new).append({'employee_no':no,'name':name})
            signature.append([no,m.id if m else None,name,m.attraction_id if m else attraction_id, bool(m.enabled) if m else None])
    digest=hashlib.sha256(json.dumps([attraction_id,scope,dates,parsed,signature],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'new':new,'reuse':reuse,'dates':dates,'digest':digest}


def provision(db, parsed, attraction_id, batch, passwords):
    for person in batch['new']+batch['reuse']:
        no=person['employee_no']
        ends=[]
        for day in batch['dates']:
            if no not in parsed['days'][day]: continue
            info=classify(parsed['days'][day][no])
            ends.append(datetime.fromisoformat(day)+timedelta(minutes=max(1440,info.get('end',1440))))
        until=max(ends)
        member=db.query(TemporaryMember).filter_by(employee_no=no).first()
        if member:
            member.valid_until=max(member.valid_until,until)
        else:
            db.add(TemporaryMember(employee_no=no,name=person['name'],attraction_id=attraction_id,
                                   password_hash=passwords[no],valid_until=until,enabled=True))
    db.flush()


def login(db, request, response, member, password):
    now=datetime.now()
    if not member.enabled or member.adopted_at or member.valid_until<=now:
        raise HTTPException(401,'临时轮岗账号已失效，请联系主管核对班表')
    if member.locked_until and member.locked_until>now: raise HTTPException(423,'登录失败次数过多，请稍后再试')
    if not verify_password(password,member.password_hash):
        member.failed_attempts+=1
        if member.failed_attempts>=5:
            member.failed_attempts=0;member.locked_until=now+timedelta(minutes=15)
        db.commit()
        raise HTTPException(401,'账号或密码/PIN不正确')
    member.failed_attempts=0;member.locked_until=None
    raw=new_session_token()
    db.add(TemporarySession(member_id=member.id,token_hash=token_hash(raw),expires_at=min(now+timedelta(hours=12),member.valid_until)))
    db.commit()
    response.delete_cookie('rc_v2_session',secure=request_is_https(request),httponly=True,samesite='lax')
    for cookie in ('rc_rotation_account','rc_rotation_adjust'):
        response.delete_cookie(cookie,secure=request_is_https(request),httponly=True,samesite='lax')
    response.set_cookie(COOKIE,raw,httponly=True,samesite='lax',secure=request_is_https(request),max_age=12*3600)
    return {'ok':True,'rotation_temporary':True}


def current(db, request):
    raw=request.cookies.get(COOKIE)
    session=db.query(TemporarySession).filter_by(token_hash=token_hash(raw)).first() if raw else None
    member=db.get(TemporaryMember,session.member_id) if session else None
    now=datetime.now()
    if not session or session.expires_at<=now or not member or not member.enabled or member.adopted_at or member.valid_until<=now:
        raise HTTPException(401,'请重新登录')
    if db.query(Employee.id).filter_by(employee_no=member.employee_no).first(): raise HTTPException(401,'账号已更新，请重新登录')
    allowed={('GET','/api/me'),('GET','/api/rotation-board/me'),('POST','/api/rotation-board/me/act'),
             ('POST','/api/rotation-board/me/entry-preview'),('GET','/api/rotation-board/confirmation')}
    if (request.method,request.url.path) not in allowed: raise HTTPException(403,'此账号仅可使用本人轮岗待办')
    circle=db.get(Attraction,member.attraction_id)
    if not circle or not circle.active or not circle.employee_circle: raise HTTPException(403,'景点圈不可用')
    return SimpleNamespace(id=-member.id,name=member.name,rotation_temporary=True,role_codes=set(),
        display_role_name='轮岗人员',employee=SimpleNamespace(id=None,employee_no=member.employee_no,
        name=member.name,attraction_id=member.attraction_id,attraction=circle),permissions=set())


def was_temporary(db, attraction_id, no, at):
    member=db.query(TemporaryMember).filter_by(employee_no=no,attraction_id=attraction_id).first()
    return bool(member and member.created_at<=at and (not member.adopted_at or at<member.adopted_at))
