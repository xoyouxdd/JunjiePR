"""Embedded attendance-honor management and public, privacy-filtered collections."""
from datetime import date, datetime
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.v2_auth import current_user, load_user_identity
from app.v2_database import get_db
from app.attendance_medals_models import MedalLeaveMonth, MedalNotice, MedalWageCode
from app.services import attendance_medals as medals
from app.employee_numbers import employee_number
from app.v2_models import Attraction, Employee, AuditLog, UserAccount

router = APIRouter()


class Confirmation(BaseModel):
    month: str
    revision: int
    refresh_roster: bool = False
    reason: str = Field(default="", max_length=500)


class Publication(Confirmation):
    attraction_id: int
    fingerprint: str


class IdentityRepair(BaseModel):
    employee_id: int
    first_created_on: date
    reason: str = Field(min_length=1, max_length=500)


class ScopeRepair(BaseModel):
    month: str
    employee_id: int
    attraction_id: int
    reason: str = Field(min_length=1, max_length=500)


class WageClassification(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    kind: Literal['leave', 'normal']
    revision: int = Field(default=0, ge=0)
    reason: str = Field(min_length=1, max_length=500)


def locked(db, operation):
    try:
        db.rollback()
        db.execute(text("BEGIN IMMEDIATE"))
        result = operation()
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def current_operator(db, user):
    account_id = getattr(user.account, "id", None)
    account = db.get(UserAccount, account_id, populate_existing=True) if account_id is not None else None
    employee = db.get(Employee, user.id, populate_existing=True)
    if not account or not account.enabled or account.employee_id != user.id or not employee or not employee.is_active:
        raise HTTPException(401, "账号不可用")
    return load_user_identity(db, employee, account)


def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"


def require_employee_account(db, user):
    if not db.query(UserAccount.id).filter_by(employee_id=user.id).first():
        raise HTTPException(403, "仅正式员工账号可查看全勤勋章")


@router.get("/attendance-medals/manage")
def manage(month: str, response: Response, trial_view: bool = False, db=Depends(get_db), user=Depends(current_user)):
    private(response)
    medals.month_bounds(month)
    can_manage = medals.can_manage(user)
    can_import = medals.is_admin(user) or ("SICK_LEAVE_IMPORT" in user.permissions and not user.has_role("HR_CIRCLE"))
    can_observe = medals.can_observe_trial(user, month)
    if not (can_manage or can_import or can_observe) or (trial_view and not can_observe):
        raise HTTPException(403, "没有全勤勋章管理权限")
    circles = db.query(Attraction).filter_by(employee_circle=True, active=True).order_by(Attraction.id).all()
    if not medals.is_admin(user) and user.has_role("HR_CIRCLE"):
        circles = [c for c in circles if c.id == user.employee.attraction_id]
    circle_info = [{"id": c.id, "name": c.name} for c in circles]
    if trial_view or (can_observe and not (can_manage or can_import)):
        return {"month": month, "circles": circle_info, "can_import": False,
                "items": [medals.trial_results(db, month, c.id) for c in circles]}
    items = [medals.preview(db, user, month, c.id) for c in circles] if can_manage else []
    state = db.get(MedalLeaveMonth, month)
    batch = medals.latest_batch(db, state)
    issues = medals.batch_issues(db, batch)
    assignment = json.loads(state.roster) if state and state.roster else medals.roster(db, month)
    return {"month": month, "circles": circle_info, "items": items,
        "can_import": can_import, "highest": medals.is_admin(user),
        "revision": state.revision if state else 0,
        "data_ready": medals.data_ready(state, issues),
        "batch": {"filename": batch.filename, "imported_by": batch.imported_by, "imported_at": str(batch.imported_at),
                  "issues": issues, "leaves": len(json.loads(batch.leaves)),
                  "ignored_codes": json.loads(batch.ignored_codes)} if batch and can_import else None,
        "roster": [{"id": e.id, "name": e.name, "employee_no": e.employee_no,
                    "attraction_id": assignment.get(str(e.id))} for e in db.query(Employee).order_by(Employee.employee_no)] if medals.is_admin(user) else [],
        "wage_codes": [{"code": c.code, "kind": c.kind, "revision": c.revision}
                       for c in db.query(MedalWageCode).order_by(MedalWageCode.code)] if can_import else [],
        "unassigned_count": sum(v is None for v in assignment.values()) if medals.is_admin(user) else 0}


@router.post('/attendance-medals/classify-code')
def classify_code(payload: WageClassification, db=Depends(get_db), user=Depends(current_user)):
    return locked(db, lambda: medals.classify_code(db, user, payload.code, payload.kind, payload.revision, payload.reason))


@router.post("/attendance-medals/import")
async def import_file(month: str = Form(...), mode: str = Form("append"), expected_revision: int = Form(...),
                      file_digest: str = Form(...), workbook: UploadFile = File(...),
                      db=Depends(get_db), user=Depends(current_user)):
    medals.ensure_importer(user)
    if not (workbook.filename or "").lower().endswith(".xls"):
        raise HTTPException(400, "请上传.xls员工事务文件")
    content = await workbook.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件不能超过10MB")
    def operation():
        import hashlib
        state = db.get(MedalLeaveMonth, month)
        if expected_revision != (state.revision if state else 0) or hashlib.sha256(content).hexdigest() != file_digest:
            raise HTTPException(409, "数据或文件已变化，请重新预览")
        return medals.store_import(db, user, month, content, workbook.filename, mode)
    return locked(db, operation)


@router.post("/attendance-medals/import-preview")
async def preview_import(month: str = Form(...), mode: str = Form("append"), workbook: UploadFile = File(...),
                         db=Depends(get_db), user=Depends(current_user)):
    import hashlib
    medals.ensure_importer(user)
    medals.month_bounds(month)
    if not (workbook.filename or "").lower().endswith('.xls'):
        raise HTTPException(400, "请上传.xls员工事务文件")
    content = await workbook.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件不能超过10MB")
    leaves, issues, ignored = medals.read_all_leaves(db, content, month)
    state = db.get(MedalLeaveMonth, month)
    batch = medals.latest_batch(db, state)
    old = json.loads(batch.leaves) if batch else []
    before, after = {medals.leave_key(r) for r in old}, {medals.leave_key(r) for r in leaves}
    return {"revision": state.revision if state else 0, "file_digest": hashlib.sha256(content).hexdigest(),
        "added": len(after-before), "removed": len(before-after) if mode == 'replace' else 0,
        "leaves": len(leaves), "issues": issues, "ignored_codes": ignored}


@router.post("/attendance-medals/confirm-data")
def confirm_data(payload: Confirmation, db=Depends(get_db), user=Depends(current_user)):
    def operation():
        medals.month_bounds(payload.month)
        medals.confirm_data(db, user, payload.month, payload.revision, payload.refresh_roster, payload.reason)
        return {"ok": True}
    return locked(db, operation)


@router.post("/attendance-medals/publish")
def publish(payload: Publication, db=Depends(get_db), user=Depends(current_user)):
    return locked(db, lambda: medals.publish(db, current_operator(db, user), payload.month, payload.attraction_id,
        payload.revision, payload.fingerprint, payload.reason))


@router.get("/attendance-medals/employees/{employee_id}")
def collection(employee_id: int, response: Response, db=Depends(get_db), user=Depends(current_user)):
    private(response)
    require_employee_account(db, user)
    if not db.get(Employee, employee_id):
        raise HTTPException(404, "员工不存在")
    return medals.collection(db, user, employee_id)


@router.get("/attendance-medals/people")
def people(response: Response, keyword: str = "", db=Depends(get_db), user=Depends(current_user)):
    private(response)
    require_employee_account(db, user)
    keyword = employee_number(keyword.strip()[:50])
    if not medals.employee_content_visible() or not keyword:
        return {"items": []}
    pattern = "%" + keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = db.query(Employee).join(UserAccount, UserAccount.employee_id == Employee.id).filter(
        Employee.is_active.is_(True), (Employee.name.like(pattern, escape="\\") | Employee.employee_no.like(pattern, escape="\\"))
    ).order_by(Employee.employee_no).limit(30).all()
    return {"items": [{"id": e.id, "name": e.name, "employee_no": e.employee_no} for e in rows]}


@router.get("/attendance-medals/history")
def history(month: str, attraction_id: int, response: Response, db=Depends(get_db), user=Depends(current_user)):
    private(response)
    medals.ensure_scope(db, user, attraction_id)
    medals.month_bounds(month)
    rows = db.query(AuditLog).filter_by(entity_type="attendance_medal_circle", entity_id=str(attraction_id)).order_by(AuditLog.id.desc()).all()
    return {"items": [{"time": str(r.created_at), "operator": r.operator_name,
        "action": r.action, "reason": json.loads(r.after_json or '{}').get('reason', '')}
        for r in rows if json.loads(r.after_json or '{}').get('month') == month][:100]}


@router.post("/attendance-medals/notices/{notice_id}/read")
def read_notice(notice_id: int, db=Depends(get_db), user=Depends(current_user)):
    def operation():
        notice = medals.visible_notices(db, user.id).filter_by(id=notice_id).first()
        if not medals.employee_content_visible() or not notice:
            raise HTTPException(404, "消息不存在")
        notice.read_at = notice.read_at or datetime.now()
        return {"ok": True}
    return locked(db, operation)


@router.post("/attendance-medals/repair-identity")
def repair_identity(payload: IdentityRepair, db=Depends(get_db), user=Depends(current_user)):
    def operation():
        from app.attendance_medals_models import MedalAccountIdentity
        from app.services.audit import write_audit
        if not medals.is_admin(user):
            raise HTTPException(403, "仅最高管理员可核实原账号日期")
        if not payload.reason.strip() or payload.first_created_on > date.today():
            raise HTTPException(400, "请填写核实原因及有效的原账号创建日期")
        if not db.get(Employee, payload.employee_id):
            raise HTTPException(404, "员工不存在")
        identity = db.get(MedalAccountIdentity, payload.employee_id)
        before = str(identity.first_created_at) if identity else None
        if not identity:
            identity = MedalAccountIdentity(employee_id=payload.employee_id)
            db.add(identity)
        identity.first_created_at = datetime.combine(payload.first_created_on, datetime.min.time())
        write_audit(db, user.employee, "核实全勤勋章账号原始日期", "employee", payload.employee_id,
            before={"first_created_at": before}, after={"first_created_at": str(identity.first_created_at)}, reason=payload.reason)
        return {"ok": True}
    return locked(db, operation)


@router.post("/attendance-medals/repair-scope")
def repair_scope(payload: ScopeRepair, db=Depends(get_db), user=Depends(current_user)):
    def operation():
        from app.services.audit import write_audit
        operator = current_operator(db, user)
        if not medals.is_admin(operator):
            raise HTTPException(403, "仅最高管理员可修正历史圈归属")
        medals.month_bounds(payload.month)
        medals.ensure_scope(db, operator, payload.attraction_id)
        if not db.get(Employee, payload.employee_id) or not payload.reason.strip():
            raise HTTPException(400, "请确认员工并填写修正原因")
        state = db.get(MedalLeaveMonth, payload.month)
        if not state or not state.roster:
            raise HTTPException(409, "请先确认该月数据完整并建立人员快照")
        assignment = json.loads(state.roster)
        before = assignment.get(str(payload.employee_id))
        assignment[str(payload.employee_id)] = payload.attraction_id
        state.roster = medals.dump(assignment)
        write_audit(db, operator.employee, "修正全勤勋章月份归属", "attendance_medal_circle", payload.attraction_id,
            before={"attraction_id": before}, after={"month": payload.month, "employee_id": payload.employee_id,
                "attraction_id": payload.attraction_id, "reason": payload.reason})
        if before and before != payload.attraction_id:
            write_audit(db, operator.employee, "修正全勤勋章月份归属", "attendance_medal_circle", before,
                after={"month": payload.month, "employee_id": payload.employee_id,
                       "attraction_id": payload.attraction_id, "reason": payload.reason})
        return {"ok": True}
    return locked(db, operation)
