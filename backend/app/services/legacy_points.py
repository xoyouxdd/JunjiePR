"""Formal-GSM-only historical opening balances; caller serializes every mutation."""
from datetime import date, datetime, timedelta
from io import BytesIO
import json
import math
from uuid import uuid4
from zipfile import ZipFile

from fastapi import HTTPException
from openpyxl import load_workbook

from app.activity_points_models import ActivityPointsEntry, LegacyPointsBalance, LegacyPointsExport, LegacyPointsPreview
from app.employee_numbers import employee_number, employee_number_cell
from app.services.activity_points import LEVELS, START_POLICY, START_POLICY_VERSION, digest
from app.services.identity import base_role_at
from app.services.employment import employment_history
from app.services.legacy_points_xlsx import HEADERS, PREVIOUS_HEADERS
from app.v2_models import Attraction, AuditLog, CircleTransferRequest, Employee, EmployeeRoleAssignment, EmployeeMonthOrganizationSnapshot

LIMIT = 1_000_000


def formal_gsm(db, user):
    today = date.today().isoformat()
    role = base_role_at(db, user.id, today)
    assignment = db.query(EmployeeRoleAssignment).filter(EmployeeRoleAssignment.employee_id == user.id,
        EmployeeRoleAssignment.status != 'cancelled', EmployeeRoleAssignment.starts_on <= today,
        (EmployeeRoleAssignment.ends_on.is_(None) | (EmployeeRoleAssignment.ends_on >= today))).order_by(
            EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc()).first()
    temporary = assignment is not None and assignment.assignment_type != 'permanent'
    if not user.employee.is_active or temporary or not role or role.code != "GSM" or user.base_role.code != "GSM" or user.role.code != "GSM" or user.has_role("HR_ADMIN", "HR_CIRCLE", "SYSTEM_ADMIN"):
        raise HTTPException(403, "仅正式GSM可操作历史积点补录")


def circles(db, user):
    formal_gsm(db, user)
    return db.query(Attraction).filter(
        Attraction.active.is_(True), Attraction.employee_circle.is_(True), Attraction.name.in_(START_POLICY)).order_by(Attraction.id).all()


def ensure_circle(db, user, circle_id):
    found = next((c for c in circles(db, user) if c.id == circle_id), None)
    if not found:
        raise HTTPException(403, "只能处理已开放历史积点补录的景点圈")
    return found


def _employed(employee, day):
    current = (employee.hired_on or "0001-01-01") <= day and (not employee.terminated_on or day < employee.terminated_on)
    return current or any((p.get('hired_on') or '0001-01-01') <= day < (p.get('terminated_on') or '9999-12-31') for p in employment_history(employee))


def _events(db):
    result = []
    for row in db.query(CircleTransferRequest).filter_by(status="completed"):
        if row.completed_at:
            result.append((row.completed_at, row.id, row.employee_id, row.source_attraction_id))
    for row in db.query(AuditLog).filter(AuditLog.entity_type == 'employee', AuditLog.before_json.is_not(None)):
        try:
            before, after = json.loads(row.before_json), json.loads(row.after_json or '{}')
            if 'attraction_id' in before and 'attraction_id' in after and before['attraction_id'] != after['attraction_id']:
                result.append((row.created_at, row.id, int(row.entity_id), before['attraction_id']))
        except (ValueError, TypeError, KeyError):
            continue
    return sorted(result, key=lambda r: (r[0], r[1]), reverse=True)


