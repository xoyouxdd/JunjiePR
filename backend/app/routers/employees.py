"""Employee master data, employee-number and import endpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user, require_permissions
from app.v2_crypto import default_initial_password, hash_password
from app.v2_database import get_db
from app.services.management_scopes import synchronize_gsm_management_scope
from app.v2_models import Attraction, Employee, EmployeeNumberHistory, EmployeeLOAPeriod, EmployeeRoleAssignment, Role, UserAccount, UserSession
from app.role_constants import FRONTLINE_CODES, LEADER_CODES
from app.services.audit import write_audit
from app.services.identity import base_role_at, duties_at_bulk, role_at
from app.services.employee_commands import EmployeeEditContext, apply_employee_edit
from app.services.employee_status import employee_circle_id
from app.services.employment import restart_employment
from app.v2_watermark import watermark_workbook
from app.excel_export import build_employee_import_template
from app.services import loa_commands
from app.routers._shared import (
    CIRCLE_HR_MANAGED_ROLE_CODES,
    EMPLOYEE_TARGET_PERMISSIONS,
    SCOPED_HR_ROLE_CODE,
    client_ip,
    employee_payloads,
    ensure_month_open,
    ensure_scoped_hr_attraction,
    ensure_scoped_hr_employee,
    invalidate_data_caches,
    like_escaped_pattern,
    parse_iso_date,
    scoped_hr_attraction_ids,
    search_employee_targets,
)

router = APIRouter()

LOA_REGISTRAR_ROLE_CODES = {"TA_GSM", "GSM", "AM", "OM", "HR_CIRCLE"}


def ensure_employee_number_change_target(db: Session, user: V2User, employee: Employee, role: Role | None = None) -> Role:
    """Apply the employee-number migration scope without changing org data."""
    if user.role.code not in {SCOPED_HR_ROLE_CODE, "SYSTEM_ADMIN"}:
        raise HTTPException(403, "仅景点圈HR和最高管理员可以变更员工号")
    target_role = role or role_at(db, employee.id)
    if not target_role:
        raise HTTPException(400, "该员工当前没有有效角色")
    if target_role.code in {"HR_ADMIN", "HR_CIRCLE", "SYSTEM_ADMIN"}:
        raise HTTPException(403, "HR和最高管理员账号不能在此处变更员工号")
    ensure_scoped_hr_employee(db, user, employee)
    if user.role.code == SCOPED_HR_ROLE_CODE and target_role.code not in CIRCLE_HR_MANAGED_ROLE_CODES:
        raise HTTPException(403, "景点圈HR只能变更本圈CM、TR、TA主管或主管的员工号")
    return target_role


def ensure_hr_role_allowed(user: V2User, role_code: str, label: str = "角色") -> None:
    if "SYSTEM_ADMIN" not in user.permissions and role_code not in CIRCLE_HR_MANAGED_ROLE_CODES:
        raise HTTPException(403, f"景点圈HR只能设置{label}为CM、TR、TA主管或主管")


def employee_payload(db: Session, employee: Employee) -> dict:
    return employee_payloads(db, [employee])[employee.id]


@router.get("/frontline-employees")
def frontline_employees(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if not ({"EMPLOYEE_ADD", "SICK_REGISTER", "DEDUCTION_ALL", "DEDUCTION_DIRECT"} & user.permissions):
        raise HTTPException(403, "没有权限查询CM/TR")
    allowed = scoped_hr_attraction_ids(db, user)
    attraction_id = next(iter(allowed)) if allowed else None
    return search_employee_targets(db, attraction_id=attraction_id, limit=500)["items"]


def supervisor_targets(db: Session, user: V2User, usage: str, keyword: str, attraction_id: int | None, limit: int) -> dict:
    """Supervisors that this account may credit or deduct (see ensure_supervisor_target_allowed)."""
    if usage not in {"recognition", "deduction"}:
        raise HTTPException(400, "员工查询用途无效")
    if not ("SUPERVISOR_SCORE" in user.permissions and user.role.code in {"GSM", "TA_GSM"}):
        raise HTTPException(403, "没有为主管登记的权限")
    if not keyword.strip():
        return {"items": [], "total": 0, "limit": max(1, min(limit, 50)), "search_scope": "全部景点圈在职主管（请输入姓名或员工号）"}
    result = search_employee_targets(db, keyword=keyword, attraction_id=attraction_id, limit=200, role_codes=("SUPERVISOR",))
    duties = duties_at_bulk(db, [row["id"] for row in result["items"]])
    items = []
    for row in result["items"]:
        duty = (duties.get(row["id"]) or [None])[0]
        # Supervisors acting as TA GSM are scored by nobody else.
        if row["id"] == user.id or (duty and duty.code == "TA_GSM"):
            continue
        items.append({**row, "duty_role_code": duty.code if duty else "", "role_name": f"主管 · 代理{duty.name}" if duty else row["role_name"], "scoring_category": "supervisor"})
    shown = items[: max(1, min(limit, 50))]
    return {"items": shown, "total": len(items), "limit": max(1, min(limit, 50)), "search_scope": "全部景点圈在职主管（不含代理TA GSM期间的主管）"}


@router.get("/employee-targets")
def employee_targets(
    usage: str,
    keyword: str = "",
    attraction_id: int | None = None,
    limit: int = 30,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
    scope: str = "frontline",
):
    if scope == "supervisor":
        return supervisor_targets(db, user, usage, keyword, attraction_id, limit)
    required = EMPLOYEE_TARGET_PERMISSIONS.get(usage)
    if not required:
        raise HTTPException(400, "员工查询用途无效")
    if not (required & user.permissions):
        raise HTTPException(403, "没有对应的员工登记权限")
    if usage == "poc":
        if user.role.code not in {"TA_GSM", "GSM", "AM", "OM"}:
            raise HTTPException(403, "仅TA GSM、GSM、AM、OM可以查询POC被认可员工")
        value = like_escaped_pattern(keyword)
        query = db.query(Employee).join(UserAccount, UserAccount.employee_id == Employee.id).filter(
            Employee.is_active.is_(True), UserAccount.enabled.is_(True),
            or_(Employee.name.like(value, escape="\\"), Employee.employee_no.like(value, escape="\\")),
        )
        if attraction_id is not None:
            query = query.filter(Employee.attraction_id == attraction_id)
        rows = []
        for item in query.order_by(Employee.name, Employee.employee_no).limit(max(1, min(limit, 50)) * 4).all():
            role = role_at(db, item.id)
            if role and role.code in (FRONTLINE_CODES | LEADER_CODES):
                circle = db.get(Attraction, item.attraction_id) if item.attraction_id else None
                rows.append({"id": item.id, "employee_no": item.employee_no, "name": item.name, "role_code": role.code, "role_name": role.name, "attraction_id": item.attraction_id, "attraction_name": circle.name if circle else "", "group_id": None, "group_name": ""})
        return {"items": rows[:max(1, min(limit, 50))], "total": len(rows), "limit": max(1, min(limit, 50))}
    if usage == "loa":
        # LOA registration deliberately permits its three authorized roles to
        # locate every active employee.  This expands lookup only, not any
        # other HR management scope.
        if user.role.code not in LOA_REGISTRAR_ROLE_CODES:
            raise HTTPException(403, "仅TA GSM、GSM、AM、OM、景点圈HR可以登记LOA")
        if not keyword.strip():
            return {"items": [], "total": 0, "limit": max(1, min(limit, 50)), "search_scope": "全部在职员工（请输入姓名或员工号）"}
        value = like_escaped_pattern(keyword)
        query = db.query(Employee).filter(
            Employee.is_active.is_(True),
            or_(Employee.name.like(value, escape="\\"), Employee.employee_no.like(value, escape="\\")),
        )
        if attraction_id is not None:
            query = query.filter(Employee.attraction_id == attraction_id)
        rows = []
        for item in query.order_by(Employee.name, Employee.employee_no).limit(max(1, min(limit, 50)) * 2).all():
            role = role_at(db, item.id)
            circle = db.get(Attraction, item.attraction_id) if item.attraction_id else None
            active_loa = (
                db.query(EmployeeLOAPeriod)
                .filter(
                    EmployeeLOAPeriod.employee_id == item.id,
                    EmployeeLOAPeriod.status == "active",
                    or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= date.today().isoformat()),
                )
                .order_by(EmployeeLOAPeriod.starts_on.desc(), EmployeeLOAPeriod.id.desc())
                .first()
            )
            rows.append({"id": item.id, "employee_no": item.employee_no, "name": item.name, "role_code": role.code if role else "", "role_name": role.name if role else "未分配角色", "attraction_id": item.attraction_id, "attraction_name": circle.name if circle else "", "group_id": None, "group_name": "", "loa_active": bool(active_loa), "loa_period_id": active_loa.id if active_loa else None, "loa_starts_on": active_loa.starts_on if active_loa else "", "loa_ends_on": active_loa.ends_on if active_loa else ""})
        return {"items": rows[:max(1, min(limit, 50))], "total": len(rows), "limit": max(1, min(limit, 50)), "search_scope": "全部在职员工"}
    # Deduction/absence target searches are intentionally keyword-only for
    # cross-circle managers.  TA主管 retains its home-circle boundary, while
    # 主管、TA GSM、GSM may locate active CM/TR across all circles.
    global_target_search = usage in {"deduction", "attendance"} and user.role.code in {"SUPERVISOR", "TA_GSM", "GSM"}
    if global_target_search and not keyword.strip():
        return {"items": [], "total": 0, "limit": max(1, min(limit, 50)), "search_scope": "全部景点圈在职CM/TR（请输入姓名或员工号）"}
    if user.role.code == "TA_SUPERVISOR" and usage in {"deduction", "attendance"}:
        attraction_id = user.employee.attraction_id
    if attraction_id is not None:
        circle = db.get(Attraction, attraction_id)
        if not circle or not circle.active or not circle.employee_circle:
            raise HTTPException(400, "请选择有效员工景点圈")
    elif not global_target_search:
        allowed = scoped_hr_attraction_ids(db, user)
        attraction_id = next(iter(allowed)) if allowed else None
    if not global_target_search:
        ensure_scoped_hr_attraction(db, user, attraction_id)
    result = search_employee_targets(
        db,
        keyword=keyword,
        attraction_id=attraction_id,
        limit=max(1, min(limit, 50)),
    )
    result["search_scope"] = "全部景点圈在职CM/TR（请输入姓名或员工号）" if global_target_search else "本景点圈在职CM/TR"
    return result


def loa_period_payload(row: EmployeeLOAPeriod, employee: Employee | None = None) -> dict:
    return {
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": employee.employee_no if employee else "",
        "employee_name": employee.name if employee else "",
        "starts_on": row.starts_on,
        "ends_on": row.ends_on or "",
        "note": row.note or "",
        "status": row.status,
        "created_by_name": row.created_by_name,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
    }


def loa_today() -> date:
    """Indirection so tests can pin the "today" used by the open-LOA horizon."""
    return date.today()


@router.get("/loa-periods")
def list_loa_periods(month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("LOA_REGISTER"))):
    query = db.query(EmployeeLOAPeriod).filter(EmployeeLOAPeriod.status != "cancelled")
    if month:
        start = parse_iso_date(f"{month}-01", "月份")
        finish = start.replace(day=28) + timedelta(days=4)
        finish = finish - timedelta(days=finish.day)
        query = query.filter(EmployeeLOAPeriod.starts_on <= finish.isoformat(), or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= start.isoformat()))
    rows = query.order_by(EmployeeLOAPeriod.starts_on.desc(), EmployeeLOAPeriod.id.desc()).limit(200).all()
    employees = {row.id: row for row in db.query(Employee).filter(Employee.id.in_({row.employee_id for row in rows})).all()} if rows else {}
    return {"items": [loa_period_payload(row, employees.get(row.employee_id)) for row in rows]}


@router.post("/loa-periods")
def create_loa_period(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("LOA_REGISTER"))):
    try:
        # Authentication may have opened a read transaction.  Reserve the
        # SQLite writer before reading the employee or testing date conflicts.
        db.rollback()
        db.execute(text("BEGIN IMMEDIATE"))
        return _create_loa_period(payload, request, db, user)
    except Exception:
        db.rollback()
        raise


def _create_loa_period(payload: dict, request: Request, db: Session, user: V2User):
    if user.role.code not in LOA_REGISTRAR_ROLE_CODES:
        raise HTTPException(403, "仅TA GSM、GSM、AM、OM、景点圈HR可以登记LOA")
    try:
        employee_id = int(payload.get("employee_id") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择员工") from exc
    employee = db.get(Employee, employee_id)
    if not employee or not employee.is_active:
        raise HTTPException(400, "请选择在职员工")
    if employee.id == user.id:
        raise HTTPException(403, "不能为本人登记LOA")
    starts_on = parse_iso_date(str(payload.get("starts_on") or ""), "LOA进入日期")
    ends_value = str(payload.get("ends_on") or "").strip()
    ends_on = parse_iso_date(ends_value, "LOA结束日期") if ends_value else None
    try:
        open_period = loa_commands.current_open_period(db, employee.id)
        note = str(payload.get("note") or "").strip() or None
        before = None
        if open_period:
            if not ends_on:
                raise HTTPException(409, "该员工已处于LOA，请填写结束日期完成登记")
            before = loa_period_payload(open_period, employee)
            change = loa_commands.close_period(
                db, employee, open_period, ends_on, actor_id=user.id, actor_name=user.name,
                today=loa_today(), gate=ensure_month_open, note=note,
            )
        else:
            change = loa_commands.create_period(
                db, employee, starts_on, ends_on, actor_id=user.id, actor_name=user.name,
                note=note, today=loa_today(), gate=ensure_month_open,
            )
        row = change.period
        loa_commands.recalculate_changed_months(db, employee, change.changed_months)
        write_audit(db, user.employee, "登记LOA结束" if open_period else "登记LOA", "employee_loa_period", row.id, before=before, after=loa_period_payload(row, employee), ip_address=client_ip(request))
        db.commit()
    except Exception:
        db.rollback()
        raise
    invalidate_data_caches()
    return {"ok": True, "record": loa_period_payload(row, employee), "excluded_months": list(change.excluded_months), "completed": bool(ends_on)}


@router.delete("/loa-periods/{period_id}")
def cancel_loa_period(period_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("LOA_REGISTER"))):
    try:
        db.rollback()
        db.execute(text("BEGIN IMMEDIATE"))
        return _cancel_loa_period(period_id, payload, request, db, user)
    except Exception:
        db.rollback()
        raise


def _cancel_loa_period(period_id: int, payload: dict, request: Request, db: Session, user: V2User):
    if user.role.code not in LOA_REGISTRAR_ROLE_CODES:
        raise HTTPException(403, "仅TA GSM、GSM、AM、OM、景点圈HR可以撤销LOA")
    row = db.get(EmployeeLOAPeriod, period_id)
    if not row or row.status == "cancelled":
        raise HTTPException(404, "LOA记录不存在或已撤销")
    employee = db.get(Employee, row.employee_id)
    if not employee:
        raise HTTPException(404, "员工不存在")
    if employee.id == user.id:
        raise HTTPException(403, "不能撤销本人的LOA")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "请填写撤销原因")
    try:
        before = loa_period_payload(row, employee)
        # ended_* keeps the end registrar; cancellation is recorded in audit.
        change = loa_commands.cancel_period(db, employee, row, today=loa_today(), gate=ensure_month_open, reason=reason)
        loa_commands.recalculate_changed_months(db, employee, change.changed_months)
        write_audit(db, user.employee, "撤销LOA", "employee_loa_period", row.id, before=before, after=loa_period_payload(row, employee), reason=reason, ip_address=client_ip(request))
        db.commit()
    except Exception:
        db.rollback()
        raise
    invalidate_data_caches()
    return {"ok": True}


@router.get("/hr/employees")
def hr_employees(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    employee_query = db.query(Employee)
    if allowed_attractions is not None:
        employee_query = employee_query.filter(Employee.attraction_id.in_(allowed_attractions))
    employees = employee_query.order_by(Employee.employee_no).all()
    payloads = employee_payloads(db, employees)
    return [payloads[employee.id] for employee in employees]


@router.get("/hr/import-template")
def employee_import_template(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    wb = build_employee_import_template()
    watermark_workbook(wb, user.employee.employee_no)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    write_audit(db, user.employee, "下载员工导入模板", "employee_import_template", None)
    db.commit()
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="employee_import_template_v3.xlsx"'})


@router.post("/hr/import-employees")
async def import_employees(request: Request, workbook: UploadFile = File(...), preview: bool = Form(False),
                           skip_existing: bool = Form(False), expected_digest: str = Form(''),
                           db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    from app.services.employee_import import preflight
    from app.services.employee_types import expire_accounts, business_today
    if Path(workbook.filename or "").suffix.lower() != ".xlsx":
        raise HTTPException(400, "请上传xlsx文件")
    content = await workbook.read(5 * 1024 * 1024 + 1)
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "导入文件不能为空且不能超过5MB")
    try:
        if not preview:
            db.rollback()
            db.execute(text('BEGIN IMMEDIATE'))
        allowed = scoped_hr_attraction_ids(db, user)
        roles = {r.code:r for r in db.query(Role).all()}
        allowed_roles = set(roles) if "SYSTEM_ADMIN" in user.permissions else CIRCLE_HR_MANAGED_ROLE_CODES
        batch = preflight(db,user,content,allowed,allowed_roles)
        report = {k:v for k,v in batch.items() if k!='rows'}
        if preview:
            return {"ok":True,"preview":True,"created":0,**report}
        if expected_digest and expected_digest!=batch['digest']:
            raise HTTPException(409,'文件、员工名单或日期已变化，请重新预检后确认')
        if batch['duplicates'] and (not skip_existing or not expected_digest):
            raise HTTPException(409,'员工已存在，请先预检并明确选择跳过；已有员工不会被覆盖')
        if batch['expired'] and not expected_digest:
            raise HTTPException(409,'包含实习已到期人员，请先预检并确认创建为停用状态')
        for row in batch['rows']:
            employee=Employee(employee_no=row['employee_no'],name=row['name'],attraction_id=row['attraction_id'],
                is_active=True,hired_on=business_today().isoformat(),employee_type=row['employee_type'],internship_ends_on=row['internship_ends_on'])
            db.add(employee);db.flush()
            from app.rotation.temporary import adopt
            adopt(db,employee)
            db.add(EmployeeRoleAssignment(employee_id=employee.id,role_id=roles[row['role_code']].id,
                starts_on=business_today().isoformat(),assignment_type='permanent',status='active',reason='HR Excel导入',created_by=user.id))
            db.add(UserAccount(employee_id=employee.id,login_account=employee.employee_no,
                password_hash=hash_password(default_initial_password(employee.employee_no)),enabled=True,
                must_change_password=True,credential_initialized=True))
            synchronize_gsm_management_scope(db,employee,row['role_code'])
        db.flush()
        for row in batch['rows']:
            employee=db.query(Employee).filter_by(employee_no=row['employee_no']).one()
            expire_accounts(db,employee.id)
        write_audit(db,user.employee,'Excel导入员工','employee_import',after={'created':len(batch['rows']),
            'skipped':len(batch['duplicates']),'expired':len(batch['expired']),'filename':workbook.filename},ip_address=client_ip(request))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409,'员工号刚刚已被注册，请重新预检') from exc
    except Exception:
        db.rollback()
        raise
    invalidate_data_caches()
    return {'ok':True,'created':len(batch['rows']),'skipped':len(batch['duplicates']),**report}


@router.post("/hr/employees")
def create_employee(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    try:
        db.rollback()
        db.execute(text("BEGIN IMMEDIATE"))
        return _create_employee(payload, request, db, user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "该员工号刚刚已被注册，请刷新后确认") from exc
    except Exception:
        db.rollback()
        raise


def _create_employee(payload: dict, request: Request, db: Session, user: V2User):
    from app.services.employee_types import normalize as normalize_type, expire_accounts
    employee_type, internship_ends_on = normalize_type(payload.get('employee_type'), payload.get('internship_ends_on'))
    employee_no = str(payload.get("employee_no") or "").strip()
    name = str(payload.get("name") or "").strip()
    role = db.query(Role).filter(Role.code == str(payload.get("role_code") or "")).first()
    if not employee_no or not name or not role:
        raise HTTPException(400, "员工号、姓名和角色必填")
    if len(employee_no) != 7 or not employee_no.isdigit():
        raise HTTPException(400, "员工号必须为7位纯数字")
    ensure_hr_role_allowed(user, role.code)
    existing = db.query(Employee).filter(Employee.employee_no == employee_no).first()
    if db.query(UserAccount).filter(UserAccount.login_account == employee_no).first() or (existing and not existing.account_deleted_at):
        raise HTTPException(400, "该员工号已存在，不能重复创建账号")
    attraction_id = employee_circle_id(db, payload.get("attraction_id"))
    ensure_scoped_hr_attraction(db, user, attraction_id)
    from app.rotation.temporary import pending, adopt
    pending(db,employee_no,name,attraction_id)
    rehire = bool(existing)
    previous_deleted_at = None
    if existing:
        ensure_scoped_hr_employee(db, user, existing)
        old_role = role_at(db, existing.id)
        if old_role:
            ensure_hr_role_allowed(user, old_role.code)
        if db.query(UserAccount).filter(UserAccount.employee_id == existing.id).first():
            raise HTTPException(409, "该历史档案仍有关联账号，请先核对")
        if name != existing.name:
            raise HTTPException(409, "该工号属于已删除档案，但姓名不一致，请核对是否为同一人员")
        if role.code in {"TA_SUPERVISOR", "TA_GSM"}:
            raise HTTPException(400, "重新入职请先选择本职，注册后再设置代理职务")
        starts_on = parse_iso_date(str(payload.get("starts_on") or payload.get("hired_on") or date.today().isoformat()), "入职日期").isoformat()
        if starts_on != date.today().isoformat():
            raise HTTPException(400, "重新注册以本次入职当天生效，请使用今天的入职日期")
        ensure_month_open(db, starts_on[:7], existing.attraction_id, "重新入职注册")
        ensure_month_open(db, starts_on[:7], attraction_id, "重新入职注册")
        previous_deleted_at = existing.account_deleted_at.isoformat()
        restart_employment(db, existing, attraction_id, starts_on)
        employee = existing
    else:
        starts_on = str(payload.get("starts_on") or date.today().isoformat())
        employee = Employee(employee_no=employee_no, name=name, attraction_id=attraction_id, is_active=True, hired_on=str(payload.get("hired_on") or date.today().isoformat()))
        db.add(employee)
    employee.employee_type = employee_type
    employee.internship_ends_on = internship_ends_on
    db.flush()
    adopt(db,employee)
    ends_on = str(payload.get("ends_on") or "").strip() or None
    return_role = db.query(Role).filter(Role.code == str(payload.get("return_role_code") or "")).first() if ends_on else None
    if ends_on:
        if not return_role:
            raise HTTPException(400, "临时角色必须选择有效的到期恢复角色")
        ensure_hr_role_allowed(user, return_role.code, "到期恢复角色")
    db.add(
        EmployeeRoleAssignment(
            employee_id=employee.id,
            role_id=role.id,
            starts_on=starts_on,
            ends_on=ends_on,
            assignment_type="temporary" if ends_on else "permanent",
            return_role_id=return_role.id if return_role else None,
            status="active",
            reason="HR重新入职注册" if rehire else "HR新建员工",
            created_by=user.id,
        )
    )
    db.flush()
    synchronize_gsm_management_scope(db, employee, role.code)
    requested_password = str(payload.get("password") or "").strip()
    # New accounts use the employee-number suffix by default. The user must
    # change it on the first successful login; HR may still explicitly provide
    # a different initial password when needed.
    temporary_password = requested_password or default_initial_password(employee_no)
    db.add(
        UserAccount(
            employee_id=employee.id,
            login_account=employee_no,
            password_hash=hash_password(temporary_password),
            enabled=True,
            must_change_password=True,
            credential_initialized=True,
        )
    )
    db.flush()
    expire_accounts(db, employee.id)
    write_audit(db, user.employee, "重新入职注册" if rehire else "新建员工", "employee", employee.id,
                before={"previous_deleted_at": previous_deleted_at} if rehire else None,
                after={"employee_no": employee_no, "role": role.name, "rehired": rehire, "history_retained": True,
                       'employee_type': employee_type, 'internship_ends_on': internship_ends_on}, ip_address=client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {
        "ok": True,
        "employee": employee_payload(db, employee),
        "temporary_password": temporary_password,
        "must_change_password": True,
        "rehired": rehire,
    }


@router.get("/hr/employee-number-targets")
def employee_number_targets(
    keyword: str = "",
    limit: int = 30,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("HR_MANAGE")),
):
    """Search current or historic employee numbers without exposing HR accounts."""
    if user.role.code not in {SCOPED_HR_ROLE_CODE, "SYSTEM_ADMIN"}:
        raise HTTPException(403, "仅景点圈HR和最高管理员可以变更员工号")
    normalized = str(keyword or "").strip()
    if not normalized:
        return {"items": [], "total": 0}
    escaped = normalized.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    pattern = f"%{escaped}%"
    candidate_rows = (
        db.query(Employee, EmployeeNumberHistory.old_employee_no)
        .join(UserAccount, UserAccount.employee_id == Employee.id)
        .outerjoin(EmployeeNumberHistory, EmployeeNumberHistory.employee_id == Employee.id)
        .filter(
            or_(
                Employee.employee_no.like(pattern, escape="!"),
                Employee.name.like(pattern, escape="!"),
                EmployeeNumberHistory.old_employee_no.like(pattern, escape="!"),
            )
        )
        .order_by(Employee.name, Employee.employee_no, EmployeeNumberHistory.id.desc())
        .limit(max(1, min(int(limit or 30) * 3, 150)))
        .all()
    )
    items: list[dict] = []
    seen: set[int] = set()
    for employee, matched_old_number in candidate_rows:
        if employee.id in seen:
            continue
        role = role_at(db, employee.id)
        try:
            ensure_employee_number_change_target(db, user, employee, role)
        except HTTPException:
            continue
        seen.add(employee.id)
        items.append(
            {
                "id": employee.id,
                "employee_no": employee.employee_no,
                "name": employee.name,
                "role_code": role.code,
                "role_name": role.name,
                "attraction_name": employee.attraction.name if employee.attraction else "未分配景点圈",
                "matched_historical_no": matched_old_number or "",
            }
        )
        if len(items) >= max(1, min(int(limit or 30), 50)):
            break
    return {"items": items, "total": len(items)}


@router.post("/hr/employees/{employee_id}/employee-number")
def change_employee_number(
    employee_id: int,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("HR_MANAGE")),
):
    """Migrate a login number while preserving the employee primary key and all records."""
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "员工不存在")
    role = ensure_employee_number_change_target(db, user, employee)
    new_employee_no = str(payload.get("new_employee_no") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    reset_password = bool(payload.get("reset_password"))
    if len(new_employee_no) != 7 or not new_employee_no.isdigit():
        raise HTTPException(400, "新员工号必须为7位纯数字")
    if not reason:
        raise HTTPException(400, "请填写员工号变更原因")
    if len(reason) > 300:
        raise HTTPException(400, "员工号变更原因不能超过300个字符")
    old_employee_no = employee.employee_no
    if new_employee_no == old_employee_no:
        return {"ok": True, "unchanged": True, "employee_id": employee.id, "employee_no": employee.employee_no}
    collision = db.query(Employee).filter(Employee.employee_no == new_employee_no, Employee.id != employee.id).first()
    account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
    login_collision = db.query(UserAccount).filter(UserAccount.login_account == new_employee_no).first()
    if collision or (login_collision and (not account or login_collision.id != account.id)):
        raise HTTPException(400, "新员工号已存在，不能合并或重复创建账号")
    if not account:
        raise HTTPException(400, "该员工尚未开通登录账号")
    before = {"employee_no": old_employee_no, "login_account": account.login_account, "role": role.name}
    employee.employee_no = new_employee_no
    employee.updated_at = datetime.now()
    account.login_account = new_employee_no
    account.failed_attempts = 0
    account.locked_until = None
    if reset_password:
        account.password_hash = hash_password(default_initial_password(new_employee_no))
        account.must_change_password = True
        account.credential_initialized = True
        account.password_changed_at = None
    db.add(
        EmployeeNumberHistory(
            employee_id=employee.id,
            old_employee_no=old_employee_no,
            new_employee_no=new_employee_no,
            effective_on=date.today().isoformat(),
            reason=reason,
            changed_by=user.id,
            changed_by_name=user.name,
        )
    )
    revoked_sessions = db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    write_audit(
        db,
        user.employee,
        "变更员工号",
        "employee_number_change",
        employee.id,
        before=before,
        after={
            "employee_no": new_employee_no,
            "login_account": new_employee_no,
            "role": role.name,
            "password_reset": reset_password,
            "sessions_revoked": int(revoked_sessions),
        },
        reason=reason,
        ip_address=client_ip(request),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "新员工号已存在，不能合并或重复创建账号")
    invalidate_data_caches()
    return {
        "ok": True,
        "unchanged": False,
        "employee_id": employee.id,
        "old_employee_no": old_employee_no,
        "employee_no": new_employee_no,
        "sessions_revoked": int(revoked_sessions),
        "must_change_password": bool(account.must_change_password),
        "password_reset": reset_password,
    }


@router.put("/hr/employees/{employee_id}")
def update_employee(employee_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    try:
        db.rollback()
        db.execute(text("BEGIN IMMEDIATE"))
        result = _update_employee(employee_id, payload, request, db, user)
    except Exception:
        # LOA and the surrounding employee/account/group changes are one unit.
        db.rollback()
        raise
    invalidate_data_caches()
    return result


def _update_employee(employee_id: int, payload: dict, request: Request, db: Session, user: V2User):
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "员工不存在")
    ensure_scoped_hr_employee(db, user, employee)
    existing_role = role_at(db, employee.id)
    existing_base_role = base_role_at(db, employee.id)
    if existing_role and existing_role.code not in CIRCLE_HR_MANAGED_ROLE_CODES and "SYSTEM_ADMIN" not in user.permissions:
        raise HTTPException(403, "景点圈HR只能编辑LEAD及以下员工")
    before = employee_payload(db, employee)
    context = EmployeeEditContext(
        db=db, employee=employee, payload=payload, user=user,
        existing_role=existing_role, existing_base_role=existing_base_role,
        today=date.today(), loa_today=loa_today(), month_gate=ensure_month_open,
        ip_address=client_ip(request),
    )
    warnings = apply_employee_edit(context)
    write_audit(
        db, user.employee, "修改员工", "employee", employee.id,
        before=before, after=employee_payload(db, employee),
        reason=str(payload.get("reason") or ""), ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "warnings": warnings}
