"""Monthly attendance honors. No scoring writes, external calls or automatic awards."""
from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from app.attendance_medals_models import (AttendanceMedal, MedalAccountIdentity, MedalCircleRun,
    MedalLeaveBatch, MedalLeaveMonth, MedalNotice, MedalWageCode)
from app.services.employment import employment_history
from app.services.audit import write_audit
from app.employee_numbers import employee_number, employee_number_aliases
from app.v2_models import (Attraction, AuditLog, CircleTransferRequest, Employee, EmployeeMonthOrganizationSnapshot,
    UserAccount)

FORMAL_START = "2027-01"
TEST_START = "2026-11"
RULE = "依据当月最终有效请假数据评定；任何请假影响全勤，正常休息、调休不影响，迟到早退暂不纳入。不绑定加分。"
NORMAL_CODES = frozenset({"正常工作", "正常出勤", "工作时间", "正常工时", "工作", "工时", "出勤", "基本工时",
    "加班", "加班时间", "正常休息", "休息", "调休", "调休假", "法定节假日", "法定假日", "节假日",
    "公休日", "假日", "本休", "调休时间", "调休工时", "法定节假日工时", "法定节假日工资", "节假日工资"})


def employee_content_visible():
    return date.today().isoformat() >= "2027-02-01"


def visible_notices(db, employee_id):
    return db.query(MedalNotice).filter_by(employee_id=employee_id).filter(
        MedalNotice.month >= FORMAL_START, MedalNotice.month < date.today().strftime('%Y-%m'))


def wage_kind(code, classifications=None):
    if classifications and code in classifications:
        return classifications[code]
    if code in NORMAL_CODES:
        return "normal"
    if "假" in code:
        return "leave"
    return None


def batch_issues(db, batch):
    if not batch:
        return []
    issues = json.loads(batch.issues)
    for issue in issues:
        if issue.get('reason') == '工号对应的姓名不匹配':
            issue['identity_conflict'] = True
    overrides = {r.code:r.kind for r in db.query(MedalWageCode)}
    # Older local batches may have silently ignored unrecognized transaction codes.
    existing = {r.get('leave_type') for r in issues if r.get('code_type') == 'unclassified'}
    existing |= {leave_total_type(code) for code in existing if code}
    for code in json.loads(batch.ignored_codes):
        normalized = leave_total_type(code)
        kind = wage_kind(code, overrides) or wage_kind(normalized, overrides)
        if kind is None and code not in existing and normalized not in existing:
            issues.append({'code_type':'unclassified', 'leave_type':normalized, 'circle_id':None,
                'reason':'工资代码未分类，请统一导入管理员确认分类后重新替换导入'})
            existing.update((code, normalized))
    return issues


def data_ready(state, issues):
    return bool(state and state.revision and state.confirmed_revision == state.revision
                and not any(r.get('code_type') in {'unclassified', 'classification_changed'} for r in issues))


def classify_code(db, user, code, kind, revision, reason):
    ensure_importer(user)
    code, reason = code.strip(), reason.strip()
    if not code or len(code) > 100 or any(ord(c) < 32 for c in code) or kind not in {'leave', 'normal'} or not reason:
        raise HTTPException(400, '请确认工资代码、分类并填写依据')
    entry = db.get(MedalWageCode, code)
    if (entry.revision if entry else 0) != revision:
        raise HTTPException(409, '工资代码分类已变化，请重新核对')
    if not entry and wage_kind(code) is not None:
        raise HTTPException(400, '标准请假、休息或调休代码已有固定规则，不能通过分类绕过')
    if entry and entry.kind == kind:
        return {'ok':True, 'affected_months':[]}
    before = entry.kind if entry else None
    if not entry:
        entry = MedalWageCode(code=code, revision=0)
        db.add(entry)
    entry.kind, entry.revision = kind, entry.revision + 1
    entry.updated_by, entry.updated_at = user.name, datetime.now()
    affected = []
    for state in db.query(MedalLeaveMonth).all():
        batch = latest_batch(db, state)
        if not batch:
            continue
        leaves, issues = json.loads(batch.leaves), json.loads(batch.issues)
        codes = set(json.loads(batch.ignored_codes)) | {r.get('leave_type') for r in leaves + issues}
        codes |= {leave_total_type(value) for value in codes if value}
        if code not in codes:
            continue
        # Preserve the original import batch; a new blocked revision forces a
        # source-file reimport instead of inventing missing historical leave rows.
        state.revision += 1
        state.confirmed_revision = None
        issues.append({'code_type':'classification_changed', 'leave_type':code, 'circle_id':None,
            'reason':'工资代码分类已变更，请重新替换导入原文件后核对'})
        db.add(MedalLeaveBatch(month=state.month, revision=state.revision, filename=batch.filename,
            digest=batch.digest, leaves=batch.leaves, issues=dump(issues), ignored_codes=batch.ignored_codes,
            imported_by=user.name))
        affected.append(state.month)
    write_audit(db, user.employee, '确认全勤勋章工资代码分类', 'attendance_medals', 0,
        before={'code':code, 'kind':before}, after={'code':code, 'kind':kind, 'reason':reason, 'affected_months':affected})
    db.flush()
    return {'ok':True, 'affected_months':affected}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def leave_key(row):
    return (row['employee_id'], row['date'], row['leave_type'], str(Decimal(row['hours']).normalize()))