def historical_ranges(db, employee, roster_cache, events):
    """Split historical time at role, employment, transfer and policy boundaries."""
    assignments = db.query(EmployeeRoleAssignment).filter(EmployeeRoleAssignment.employee_id == employee.id,
        EmployeeRoleAssignment.status != 'cancelled', EmployeeRoleAssignment.starts_on < '2026-11-01').all()
    boundaries = {'2026-09-01', '2026-10-01', '2026-11-01'}
    for row in assignments:
        boundaries.add(row.starts_on)
        if row.ends_on:
            boundaries.add((date.fromisoformat(row.ends_on) + timedelta(days=1)).isoformat())
    for value in [employee.hired_on, employee.terminated_on]:
        if value:
            boundaries.add(value)
    for p in employment_history(employee):
        boundaries.update(p[k] for k in ['hired_on', 'terminated_on'] if p.get(k))
    for stamp, _, eid, _ in events:
        if eid == employee.id:
            boundaries.add(stamp.date().isoformat())
    boundaries = sorted(d for d in boundaries if d <= '2026-11-01')
    catalog = {c.id: c.name for c in db.query(Attraction).filter(Attraction.name.in_(START_POLICY))}
    ranges = []
    for start, stop in zip(boundaries, boundaries[1:]):
        if not _employed(employee, start):
            continue
        role = base_role_at(db, employee.id, start)
        if not role or role.code not in LEVELS:
            continue
        month = start[:7]
        if month not in roster_cache:
            roster_cache[month] = {r.employee_id: r.attraction_id for r in db.query(EmployeeMonthOrganizationSnapshot).filter_by(score_month=month)}
        snapshot = roster_cache[month]
        circle_id = snapshot.get(employee.id, employee.attraction_id)
        end_of_day = datetime.fromisoformat(start + 'T23:59:59.999999')
        # Month-end snapshots are authoritative; rewind changes within that month.
        for stamp, _, eid, previous in events:
            if eid == employee.id and stamp > end_of_day and (employee.id not in snapshot or stamp.strftime('%Y-%m') == month):
                circle_id = previous
        for p in employment_history(employee):
            if (p.get('hired_on') or '0001-01-01') <= start < (p.get('terminated_on') or '9999-12-31'):
                circle_id = p.get('attraction_id', circle_id)
                break
        circle = catalog.get(circle_id)
        if not circle:
            continue
        group = 'frontline' if role.code in {'CM', 'TR'} else 'management'
        exclusive_end = min(stop, START_POLICY[circle][group])
        if start >= exclusive_end:
            continue
        end = (date.fromisoformat(exclusive_end) - timedelta(days=1)).isoformat()
        item = {'start': start, 'end': end, 'circle': circle, 'group': group}
        if ranges and ranges[-1]['circle'] == circle and ranges[-1]['group'] == group and (date.fromisoformat(ranges[-1]['end']) + timedelta(days=1)).isoformat() == start:
            ranges[-1]['end'] = end
        else:
            ranges.append(item)
    return ranges


def roster(db, user, circle_id):
    circle = ensure_circle(db, user, circle_id)
    balances = {r.employee_id: r for r in db.query(LegacyPointsBalance)}
    cache, events, rows = {}, _events(db), []
    candidates = db.query(Employee).filter((Employee.attraction_id == circle_id) | Employee.attraction_id.is_(None)).order_by(Employee.employee_no)
    for employee in candidates:
        owner = employee.attraction_id
        if owner is None and (not employee.is_active or employee.account_deleted_at):
            periods = sorted(employment_history(employee), key=lambda p: p.get('terminated_on') or p.get('hired_on') or '', reverse=True)
            owner = next((p['attraction_id'] for p in periods if p.get('attraction_id') is not None), None)
            if owner is None:
                owner = next((previous for _, _, eid, previous in events if eid == employee.id and previous is not None), None)
        if owner != circle_id:
            continue
        reference = employee.terminated_on or date.today().isoformat()
        role = base_role_at(db, employee.id, reference)
        if role and role.code.startswith(('HR', 'SYSTEM')):
            continue
        ranges = historical_ranges(db, employee, cache, events)
        if not role and not ranges:
            continue
        state = balances.get(employee.id)
        coverage = '；'.join(f"{r['circle']} {'CM/TR' if r['group']=='frontline' else '主管及以上'} {r['start']}至{r['end']}" for r in ranges)
        mixed = len({(r['circle'], r['group']) for r in ranges}) > 1 or len(ranges) > 1 or any(r['circle'] != circle.name for r in ranges)
        if role and role.code in LEVELS:
            group = 'frontline' if role.code in {'CM', 'TR'} else 'management'
            mixed = mixed or any(r['group'] != group for r in ranges)
        rows.append({'employee_id': employee.id, 'employee_no': employee.employee_no, 'name': employee.name,
            'circle': circle.name, 'circle_id': circle.id, 'role': role.name if role else '历史业务身份',
            'status': '在职' if employee.is_active and not employee.account_deleted_at else '已归档',
            'cutoff': max((r['end'][:7] for r in ranges), default=''), 'coverage': coverage or '无可核实的起算前在职业务身份',
            'ranges': ranges, 'needs_review': mixed, 'blocked': not bool(ranges),
            'total': state.total if state else 0, 'revision': state.revision if state else 0})
    return rows


