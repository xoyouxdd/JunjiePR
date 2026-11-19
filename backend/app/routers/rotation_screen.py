"""Formal rotation for a rest-room display authenticated as a screen device."""
from __future__ import annotations

import asyncio
from datetime import datetime
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.connection import SessionLocal, get_db
from app.http_utils import client_ip
from app.rotation import service
from app.rotation.access import (
    ACCOUNT_COOKIE,
    KIND_SCREEN,
    SCREEN_LABEL,
    ROTATION_ATTRACTION_NAME,
    RotationActor,
    require_screen_device,
    rotation_attraction_id,
    validate_account_password,
)
from app.rotation.models import RotationAccount, RotationAccountSession
from app.rotation.namespace import runtime_for
from app.security import request_is_https
from app.services.audit import write_audit
from app.services.organization import managed_attraction_ids
from app.v2_auth import V2User, current_user
from app.v2_crypto import hash_password, token_hash


router = APIRouter(prefix="/rotation-screen", tags=["rotation-screen"])


def _action_error(exc: service.ActionError) -> HTTPException:
    return HTTPException(409 if isinstance(exc, service.ConfirmationConflict) else 400, str(exc))


@router.get("/me")
def me(actor: RotationActor = Depends(require_screen_device)):
    return {"name": actor.name, "attraction_name": ROTATION_ATTRACTION_NAME}


@router.get("/state", dependencies=[Depends(require_screen_device)])
def state(db: Session = Depends(get_db)):
    attraction_id = rotation_attraction_id(db)
    db.rollback()
    service.tick_attraction(attraction_id, formal=True)
    return {**service.screen_payload(db, attraction_id), "formal": True}


_ACTION_FIELDS = frozenset({
    "action", "pid", "confirmation_token", "meal_choice", "expected_line",
    "expected_push7", "expected_kind", "expected_arrival_outcome",
})


@router.post("/act")
def act(payload: dict, db: Session = Depends(get_db), actor: RotationActor = Depends(require_screen_device)):
    action = payload.get("action")
    if not isinstance(action, str):
        raise HTTPException(400, "操作类型必须是字符串")
    if action not in service.SCREEN_ACTIONS:
        raise HTTPException(403, "大屏只能确认班前已到、去休息或去轮岗")
    if not payload.get("confirmation_token"):
        raise HTTPException(400, "请刷新后重新确认")
    body = {key: value for key, value in payload.items() if key in _ACTION_FIELDS}
    try:
        return service.do_live_action(db, rotation_attraction_id(db), actor, body)
    except service.ActionError as exc:
        raise _action_error(exc) from exc


@router.get("/confirmation", dependencies=[Depends(require_screen_device)])
def confirmation(token: str, date: str, db: Session = Depends(get_db)):
    if len(token) != 64:
        raise HTTPException(400, "确认任务无效")
    try:
        return service.confirmation_status(db, rotation_attraction_id(db), date, token, None)
    except service.ActionError as exc:
        raise _action_error(exc) from exc


@router.get("/stream")
async def stream(request: Request):
    """Use short device authentication sessions, including on revalidation."""
    def check_access():
        with SessionLocal() as db:
            actor = require_screen_device(request, db)
            return actor.actor_id

    identity = await run_in_threadpool(check_access)
    runtime = runtime_for(formal=True)

    async def events():
        last, idle = None, 0.0
        next_check = monotonic() + 15
        while not await request.is_disconnected():
            if monotonic() >= next_check:
                try:
                    if await run_in_threadpool(check_access) != identity:
                        break
                except HTTPException:
                    break
                next_check = monotonic() + 15
            version = runtime.version
            if version != last:
                last, idle = version, 0.0
                yield f"data: {version}\n\n"
            elif idle >= 15:
                idle = 0.0
                yield ": ping\n\n"
            await asyncio.sleep(0.5)
            idle += 0.5

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(ACCOUNT_COOKIE)
    if raw:
        db.query(RotationAccountSession).filter_by(token_hash=token_hash(raw)).delete(synchronize_session=False)
        db.commit()
    response.delete_cookie(ACCOUNT_COOKIE, secure=request_is_https(request), httponly=True, samesite="lax")
    return {"ok": True}


def can_manage_screen_accounts(db: Session, user: V2User) -> bool:
    if user.rotation_temporary or user.base_role.code != "GSM":
        return False
    attraction_id = rotation_attraction_id(db)
    return user.employee.attraction_id == attraction_id or attraction_id in managed_attraction_ids(db, user.id)


def _require_account_manager(db: Session = Depends(get_db), user: V2User = Depends(current_user)) -> V2User:
    if not can_manage_screen_accounts(db, user):
        raise HTTPException(403, "只有负责热力追踪的正式 GSM 可以管理休息室大屏账号")
    return user


def _screen_accounts(db: Session):
    return db.query(RotationAccount).filter_by(kind=KIND_SCREEN, attraction_id=rotation_attraction_id(db))


def _format_time(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


@router.get("/accounts", dependencies=[Depends(_require_account_manager)])
def accounts(db: Session = Depends(get_db)):
    rows = _screen_accounts(db).order_by(RotationAccount.login_account).all()
    now = datetime.now()
    return {"items": [{
        "login_account": account.login_account,
        "kind": account.kind,
        "kind_label": SCREEN_LABEL,
        "name": account.name,
        "enabled": account.enabled,
        "last_login_at": _format_time(account.last_login_at),
        "password_changed_at": _format_time(account.password_changed_at),
        "locked": bool(account.locked_until and account.locked_until > now),
    } for account in rows]}


@router.post("/accounts/{login_account}/password")
def reset_screen_password(
    login_account: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(_require_account_manager),
):
    password = validate_account_password(payload.get("new_password"))
    account = _screen_accounts(db).filter_by(login_account=login_account).first()
    if not account:
        raise HTTPException(404, "账号不存在")
    account.password_hash = hash_password(password)
    account.password_changed_at = datetime.now()
    account.failed_attempts = 0
    account.locked_until = None
    db.query(RotationAccountSession).filter_by(account_id=account.id).delete(synchronize_session=False)
    write_audit(
        db, user.employee, f"重置{SCREEN_LABEL}密码", "rotation_account", account.id,
        after={"account": account.login_account, "operator": user.name}, ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "message": f"{account.name}密码已重置，原有登录已失效"}