def leave_total_type(code):
    code = re.sub(r'(?:时间)?总计$', '', code)
    return '病假时间总计' if code in {'法定病假', '全薪病假', '病假'} else code


def month_bounds(month):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", str(month)):
        raise HTTPException(400, "请选择有效月份")
    try:
        first = date.fromisoformat(month + "-01")
    except ValueError as exc:
        raise HTTPException(400, "请选择有效月份") from exc
    if month < TEST_START:
        raise HTTPException(400, "全勤勋章从2026年11月开放管理端试用，2027年1月正式累计")
    return first.isoformat(), f"{month}-{calendar.monthrange(first.year, first.month)[1]:02}"


def is_admin(user):
    return "SYSTEM_ADMIN" in user.permissions


def can_manage(user):
    return is_admin(user) or user.has_role("HR_CIRCLE")


def ensure_scope(db, user, circle_id):
    if not can_manage(user):
        raise HTTPException(403, "仅景点圈管理员或最高管理员可发放全勤勋章")
    circle = db.get(Attraction, circle_id)
    if not circle or not circle.employee_circle or not circle.active:
        raise HTTPException(400, "请选择有效景点圈")
    if not is_admin(user) and circle_id != user.employee.attraction_id:
        raise HTTPException(403, "只能操作本景点圈全勤勋章")


def ensure_importer(user):
    if not is_admin(user) and ("SICK_LEAVE_IMPORT" not in user.permissions or user.has_role("HR_CIRCLE")):
        raise HTTPException(403, "请由具有全员导入权限的管理员统一处理")


def circle_on(db, employee, month):
    return roster(db, month).get(str(employee.id))


def roster(db, month):
    _, end = month_bounds(month)
    snapshots = {s.employee_id:s.attraction_id for s in db.query(EmployeeMonthOrganizationSnapshot).filter_by(score_month=month)}
    transfers = {}
    # Direct HR edits and workbook updates already retain their before-images.
    # Combine those with transfer events instead of assuming today's circle was
    # also the employee's circle at the end of a historical month.
    events = []
    cutoff = datetime.fromisoformat(end + 'T23:59:59.999999')
    for transfer in db.query(CircleTransferRequest).filter(
            CircleTransferRequest.status == 'completed', CircleTransferRequest.completed_at > cutoff):
        events.append((transfer.completed_at, transfer.id, transfer.employee_id, transfer.source_attraction_id))
    for audit in db.query(AuditLog).filter(AuditLog.entity_type == 'employee', AuditLog.created_at > cutoff,
                                          AuditLog.before_json.is_not(None)):
        try:
            before, after = json.loads(audit.before_json), json.loads(audit.after_json or '{}')
            if 'attraction_id' in before and 'attraction_id' in after and before['attraction_id'] != after['attraction_id']:
                events.append((audit.created_at, audit.id, int(audit.entity_id), before['attraction_id']))
        except (ValueError, TypeError):
            continue
    for _, _, eid, source in sorted(events, key=lambda r:(r[0], r[1]), reverse=True):
        transfers[eid] = source
    result = {}
    for employee in db.query(Employee).order_by(Employee.id):
        circle = snapshots.get(employee.id, transfers.get(employee.id, employee.attraction_id))
        if employee.id not in snapshots:
            for period in employment_history(employee):
                if (period.get("hired_on") or "0001-01-01") <= end <= (period.get("terminated_on") or "9999-12-31"):
                    circle = period.get("attraction_id", circle)
                    break
        result[str(employee.id)] = circle
    return result