def public_row(row):
    return {k: v for k, v in row.items() if k not in {'ranges'}}


def make_export(db, user, circle_id):
    rows = roster(db, user, circle_id)
    batch = LegacyPointsExport(id=uuid4().hex, circle_id=circle_id, created_by=user.id,
        roster_json=json.dumps(rows, ensure_ascii=False))
    db.add(batch); db.flush()
    return batch, rows


def _parse(content):
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(413, 'Excel文件不能超过5MB')
    try:
        with ZipFile(BytesIO(content)) as archive:
            if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 30 * 1024 * 1024:
                raise HTTPException(413, 'Excel解压后过大')
        book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
        if '名单' not in book or '_control' not in book:
            raise ValueError('请使用本页面导出的模板')
        token = book['_control']['A1'].value
        sheet = book['名单']
        if sheet.max_row > 10005 or sheet.max_column > len(HEADERS):
            raise ValueError('名单最多10000行，请勿增加列')
        if [sheet.cell(3, c).value for c in range(1, len(HEADERS)+1)] not in (HEADERS, PREVIOUS_HEADERS):
            raise ValueError('模板列名已变化，请重新导出')
        rows = [(i, list(row)) for i, row in enumerate(sheet.iter_rows(min_row=4, max_col=len(HEADERS)), 4)]
        book.close()
        return token, rows
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, 'Excel无法读取，请使用原始.xlsx模板：'+str(exc)[:100]) from exc


def preview(db, user, circle_id, content):
    circle = ensure_circle(db, user, circle_id)
    export_id, uploaded = _parse(content)
    batch = db.get(LegacyPointsExport, str(export_id))
    if not batch or datetime.now() - batch.created_at > timedelta(days=30):
        raise HTTPException(409, '模板无效或已过期，请重新导出')
    if batch.circle_id != circle_id:
        raise HTTPException(409, f'名单所属圈与当前选择的「{circle.name}」不一致，请切换到名单所属圈后再导入')
    expected = {employee_number(r['employee_no']): r for r in json.loads(batch.roster_json)}
    current = {r['employee_id']: r for r in roster(db, user, circle_id)}
    errors, changes, seen = [], [], set()
    counts = {'new': 0, 'adjusted': 0, 'unchanged': 0, 'blank': 0, 'increase': 0, 'decrease': 0}
    for line, cells in uploaded:
        values = [c.value for c in cells]
        if all(v is None for v in values):
            continue
        def error(message): errors.append({'row': line, 'employee_no': str(values[0] or ''), 'message': message})
        if any(c.data_type == 'f' for c in cells):
            error('不接受公式，请填写实际积点整数');continue
        number = employee_number_cell(values[0])
        if number in seen:
            error('工号重复');continue
        seen.add(number)
        old = expected.get(number)
        if not old:
            error('工号不在本次导出名单中');continue
        live = current.get(old['employee_id'])
        if not live or any(live[k] != old[k] for k in ['employee_no', 'name', 'circle_id', 'role', 'status', 'cutoff', 'coverage', 'blocked']):
            error('人员身份、归属或历史范围已变化，请重新导出');continue
        fixed = [old['name'], old['circle'], old['role'], old['status'], old['cutoff'], old['total']]
        if [str(v or '') for v in values[1:6]] != [str(v or '') for v in fixed[:5]] or values[6] != fixed[5] or str(values[9] or '') != old['coverage']:
            error('姓名、归属、历史范围或已存积点列被修改');continue
        value = values[7]
        if value is None or isinstance(value, str) and not value.strip():
            counts['blank'] += 1;continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not 0 <= value <= LIMIT:
            error(f'核定积点必须为0至{LIMIT}的整数');continue
        if live['blocked']:
            error('历史身份未核实，不能补录');continue
        value, note = int(value), str(values[8] or '').strip()
        if len(note) > 500:
            error('备注最多500字');continue
        delta = value - live['total']
        if delta and live['revision'] != old['revision']:
            error('已存积点已被其他导入更新，请重新导出');continue
        if delta and (live['revision'] or live['needs_review']) and not note:
            error('修正积点或历史归属/职级有变化时，必须填写核对原因');continue
        counts['unchanged' if not delta else 'new' if not live['revision'] else 'adjusted'] += 1
        counts['increase'] += max(delta, 0);counts['decrease'] += max(-delta, 0)
        changes.append({'employee_id': live['employee_id'], 'employee_no': live['employee_no'], 'name': live['name'],
            'previous': live['total'], 'total': value, 'delta': delta, 'revision': live['revision'], 'note': note,
            'identity': digest({k: live[k] for k in ['employee_no', 'name', 'circle_id', 'role', 'status', 'coverage', 'blocked']}),
            'cutoff': live['cutoff'], 'coverage': live['coverage']})
    result = {'circle_id': circle_id, 'circle_name': circle.name, 'summary': counts, 'errors': errors, 'changes': changes,
              'policy': START_POLICY_VERSION, 'file_digest': digest(content.hex())}
    token = uuid4().hex
    db.add(LegacyPointsPreview(id=token, export_id=batch.id, created_by=user.id, payload_json=json.dumps(result, ensure_ascii=False)))
    db.flush()
    return {'preview_id': token, **result, 'can_apply': not errors and any(c['delta'] for c in changes)}


