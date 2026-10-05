"""轮岗接口：身份、轮岗专用账号。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.rotation.access import (
    ACCOUNT_COOKIE,
    KIND_LABELS,
    ROTATION_ATTRACTION_NAME,
    RotationActor,
    can_manage,
    can_view_self,
    require_manager,
    require_screen,
    validate_account_password,
)
from app.rotation.models import RotationAccount, RotationAccountSession
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
def rotation_access(user: V2User = Depends(current_user)):
    """PR 员工账号据此决定显示哪些轮岗入口。"""
    return {
        "attraction_name": ROTATION_ATTRACTION_NAME,
        "can_manage": can_manage(user),
        "can_view_self": can_view_self(user),
    }


@router.get("/whoami")
def rotation_whoami(actor: RotationActor = Depends(require_manager)):
    """轮岗看板页面的当前操作人。"""
    return {"kind": actor.kind, "name": actor.name, "attraction_name": ROTATION_ATTRACTION_NAME}


@router.post("/logout")
def rotation_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """退出轮岗专用账号；员工账号仍用 /api/logout。"""
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
    """重置专用账号密码：该账号原有的登录全部失效。"""
    password = validate_account_password(payload.get("new_password"))
    account = db.query(RotationAccount).filter(RotationAccount.login_account == login_account).first()
    if not account:
        raise HTTPException(404, "账号不存在")
    account.password_hash = hash_password(password)
    account.password_changed_at = datetime.now()
    account.failed_attempts = 0
    account.locked_until = None
    db.query(RotationAccountSession).filter(RotationAccountSession.account_id == account.id).delete(synchronize_session=False)
    write_audit(
        db,
        actor.employee,
        f"重置{KIND_LABELS.get(account.kind, '轮岗')}密码",
        "rotation_account",
        account.id,
        after={"account": account.login_account, "operator": actor.name, "operator_kind": actor.kind},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "message": f"{account.name}密码已重置，原有登录已失效"}