def qualification(db, employee, month, context=None):
    start, end = month_bounds(month)
    from app.services.identity import base_role_at
    role = context['roles'].get(employee.id) if context is not None else base_role_at(db, employee.id, end)
    if role and role.code in {"HR_CIRCLE", "HR_ADMIN", "SYSTEM_ADMIN"}:
        return "不参与", "管理专用账号不属于员工评定范围", None
    identity = context['identities'].get(employee.id) if context is not None else db.get(MedalAccountIdentity, employee.id)
    account = context['accounts'].get(employee.id) if context is not None else db.query(UserAccount).filter_by(employee_id=employee.id).first()
    created = identity.first_created_at if identity else account.created_at if account else None
    if not created:
        return ("待处理", "历史账号创建日期待核实", None) if employee.account_deleted_at else ("不参与", "无正式员工账号", None)
    canonical = employee_number(employee.employee_no)
    if not re.fullmatch(r'[0-9]{7}', canonical):
        return '待处理', '员工号格式异常，请先核实正式员工身份', None
    duplicate = (canonical in context['duplicate_numbers']) if context is not None else (
        db.query(Employee.id).filter(Employee.employee_no.in_(employee_number_aliases(canonical))).limit(2).count() > 1)
    if duplicate:
        return '待处理', '员工号对应多个档案，请先处理重复身份', None
    next_month = (date(created.year + 1, 1, 1) if created.month == 12 else date(created.year, created.month + 1, 1)).strftime("%Y-%m")
    periods = employment_history(employee) + [{"hired_on": employee.hired_on, "terminated_on": employee.terminated_on}]
    active_period = None
    for period in reversed(periods):
        hired, terminated = period.get("hired_on"), period.get("terminated_on")
        if hired and hired > end or terminated and terminated < start:
            continue
        # A missing current termination date on an inactive employee is ambiguous.
        if period is periods[-1] and not employee.is_active and not terminated:
            return "待处理", "离职日期缺失，不能以账号或当前状态替代历史在职日期", next_month
        active_period = period
        break
    if active_period is None:
        return "不参与", "该月不在职", next_month
    hired, terminated = active_period.get("hired_on"), active_period.get("terminated_on")
    # Rehire always starts with the next full natural month.
    if any(p.get("rehired_on") == hired for p in employment_history(employee)) and hired:
        hire_date = date.fromisoformat(hired)
        next_month = max(next_month, (date(hire_date.year + 1, 1, 1) if hire_date.month == 12 else date(hire_date.year, hire_date.month + 1, 1)).strftime("%Y-%m"))
        if hired[:7] >= month:
            return "不参与", "重新入职当月不参与", next_month
    if next_month > month:
        return "不参与", "账号创建当月或尚未到首次参与月份", next_month
    if hired and hired > start:
        return "不参与", "月中入职，未覆盖完整月份", next_month
    if terminated and terminated < end:
        return "不参与", "月中离职", next_month
    return "拟发放", "符合完整月份资格且无有效请假", next_month


