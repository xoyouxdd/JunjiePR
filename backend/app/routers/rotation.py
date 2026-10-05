"""轮岗接口：身份、休息室大屏账号。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.rotation.access import (
    DEFAULT_SCREEN_ACCOUNT,
    ROTATION_ATTRACTION_NAME,
    SCREEN_COOKIE,
    ScreenUser,
    can_manage,
    can_view_self,
    current_screen,
    require_manager,
    rotation_attraction_id,
    validate_screen_password,
)
from app.rotation.models import RotationScreenAccount, RotationScreenSession
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
    """前端据此决定显示哪些轮岗入口。"""
    attraction_id = rotation_attraction_id(db)
    return {
        "attraction_name": ROTATION_ATTRACTION_NAME,
        "can_manage": can_manage(db, user, attraction_id),
        "can_view_self": can_view_self(user),
    }


# ---------------------------------------------------------------- 休息室大屏

@router.get("/screen/me")
def screen_me(screen: ScreenUser = Depends(current_screen)):
    return {"name": screen.name, "attraction_name": ROTATION_ATTRACTION_NAME}


@router.post("/screen/logout")
def screen_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get(SCREEN_COOKIE)
    if raw_token:
        db.query(RotationScreenSession).filter(RotationScreenSession.token_hash == token_hash(raw_token)).delete(synchronize_session=False)
        db.commit()
    response.delete_cookie(SCREEN_COOKIE, secure=request_is_https(request), httponly=True, samesite="lax")
    return {"ok": True}


def _screen_account(db: Session) -> RotationScreenAccount:
    account = db.query(RotationScreenAccount).filter(RotationScreenAccount.login_account == DEFAULT_SCREEN_ACCOUNT).first()
    if not account:
        raise HTTPException(404, "大屏账号不存在")
    return account


@router.get("/screen-account")
def screen_account_info(db: Session = Depends(get_db), user: V2User = Depends(require_manager)):
    account = _screen_account(db)
    return {
        "login_account": account.login_account,
        "name": account.name,
        "enabled": account.enabled,
        "last_login_at": _format_time(account.last_login_at),
        "password_changed_at": _format_time(account.password_changed_at),
        "locked": bool(account.locked_until and account.locked_until > datetime.now()),
    }


@router.post("/screen-account/password")
def reset_screen_password(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_manager)):
    """重置大屏密码：旧的大屏登录全部失效，需要在大屏上用新密码重新登录。"""
    password = validate_screen_password(payload.get("new_password"))
    account = _screen_account(db)
    account.password_hash = hash_password(password)
    account.password_changed_at = datetime.now()
    account.failed_attempts = 0
    account.locked_until = None
    db.query(RotationScreenSession).filter(RotationScreenSession.account_id == account.id).delete(synchronize_session=False)
    write_audit(db, user.employee, "重置大屏密码", "rotation_screen", account.id, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "message": "大屏密码已重置，请在大屏上用新密码重新登录"}