def apply(db, user, preview_id):
    formal_gsm(db, user)
    record = db.get(LegacyPointsPreview, preview_id)
    if not record or record.created_by != user.id:
        raise HTTPException(404, '预览不存在')
    payload = json.loads(record.payload_json)
    circle = ensure_circle(db, user, payload['circle_id'])
    if record.applied_at:
        return {**json.loads(record.result_json), 'duplicate': True}
    if datetime.now() - record.created_at > timedelta(minutes=30) or payload['policy'] != START_POLICY_VERSION:
        raise HTTPException(409, '预览已过期，请重新导入预览')
    if payload['errors']:
        raise HTTPException(400, '有错误行，整批不入账')
    current = {r['employee_id']: r for r in roster(db, user, circle.id)}
    for item in payload['changes']:
        live = current.get(item['employee_id'])
        if not live or live['revision'] != item['revision'] or digest({k: live[k] for k in ['employee_no', 'name', 'circle_id', 'role', 'status', 'coverage', 'blocked']}) != item['identity']:
            raise HTTPException(409, '人员或积点已变化，请重新预览')
    changed = 0
    for item in payload['changes']:
        if not item['delta']:
            continue
        state = db.get(LegacyPointsBalance, item['employee_id'])
        if not state:
            state = LegacyPointsBalance(employee_id=item['employee_id'], revision=0)
            db.add(state)
        state.total, state.revision, state.updated_by, state.updated_at = item['total'], item['revision']+1, user.id, datetime.now()
        db.add(ActivityPointsEntry(employee_id=item['employee_id'], recognition_id=None, revision=state.revision,
            delta=item['delta'], kind='legacy_import', origin='legacy_import', business_date=date.today().isoformat(),
            detail_json=json.dumps({'category': '历史积点补录' if not item['revision'] else '历史积点修正', 'signer_name': user.name,
                'cutoff': item['cutoff'], 'coverage': item['coverage'], 'note': item['note'], 'preview_id': preview_id,
                'previous': item['previous'], 'total': item['total']}, ensure_ascii=False)))
        changed += 1
    result = {'ok': True, 'changed': changed, **payload['summary']}
    record.applied_at, record.result_json = datetime.now(), json.dumps(result, ensure_ascii=False)
    db.add(AuditLog(operator_id=user.id, operator_name=user.name, action='历史积点补录', entity_type='legacy_points',
        entity_id=preview_id, attraction_id=circle.id, after_json=json.dumps(payload, ensure_ascii=False)))
    db.flush()
    return result