def read_all_leaves(db, content, month):
    """Read every leave code separately; never broaden sick-leave score deductions."""
    from app.routers.sick_leave_import import (_locate_workbook, _header_index, _cell, _source_id,
        _system_number, _date_value, _matching_employee, _import_name_matches, _transaction_months, _chinese_name)
    book, sheet, header, stop, summary_header = _locate_workbook(content)
    columns = [_header_index(sheet.row_values(header), label) for label in ("员工", "ID", "日期", "工资代码", "时数")]
    if _transaction_months(content) != {month}:
        raise HTTPException(400, "文件日期必须覆盖且只属于所选月份；空文件不能证明数据完整")
    leaves, issues, ignored, seen = [], [], set(), set()
    classifications = {r.code:r.kind for r in db.query(MedalWageCode)}
    historical_circles = roster(db, month)
    transaction_totals, summary_totals, summary_names = {}, {}, {}
    def locate_issue(issue):
        try:
            no = _system_number(_source_id(issue.get('employee_no') or issue.get('source_no')))
            employee = _matching_employee(db, no)
            if employee:
                issue.update(employee_id=employee.id, circle_id=historical_circles.get(str(employee.id)))
                names = issue.get('source_names') or [issue.get('source_name')]
                if any(name and not _import_name_matches(employee.name, _chinese_name(name)) for name in names):
                    issue['identity_conflict'] = True
        except (ValueError, HTTPException):
            pass
        return issue
    for index in range(header + 1, stop):
        row = sheet.row_values(index)
        name_i, no_i, date_i, type_i, hours_i = columns
        code = _cell(row, type_i)
        if not code:
            if _cell(row, no_i) and _cell(row, date_i):
                issue = {'row':index + 1, 'source_name':_cell(row, name_i), 'source_no':_cell(row, no_i),
                         'circle_id':None, 'reason':'事务缺少工资代码，请修正原文件后重新导入'}
                try:
                    employee = _matching_employee(db, _system_number(_source_id(row[no_i])))
                    if employee:
                        issue.update(employee_id=employee.id, circle_id=historical_circles.get(str(employee.id)))
                except (ValueError, HTTPException):
                    pass
                issues.append(issue)
            continue
        kind = wage_kind(code, classifications)
        if kind == 'normal':
            ignored.add(code)
            continue
        if kind is None:
            issues.append({'row':index + 1, 'code_type':'unclassified', 'leave_type':code, 'circle_id':None,
                'reason':'工资代码未分类，请统一导入管理员确认分类后重新替换导入'})
            continue
        item = {"row": index + 1, "leave_type": code, "source_name": _cell(row, name_i),
                "source_no": _cell(row, no_i), "circle_id": None}
        try:
            no = _system_number(_source_id(row[no_i]))
            day = _date_value(book, row[date_i])
            item['employee_no'] = no
            hours = Decimal(str(row[hours_i]).strip())
            if not hours.is_finite() or hours <= 0:
                raise ValueError("请假时数必须为正数")
            total_key = (no, leave_total_type(code))
            transaction_totals[total_key] = transaction_totals.get(total_key, Decimal('0')) + hours
            employee = _matching_employee(db, no)
            if not employee:
                raise ValueError("工号无法匹配")
            item.update(employee_id=employee.id, circle_id=historical_circles.get(str(employee.id)))
            item.update(employee_no=no, date=day, hours=str(hours))
            if not _import_name_matches(employee.name, _chinese_name(item["source_name"])):
                item['identity_conflict'] = True
                raise ValueError("工号对应的姓名不匹配")
            key = (employee.id, day, code, hours)
            if key not in seen:
                leaves.append(item)
                seen.add(key)
        except (ValueError, InvalidOperation, IndexError, HTTPException) as exc:
            item["reason"] = str(exc.detail if isinstance(exc, HTTPException) else exc)
            issues.append(locate_issue(item))
    summary_columns = [_header_index(sheet.row_values(summary_header), label) for label in ('员工', 'ID', '工资代码', '时数')]
    for index in range(summary_header + 1, sheet.nrows):
        row = sheet.row_values(index)
        name_i, no_i, type_i, hours_i = summary_columns
        code = _cell(row, type_i)
        if not code:
            continue
        normalized = leave_total_type(code)
        kinds = {wage_kind(code, classifications), wage_kind(normalized, classifications)}
        if 'normal' in kinds:
            ignored.update((code, normalized))
            continue
        if 'leave' not in kinds:
            if _cell(row, no_i):
                issues.append({'row':index + 1, 'code_type':'unclassified', 'leave_type':normalized,
                    'circle_id':None, 'reason':'总数区工资代码未分类，请核实后重新替换导入'})
            continue
        try:
            no = _system_number(_source_id(row[no_i]))
            hours = Decimal(str(row[hours_i]).strip())
            if not hours.is_finite() or hours < 0:
                raise ValueError('总数区请假时数必须为非负数')
            key = (no, normalized)
            summary_totals[key] = summary_totals.get(key, Decimal('0')) + hours
            summary_names.setdefault(key, []).append(_cell(row, name_i))
        except (ValueError, InvalidOperation, IndexError) as exc:
            issues.append(locate_issue({'row':index + 1, 'circle_id':None, 'source_no':row[no_i] if no_i < len(row) else '',
                'source_name':_cell(row, name_i), 'reason':f'总数区数据异常：{exc}'}))
    for key in sorted(set(transaction_totals) | set(summary_totals)):
        actual, expected = transaction_totals.get(key, Decimal('0')), summary_totals.get(key, Decimal('0'))
        if actual != expected:
            issues.append(locate_issue({'circle_id':None, 'employee_no':key[0], 'leave_type':key[1], 'source_no':key[0],
                'source_names':summary_names.get(key, []),
                'reason':f'请假事务与总数区不一致：事务 {actual} 小时，总数 {expected} 小时，请修正原文件'}))
    return leaves, issues, sorted(ignored)


