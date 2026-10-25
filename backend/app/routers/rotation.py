"""轮岗接口：测试入口、模拟账号、看板、大屏、个人页、名单、记录、配置与实时同步。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
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
    require_controller,
    require_member,
    require_screen,
    rotation_attraction_id,
    start_session,
    validate_account_password,
)
from app.rotation import service
from app.rotation.models import RotationAccount, RotationAccountSession, RotationRosterEntry
from app.rotation.roster import parse_any
from app.routers._shared import client_ip, parse_iso_date
from app.security import request_is_https
from app.v2_auth import V2User, current_user
from app.v2_crypto import hash_password, token_hash
from app.v2_database import get_db
from app.v2_services import write_audit


router = APIRouter(prefix="/rotation", tags=["rotation"])
from app.routers.rotation_adjust import router as adjustment_router
router.include_router(adjustment_router)


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
    if login_name == 'self':
        from app.rotation.adjustment import qualified, start_staff
        if not qualified(db,user.employee):
            raise HTTPException(403,'只有本圈管理人员可直接进入现场调整')
        start_session(db,request,response,account=find_rotation_account(db,'6666666'),entered_by=user.employee)
        start_staff(db,request,response,user.employee)
        return {'ok':True,'kind':'supervisor','name':user.name,'redirect':'/rotation/screen'}
    account = find_rotation_account(db, login_name)
    if account:
        if account.kind in {'supervisor','manager'} and user.base_role.code!='GSM':
            raise HTTPException(403,'请使用本人现场调整入口；TR须使用本人账号和临时授权码')
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
        "adjustment": actor.adjustment, "can_authorize": actor.can_authorize,
        "expires_at": actor.expires_at.isoformat() if actor.expires_at else None,
        "temporary": actor.temporary,
    }


@router.post("/logout")
def rotation_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """退出轮岗模拟账号；PR 员工账号不受影响。"""
    from app.rotation.adjustment import COOKIE
    from app.rotation.models import RotationAdjustSession
    raw_adjust=request.cookies.get(COOKIE)
    if raw_adjust:
        db.query(RotationAdjustSession).filter_by(token_hash=token_hash(raw_adjust)).delete()
        db.commit()
    response.delete_cookie(COOKIE,secure=request_is_https(request),httponly=True,samesite="lax")
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
def rotation_accounts(db: Session = Depends(get_db), actor: RotationActor = Depends(require_controller)):
    rows = db.query(RotationAccount).order_by(RotationAccount.login_account.asc()).all()
    return {"items": [_account_payload(row) for row in rows]}


@router.post("/accounts/{login_account}/password")
def reset_rotation_account_password(
    login_account: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    actor: RotationActor = Depends(require_controller),
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


# ---------------------------------------------------------------- 看板、大屏、个人页

def _action_error(exc: service.ActionError) -> HTTPException:
    return HTTPException(409 if isinstance(exc, service.ConfirmationConflict) else 400, str(exc))


@router.get("/board")
def rotation_board(db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    payload = service.board_payload(db, rotation_attraction_id(db))
    payload["me"] = {"kind": actor.kind, "name": actor.name}
    return payload


@router.get("/screen/state")
def rotation_screen_state(db: Session = Depends(get_db), actor: RotationActor = Depends(require_screen)):
    payload = service.screen_payload(db, rotation_attraction_id(db))
    return payload


@router.post("/screen/act")
def rotation_screen_act(payload: dict, db: Session = Depends(get_db), actor: RotationActor = Depends(require_screen)):
    """大屏只能点「去休息」（到达）和「去轮岗」（出发）。"""
    if not isinstance(payload.get("action"), str):
        raise HTTPException(400, "操作类型必须是字符串")
    if payload["action"] not in service.SCREEN_ACTIONS:
        raise HTTPException(403, "大屏只能点去休息和去轮岗")
    if not payload.get("confirmation_token"):
        raise HTTPException(400, "请刷新后重新确认")
    try:
        return service.do_live_action(db, rotation_attraction_id(db), actor,
                                      {"action": payload["action"], "pid": payload.get("pid"),
                                       "confirmation_token": payload["confirmation_token"], "meal_choice": payload.get("meal_choice"),
                                       "group_confirm": payload.get('group_confirm',False),"expected_plan":payload.get('expected_plan')})
    except service.ActionError as exc:
        raise _action_error(exc) from exc


@router.post("/me/act")
def rotation_member_act(payload: dict, db: Session = Depends(get_db), actor: RotationActor = Depends(require_member)):
    """模拟 CM/TR 只确认当前会话本人，和大屏共用一次性步骤。"""
    if not isinstance(payload.get("action"), str):
        raise HTTPException(400, "操作类型必须是字符串")
    if payload.get("action") not in service.SCREEN_ACTIONS:
        raise HTTPException(403, "个人待办只能确认到达休息室和去轮岗")
    if payload.get("pid", actor.member_employee_no) != actor.member_employee_no:
        raise HTTPException(403, "只能确认自己的轮岗待办")
    if not payload.get("confirmation_token"):
        raise HTTPException(400, "请刷新后重新确认")
    try:
        return service.do_live_action(db, rotation_attraction_id(db), actor,
                                      {"action": payload["action"], "pid": actor.member_employee_no,
                                       "confirmation_token": payload["confirmation_token"], "meal_choice": payload.get("meal_choice")})
    except service.ActionError as exc:
        raise _action_error(exc) from exc


@router.get("/me")
def rotation_me(db: Session = Depends(get_db), actor: RotationActor = Depends(require_member)):
    payload = service.member_payload(db, rotation_attraction_id(db), actor.member_employee_no)
    payload["test_mode"] = True
    return payload


@router.get("/confirmation")
def rotation_confirmation(token: str, date: str, db: Session = Depends(get_db), actor: RotationActor = Depends(require_actor)):
    if actor.kind not in {"member", "screen"}:
        raise HTTPException(403, "只有个人待办和大屏可以核实确认结果")
    if len(token) != 64:
        raise HTTPException(400, "确认任务无效")
    try:
        return service.confirmation_status(db, rotation_attraction_id(db), date, token,
                                           actor.member_employee_no if actor.kind == "member" else None)
    except service.ActionError as exc:
        raise _action_error(exc) from exc


@router.post("/act")
def rotation_act(payload: dict, db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    try:
        service.do_live_action(db, rotation_attraction_id(db), actor, payload)
    except service.ActionError as exc:
        raise _action_error(exc) from exc
    return {"ok": True}


@router.post("/draft")
def rotation_draft(payload: dict, db: Session = Depends(get_db), actor: RotationActor = Depends(require_controller)):
    try:
        service.do_draft_action(db, rotation_attraction_id(db), actor, payload)
    except service.ActionError as exc:
        raise _action_error(exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------- 名单、记录、配置

ROSTER_MAX_BYTES = 10 * 1024 * 1024


@router.post("/roster/upload")
async def rotation_roster_upload(
    file: UploadFile = File(...),
    scope: str = Form("week"),
    date: str = Form(""),
    db: Session = Depends(get_db),
    actor: RotationActor = Depends(require_controller),
):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "请上传 .xlsx 格式的名单")
    content = await file.read(ROSTER_MAX_BYTES + 1)
    if len(content) > ROSTER_MAX_BYTES:
        raise HTTPException(400, "名单文件不能超过 10MB")
    try:
        parsed = parse_any(BytesIO(content))
    except (ValueError, KeyError, StopIteration, zipfile.BadZipFile) as exc:
        raise HTTPException(400, f"名单无法识别：{exc}") from exc
    try:
        upload = service.save_roster(
            db, rotation_attraction_id(db), parsed, scope=scope, file_name=file.filename or "名单.xlsx",
            uploader_id=actor.entered_by.id if actor.entered_by else None, uploader_name=actor.name,
            only_date=date or None,
        )
    except service.ActionError as exc:
        raise _action_error(exc) from exc
    return {"ok": True, "scope": upload.scope, "start_date": upload.start_date, "end_date": upload.end_date, "entry_count": upload.entry_count}


@router.get("/roster")
def rotation_roster(date: str, db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    return service.roster_summary(db, rotation_attraction_id(db), parse_iso_date(date).isoformat())


@router.get("/log")
def rotation_log(date: str, db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    return {"items": service.day_log(db, rotation_attraction_id(db), parse_iso_date(date).isoformat())}


@router.get("/person")
def rotation_person(employee_no: str, date: str, db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    return service.person_record(db, rotation_attraction_id(db), employee_no.strip(), parse_iso_date(date).isoformat())


@router.get("/config")
def rotation_config(db: Session = Depends(get_db), actor: RotationActor = Depends(require_manager)):
    with service.transaction(db):
        cfg = service.load_config(db, rotation_attraction_id(db))
        payload = {"lines": service.lines_of(cfg), "settings": service.settings_of(cfg), "defaults": service.E.DEFAULT_SETTINGS}
    return payload


@router.put("/config")
def rotation_config_update(payload: dict, db: Session = Depends(get_db), actor: RotationActor = Depends(require_controller)):
    try:
        with service.transaction(db):
            cfg = service.load_config(db, rotation_attraction_id(db))
            service.update_config(cfg, payload.get("lines"), payload.get("settings"), actor.entered_by.id if actor.entered_by else None)
            service.mark_changed(db)
    except service.ActionError as exc:
        raise _action_error(exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------- 实时同步

@router.get("/stream")
async def rotation_stream(request: Request, actor: RotationActor = Depends(require_actor)):
    """推送版本号：版本变了，页面再去拉取最新数据。"""

    async def events():
        last, idle = None, 0.0
        while not await request.is_disconnected():
            version = service.RUNTIME.version
            if version != last:
                last, idle = version, 0.0
                yield f"data: {version}\n\n"
            elif idle >= 15:
                idle = 0.0
                yield ": ping\n\n"
            await asyncio.sleep(0.5)
            idle += 0.5

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Daily schedules use the original controller identity; read-only helpers get no new access.
@router.get('/daily')
def rotation_daily_month(month:str,db:Session=Depends(get_db),actor:RotationActor=Depends(require_controller)):
    from app.rotation.daily import month_payload
    try:return month_payload(db,rotation_attraction_id(db),month)
    except service.ActionError as exc:raise _action_error(exc) from exc


@router.get('/daily/{work_date}')
def rotation_daily_date(work_date:str,at:str|None=None,db:Session=Depends(get_db),actor:RotationActor=Depends(require_controller)):
    from app.rotation.daily import day_payload
    try:return day_payload(db,rotation_attraction_id(db),parse_iso_date(work_date).isoformat(),at)
    except service.ActionError as exc:raise _action_error(exc) from exc


@router.post('/daily/{work_date}')
def rotation_daily_edit(work_date:str,payload:dict,db:Session=Depends(get_db),actor:RotationActor=Depends(require_controller)):
    from app.rotation.daily import edit
    try:edit(db,rotation_attraction_id(db),actor,parse_iso_date(work_date).isoformat(),payload)
    except service.ActionError as exc:raise _action_error(exc) from exc
    return {'ok':True}
