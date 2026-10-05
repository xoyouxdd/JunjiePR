"""轮岗接口：测试入口、模拟账号、专用账号管理。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.rotation.access import (
    ACCOUNT_COOKIE,
    KIND_LABELS,
    KIND_MEMBER,
    ROTATION_ATTRACTION_NAME,
    RotationActor,
    can_enter,
    find_rotation_account,
    landing_path,
    require_actor,
    require_entry,
    require_manager,
    require_screen,
    rotation_attraction_id,
    start_session,
    validate_account_password,
)
from app.rotation.models import RotationAccount, RotationAccountSession, RotationRosterEntry
from app.routers._shared import client_ip
from app.security import request_is_https
from app.v2_auth import V2User, current_user
from app.v2_crypto import hash_password, token_hash
from app.v2_database import get_db
from app.v2_services import write_audit


router = APIRouter(prefix="/rotation", tags=["rotation"])


def _format_time(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


@router.get("/access")
def rotation_access(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    """PR 员工账号据此决定是否显示「轮岗（测试）」入口。"""
    return {"attraction_name": ROTATION_ATTRACTION_NAME, "can_enter": can_enter(db, user)}


@router.post("/enter")
def rotation_enter(payload: dict, request: Request, response: Response, db: Session = Depends(get_db), user: V2User = Depends(require_entry)):
    """测试入口里输入模拟账号：专用账号，或模拟名单中某个 CM/TR 的工号。不需要密码。"""
    login_name = str(payload.get("account") or "").strip()
    if not login_name:
        raise HTTPException(400, "请输入模拟账号")
    account = find_rotation_account(db, login_name)
    if account:
        if not account.enabled:
            raise HTTPException(403, "该模拟账号已停用")
        start_session(db, request, response, account=account, entered_by=user.employee)
        kind, name = account.kind, account.name
    else:
        attraction_id = rotation_attraction_id(db)
        entry = (
            db.query(RotationRosterEntry)
            .filter(RotationRosterEntry.attraction_id == attraction_id, RotationRosterEntry.employee_no == login_name)
            .order_by(RotationRosterEntry.id.desc())
            .first()
        )
        if not entry:
            raise HTTPException(404, "模拟名单里没有这个工号，请先由轮岗主管上传名单")
        start_session(db, request, response, member_employee_no=login_name, entered_by=user.employee)
        kind, name = KIND_MEMBER, entry.name
    write_audit(db, user.employee, "进入轮岗测试", "rotation_account", account.id if account else None, after={"account": login_name, "kind": kind}, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "kind": kind, "name": name, "redirect": landing_path(kind)}


@router.get("/whoami")
def rotation_whoami(db: Session = Depends(get_db), actor: RotationActor = Depends(require_actor)):
    name = actor.name
    if actor.kind == KIND_MEMBER:
        entry = (
            db.query(RotationRosterEntry)
            .filter(RotationRosterEntry.employee_no == actor.member_employee_no)
            .order_by(RotationRosterEntry.id.desc())
            .first()
        )
        name = entry.name if entry else actor.member_employee_no
    return {
        "kind": actor.kind,
        "kind_label": KIND_LABELS[actor.kind],
        "name": name,
        "employee_no": actor.member_employee_no,
        "attraction_name": ROTATION_ATTRACTION_NAME,
        "test_mode": True,
    }


@router.post("/logout")
def rotation_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """退出轮岗模拟账号；PR 员工账号不受影响。"""
    raw_token = request.cookies.get(ACCOUNT_COOKIE)
    if raw_token:
        db.query(RotationAccountSession).filter(RotationAccountSession.token_hash == token_hash(raw_token)).delete(synchronize_session=False)
        db.commit()
    response.delete_cookie(ACCOUNT_COOKIE, secure=request_is_https(request), httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/screen/me")
def screen_me(actor: RotationActor = Depends(require_screen)):
    return {"name": actor.name, "attraction_name": ROTATION_ATTRACTION_NAME}


# ---------------------------------------------------------------- 专用账号管理

def _account_payload(account: RotationAccount) -> dict:
    return {
        "login_account": account.login_account,
        "kind": account.kind,
        "kind_label": KIND_LABELS.get(account.kind, account.kind),
        "name": account.name,
        "enabled": account.enabled,
        "last_login_at": _format_time(account.last_login_at),
        "password_changed_at": _format_time(account.password_changed_at),
        "locked": bool(account.locked_until and account.locked_until > datetime.now()),
    }


@router.get("/accounts")
def rotation_accounts(db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    rows = db.query(RotationAccount).order_by(RotationAccount.login_account.asc()).all()
    return {"items": [_account_payload(row) for row in rows]}


@router.post("/accounts/{login_account}/password")
def reset_rotation_account_password(
    login_account: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    actor: RotationActor = Depends(require_manager),
):
    """重置专用账号密码（用于登录页直接登录）：该账号原有的登录全部失效。"""
    password = validate_account_password(payload.get("new_password"))
    account = find_rotation_account(db, login_account)
    if not account:
        raise HTTPException(404, "账号不存在")
    account.password_hash = hash_password(password)
    account.password_changed_at = datetime.now()
    account.failed_attempts = 0
    account.locked_until = None
    db.query(RotationAccountSession).filter(RotationAccountSession.account_id == account.id).delete(synchronize_session=False)
    write_audit(
        db,
        actor.entered_by,
        f"重置{KIND_LABELS.get(account.kind, '轮岗')}密码",
        "rotation_account",
        account.id,
        after={"account": account.login_account, "operator": actor.name},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "message": f"{account.name}密码已重置，原有登录已失效"}