def store_import(db, user, month, content, filename, mode="replace"):
    ensure_importer(user)
    month_bounds(month)
    if mode not in {"append", "replace"}:
        raise HTTPException(400, "请选择追加或替换整月数据")
    leaves, issues, ignored = read_all_leaves(db, content, month)
    state = db.get(MedalLeaveMonth, month)
    if not state:
        state = MedalLeaveMonth(month=month, revision=0)
        db.add(state)
    old = latest_batch(db, state)
    if mode == "append" and old:
        indexed = {leave_key(r): r for r in json.loads(old.leaves) + leaves}
        leaves = list(indexed.values())
        issues = json.loads(old.issues) + issues
        ignored = sorted(set(json.loads(old.ignored_codes)) | set(ignored))
    state.revision += 1
    state.confirmed_revision = None
    batch = MedalLeaveBatch(month=month, revision=state.revision, filename=filename[:255],
        digest=hashlib.sha256(content).hexdigest(), leaves=dump(leaves), issues=dump(issues),
        ignored_codes=dump(ignored), imported_by=user.name)
    db.add(batch)
    write_audit(db, user.employee, "导入全勤勋章请假数据", "attendance_medals", 0,
        after={"month": month, "revision": state.revision, "mode": mode, "leaves": len(leaves), "issues": len(issues), "digest": batch.digest})
    db.flush()
    return {"month": month, "revision": state.revision, "leaves": len(leaves), "issues": issues,
            "ignored_codes": ignored, "previous_leaves": len(json.loads(old.leaves)) if old else 0}


def latest_batch(db, state):
    return db.query(MedalLeaveBatch).filter_by(month=state.month, revision=state.revision).first() if state and state.revision else None


def confirm_data(db, user, month, revision, refresh=False, reason=""):
    ensure_importer(user)
    state = db.get(MedalLeaveMonth, month)
    batch = latest_batch(db, state)
    if not batch or revision != state.revision:
        raise HTTPException(409, "导入数据已变化或尚未导入，请重新核对")
    if any(r.get('code_type') in {'unclassified', 'classification_changed'} for r in batch_issues(db, batch)):
        raise HTTPException(409, '请先完成工资代码分类并重新替换导入，不能将未知代码视为无请假')
    if refresh and (not is_admin(user) or not reason.strip()):
        raise HTTPException(403, "仅最高管理员可填写原因后修正历史圈归属")
    if not state.roster or refresh:
        state.roster = dump(roster(db, month))
    state.confirmed_revision, state.confirmed_by, state.confirmed_at = state.revision, user.name, datetime.now()
    write_audit(db, user.employee, "确认全勤勋章数据完整", "attendance_medals", 0,
        after={"month": month, "revision": revision, "refresh_roster": refresh, "reason": reason})


def preview(db, user, month, circle_id):
    ensure_scope(db, user, circle_id)
    start, end = month_bounds(month)
    state = db.get(MedalLeaveMonth, month)
    batch = latest_batch(db, state)
    assignment = json.loads(state.roster) if state and state.roster else roster(db, month)
    leaves = json.loads(batch.leaves) if batch else []
    # Resolve the saved employee identity, so changing a number never loses a leave.
    issues = []
    all_employees = db.query(Employee).order_by(Employee.employee_no).all()
    employees_by_id = {e.id:e for e in all_employees}
    valid_circles = {c.id for c in db.query(Attraction).filter_by(employee_circle=True, active=True)}
    all_issues = batch_issues(db, batch)
    for issue in all_issues:
        owner = None if issue.get('identity_conflict') else assignment.get(str(issue.get('employee_id')), issue.get('circle_id'))
        if owner in valid_circles and owner != circle_id:
            continue
        if not is_admin(user):
            employee = employees_by_id.get(issue.get('employee_id')) if owner == circle_id else None
            # A row may pair this circle's number with another person's name.
            # Only the global importer can inspect that unverified source pair.
            label = f'{employee.name}（{employee.employee_no}）的导入数据待核实' if employee else '存在归属未明确或工资代码待核实的数据'
            issue = {'redacted':True, 'reason':label + '，请联系统一导入管理员处理'}
        issues.append(issue)
    rows = []
    number_counts = {}
    for employee in all_employees:
        key = employee_number(employee.employee_no)
        number_counts[key] = number_counts.get(key, 0) + 1
    employees = [e for e in all_employees if assignment.get(str(e.id)) == circle_id]
    from app.services.identity import base_roles_at
    context = {'roles': base_roles_at(db, [e.id for e in employees], end),
        'identities': {i.employee_id:i for i in db.query(MedalAccountIdentity)},
        'accounts': {a.employee_id:a for a in db.query(UserAccount)},
        'duplicate_numbers': {key for key, count in number_counts.items() if count > 1}}
    leaves_by_employee = {}
    for detail in leaves:
        leaves_by_employee.setdefault(detail['employee_id'], []).append(detail)
    for employee in employees:
        if assignment.get(str(employee.id)) != circle_id:
            continue
        status, reason, first = qualification(db, employee, month, context)
        details = leaves_by_employee.get(employee.id, [])
        if status == "拟发放" and details:
            status, reason = "未获得", "当月存在有效请假记录"
        rows.append({"employee_id": employee.id, "employee_no": employee.employee_no, "name": employee.name,
            "status": status, "reason": reason, "first_month": first, "leaves": details,
            "needs_identity_date": reason == '历史账号创建日期待核实'})
    ready = bool(batch and data_ready(state, all_issues))
    fingerprint = digest(rows)
    outcome_fingerprint = digest([{'employee_id': r['employee_id'], 'status': r['status'], 'reason': r['reason']} for r in sorted(rows, key=lambda r:r['employee_id'])])
    run = db.query(MedalCircleRun).filter_by(month=month, attraction_id=circle_id).first()
    last_completed_month = (date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    historical_initial = month >= FORMAL_START and month < last_completed_month and run is None
    previous = {r["employee_id"]:r for r in json.loads(run.results)} if run else {}
    changes = []
    current = {r["employee_id"]:r for r in rows}
    for eid in sorted(set(previous) | set(current)):
        before, after = previous.get(eid), current.get(eid)
        if bool(before and before["status"] == "拟发放") != bool(after and after["status"] == "拟发放"):
            changes.append({"employee_id": eid, "name": (after or before)["name"],
                "action": "补发" if after and after["status"] == "拟发放" else "撤回",
                "reason": after["reason"] if after else "归属已修正"})
    closed = end < date.today().isoformat()
    return {"month": month, "attraction_id": circle_id, "trial": month < FORMAL_START,
        "revision": state.revision if state else 0, "fingerprint": fingerprint, "outcome_fingerprint": outcome_fingerprint,
        "status": "待数据就绪" if not ready else "待调整" if run and run.fingerprint != outcome_fingerprint else "已发放" if run else "待确认发放",
        "requires_highest": historical_initial,
        "can_publish": ready and not issues and (closed or month < FORMAL_START)
            and not (historical_initial and not is_admin(user)) and not any(r["status"] == "待处理" for r in rows),
        "rows": rows, "issues": issues, "changes": changes,
        "confirmed_by": run.confirmed_by if run else None,
        "confirmed_at": str(run.confirmed_at) if run else None,
        "data_confirmed_by": state.confirmed_by if ready else None,
        "data_imported_by": batch.imported_by if batch else None,
        "data_imported_at": str(batch.imported_at) if batch else None,
        "ignored_codes": json.loads(batch.ignored_codes) if batch else [],
        "month_ended": closed, "rule": RULE}


def publish(db, user, month, circle_id, revision, fingerprint, reason=""):
    result = preview(db, user, month, circle_id)
    if result['requires_highest'] and not is_admin(user):
        raise HTTPException(403, '历史月份首次补发需由最高管理员核对确认')
    if revision != result["revision"] or fingerprint != result["fingerprint"]:
        raise HTTPException(409, "数据或人员资格已变化，请重新核对名单")
    if not result["can_publish"]:
        raise HTTPException(409, "请先确认全员数据完整、解决异常；正式月份须等待月份结束")
    run = db.query(MedalCircleRun).filter_by(month=month, attraction_id=circle_id).first()
    if run and run.fingerprint == result['outcome_fingerprint']:
        return {"ok": True, "duplicate": True, "awarded": 0, "revoked": 0}
    if run and not reason.strip():
        raise HTTPException(400, "调整已发放结果需要填写原因")
    if not run:
        run = MedalCircleRun(month=month, attraction_id=circle_id)
        db.add(run)
    awarded = revoked = 0
    intended = {r["employee_id"] for r in result["rows"] if r["status"] == "拟发放"}
    medals = {m.employee_id: m for m in db.query(AttendanceMedal).filter_by(month=month).all()}
    actions = []
    for row in result["rows"]:
        eid = row["employee_id"]
        medal = medals.get(eid)
        if medal and medal.attraction_id != circle_id:
            if not is_admin(user):
                raise HTTPException(409, "跨圈历史归属纠错需由最高管理员处理")
            medal.attraction_id = circle_id
        if eid in intended and (not medal or medal.status != "awarded"):
            if not medal:
                medal = AttendanceMedal(employee_id=eid, month=month, attraction_id=circle_id)
                db.add(medal)
            medal.status, medal.attraction_id, medal.updated_at = "awarded", circle_id, datetime.now()
            medal.issued_at = medal.updated_at
            awarded += 1
            actions.append((eid, f"你获得了{month}月度全勤勋章", reason))
        elif medal and medal.status == "awarded" and eid not in intended:
            medal.status, medal.updated_at = "revoked", datetime.now()
            revoked += 1
            actions.append((eid, f"{month}月度全勤勋章已撤回", reason or row["reason"]))
    # An administrator may have corrected a frozen circle assignment. Move the
    # existing monthly honor without creating a second honor or notification.
    assignment = json.loads(db.get(MedalLeaveMonth, month).roster)
    for medal in medals.values():
        target_circle = assignment.get(str(medal.employee_id))
        if medal.attraction_id == circle_id and target_circle != circle_id:
            if not is_admin(user):
                raise HTTPException(409, "存在跨圈历史归属调整，请最高管理员处理")
            if target_circle:
                medal.attraction_id = target_circle
    run.fingerprint, run.results = result['outcome_fingerprint'], dump(result["rows"])
    run.confirmed_by, run.confirmed_at = user.name, datetime.now()
    if month >= FORMAL_START:
        for eid, message, why in actions:
            db.add(MedalNotice(employee_id=eid, month=month, message=message, reason=why))
    write_audit(db, user.employee, "确认全勤勋章发放" if not reason else "调整全勤勋章", "attendance_medal_circle", circle_id,
        after={"month": month, "revision": revision, "fingerprint": fingerprint, "awarded": awarded,
            "revoked": revoked, "reason": reason, "trial": month < FORMAL_START,
            "historical_initial": result['requires_highest'], "results": result["rows"]})
    db.flush()
    return {"ok": True, "awarded": awarded, "revoked": revoked, "trial": month < FORMAL_START}


def collection(db, user, employee_id):
    # Trial honors never reach employee-facing APIs, including managers' public profiles.
    medals = db.query(AttendanceMedal).filter_by(employee_id=employee_id, status="awarded").filter(
        AttendanceMedal.month >= FORMAL_START, AttendanceMedal.month < date.today().strftime('%Y-%m')
    ).order_by(AttendanceMedal.month.desc()).all()
    visible = employee_content_visible()
    if not visible:
        return {"visible": False, "items": [], "count": 0}
    result = {"visible": True, "items": [{"month": m.month, "issued_at": str(m.issued_at)} for m in medals],
              "count": len(medals), "rule": RULE}
    if user.id == employee_id:
        result["notices"] = [{"id": n.id, "message": n.message, "reason": n.reason, "read": bool(n.read_at)}
            for n in visible_notices(db, employee_id).order_by(MedalNotice.id.desc()).limit(50)]
        private = []
        for run in db.query(MedalCircleRun).filter(MedalCircleRun.month >= FORMAL_START,
                MedalCircleRun.month < date.today().strftime('%Y-%m')).order_by(MedalCircleRun.month.desc()).all():
            state = db.get(MedalLeaveMonth, run.month)
            if state and state.roster and json.loads(state.roster).get(str(employee_id)) != run.attraction_id:
                continue
            row = next((r for r in json.loads(run.results) if r["employee_id"] == employee_id), None)
            if row:
                private.append({"month": run.month, **row})
        result["assessments"] = private
        previous_month = (date.today().replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
        if previous_month >= FORMAL_START and not any(r['month'] == previous_month for r in private):
            state = db.get(MedalLeaveMonth, previous_month)
            result['pending_month'] = {'month': previous_month, 'status': '待确认发放'
                if data_ready(state, batch_issues(db, latest_batch(db, state))) else '待数据就绪'}
    return result
