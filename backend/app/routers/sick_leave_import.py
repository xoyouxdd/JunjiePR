"""Monthly sick-leave import from the HR transaction workbook."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import secrets
import re
from threading import Lock

import xlrd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import case, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.v2_auth import V2User, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, Employee, EmployeeLOAPeriod, SickLeaveRecord
from app.v2_services import recalculate_attendance, role_at, write_audit
from app.routers._shared import client_ip, ensure_month_open, ensure_scoped_hr_employee, existing_submission, invalidate_data_caches, like_escaped_pattern, normalize_request_key, parse_iso_date, remember_submission, scoped_hr_attraction_ids, sick_leave_payloads, submission_payload_digest
from app.routers.sick_leaves import active_sick_leave_overlaps, sick_leave_overlap_detail


router = APIRouter()
_PREVIEWS: dict[str, dict] = {}
_PREVIEWS_LOCK = Lock()
_MAX_WORKBOOK_BYTES = 10 * 1024 * 1024
_MAX_CACHED_PREVIEWS = 8
_MAX_CACHED_BYTES = 40 * 1024 * 1024
_PREVIEW_TTL = timedelta(minutes=20)
_SICK_TYPES = {"法定病假", "全薪病假", "无薪病假"}
_SINGLE_SOURCE = "single_backup"
_SOURCE_ID_LENGTH = 8
_TRANSACTION_MARKERS = ("事务:", "事务：")
_SUMMARY_MARKERS = ("总数:", "总数：")
_HEADER_SCAN_ROWS = 20
_TRANSACTION_COLUMNS = ("员工", "ID", "日期", "工资代码", "时数")
_SUMMARY_COLUMNS = ("员工", "ID", "工资代码", "时数")
_PREVIEW_EXPIRED_MESSAGE = "预检结果已失效（超时、服务重启或被较新的预检挤出），请重新上传文件预检"


def _prune_previews(now: datetime) -> None:
    """Caller holds _PREVIEWS_LOCK; retain only recent, bounded previews."""
    for token, preview in list(_PREVIEWS.items()):
        if now - preview["created_at"] > _PREVIEW_TTL:
            _PREVIEWS.pop(token, None)
    while _PREVIEWS and (
        len(_PREVIEWS) > _MAX_CACHED_PREVIEWS
        or sum(len(item["content"]) for item in _PREVIEWS.values()) > _MAX_CACHED_BYTES
    ):
        oldest = min(_PREVIEWS, key=lambda token: _PREVIEWS[token]["created_at"])
        _PREVIEWS.pop(oldest, None)


def _store_preview(token: str, preview: dict) -> None:
    with _PREVIEWS_LOCK:
        _prune_previews(datetime.now())
        _PREVIEWS[token] = preview
        _prune_previews(datetime.now())


def _get_preview(token: str, user_id: int) -> dict | None:
    with _PREVIEWS_LOCK:
        _prune_previews(datetime.now())
        preview = _PREVIEWS.get(token)
        return preview if preview and preview["user_id"] == user_id else None


def _drop_preview(token: str) -> None:
    with _PREVIEWS_LOCK:
        _PREVIEWS.pop(token, None)


def _cell(row: list, index: int) -> str:
    return str(row[index]).strip() if index < len(row) and row[index] not in (None, "") else ""


def _find_row(sheet, labels: tuple[str, ...]) -> int:
    """Locate a block marker; half- and full-width colons are both accepted."""
    for index in range(sheet.nrows):
        if any(_layout_label(value) in {_layout_label(label) for label in labels} for value in sheet.row_values(index)):
            return index
    raise HTTPException(400, f"未找到“{labels[0]}”区块")


def _header_index(row: list, name: str) -> int:
    for index, value in enumerate(row):
        if _layout_label(value) == name:
            return index
    raise HTTPException(400, f"导入文件缺少“{name}”列")


def _layout_label(value) -> str:
    return re.sub(r"\s+", "", str(value)).rstrip(":：")


def _locate_header(sheet, marker: int, end: int, required: tuple[str, ...], block: str) -> int:
    start, stop = marker + 1, min(end, marker + 1 + _HEADER_SCAN_ROWS)
    candidates, nearby = [], []
    for index in range(start, stop):
        labels = [_layout_label(value) for value in sheet.row_values(index)]
        present = {name for name in required if name in labels}
        if present:
            nearby.append((len(present), index, labels))
        if len(present) == len(required):
            duplicates = [name for name in required if labels.count(name) > 1]
            if duplicates:
                raise HTTPException(400, f"工作表“{sheet.name}”第 {index + 1} 行：{block}表头重复列：{'、'.join(duplicates)}")
            candidates.append(index)
    if len(candidates) > 1:
        raise HTTPException(400, f"工作表“{sheet.name}”第 {'、'.join(str(i + 1) for i in candidates)} 行：{block}存在多个表头，无法唯一定位")
    if candidates:
        return candidates[0]
    if nearby:
        _, index, labels = max(nearby, key=lambda item: item[0])
        missing = [name for name in required if name not in labels]
        raise HTTPException(400, f"工作表“{sheet.name}”第 {index + 1} 行：{block}疑似表头缺少列：{'、'.join(missing)}")
    raise HTTPException(400, f"工作表“{sheet.name}”第 {marker + 1} 行：{block}标记后第 {start + 1} 至 {max(start + 1, stop)} 行未找到完整表头")


def _locate_workbook(content: bytes):
    """Accept exactly one worksheet with a complete, unambiguous block layout."""
    try:
        book = xlrd.open_workbook(file_contents=content)
    except (xlrd.biffh.XLRDError, xlrd.compdoc.CompDocError, ValueError, IndexError, EOFError) as exc:
        raise HTTPException(400, "无法读取工作簿，无法定位行号：请上传未损坏的 Excel 97-2003 格式（.xls）文件") from exc
    candidates, problems = [], []
    for sheet in book.sheets():
        markers = {"事务": [], "总数": []}
        for index in range(sheet.nrows):
            for value in sheet.row_values(index):
                label = _layout_label(value)
                if label in markers and index not in markers[label]:
                    markers[label].append(index)
        try:
            for name, indices in markers.items():
                if not indices:
                    raise HTTPException(400, f"工作表“{sheet.name}”第 1 至 {max(sheet.nrows, 1)} 行：未找到“{name}:”区块")
                if len(indices) > 1:
                    raise HTTPException(400, f"工作表“{sheet.name}”第 {'、'.join(str(i + 1) for i in indices)} 行：存在多个“{name}:”区块，无法唯一定位")
            transaction, summary = markers["事务"][0], markers["总数"][0]
            if transaction >= summary:
                raise HTTPException(400, f"工作表“{sheet.name}”第 {transaction + 1}、{summary + 1} 行：事务区必须位于总数区之前")
            t_header = _locate_header(sheet, transaction, summary, _TRANSACTION_COLUMNS, "事务区")
            s_header = _locate_header(sheet, summary, sheet.nrows, _SUMMARY_COLUMNS, "总数区")
            candidates.append((sheet, t_header, summary, s_header))
        except HTTPException as exc:
            problems.append(str(exc.detail))
    if len(candidates) > 1:
        positions = [f"“{s.name}”第 {t + 1}、{h + 1} 行" for s, t, _, h in candidates]
        raise HTTPException(400, f"多个工作表符合导入结构：{'；'.join(positions)}；无法唯一定位，请只保留一份导入报表")
    if not candidates:
        raise HTTPException(400, "无法定位导入报表：" + "；".join(problems or ["工作簿中没有工作表，无法定位行号"]))
    return book, *candidates[0]


def _date_value(book, value) -> str:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return xlrd.xldate_as_datetime(value, book.datemode).date().isoformat()
        return date.fromisoformat(str(value).strip().replace("/", "-")).isoformat()
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"事务日期无效：{value}") from exc


def _hours_value(value) -> Decimal:
    """Parse an hours cell; xlrd returns numeric cells as float (e.g. 8.0)."""
    raw = repr(value) if isinstance(value, float) else str(value).strip()
    try:
        return Decimal(raw).quantize(Decimal("0.01"))
    except ArithmeticError as exc:
        raise ValueError(f"时数无效：{value}") from exc


def _chinese_name(value: str) -> str:
    return value.split(",")[-1].strip().replace("，", "").strip()


def _source_id(value) -> str:
    """Normalize an ID cell to the 8-digit source ID.

    Leading zeros can be lost either way: numeric-formatted cells come back
    from xlrd as float (1727264.0), and text cells may be exported as "1727264".
    Both are left-padded; the name check during preview still guards the match.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"源文件 ID 必须为 {_SOURCE_ID_LENGTH} 位数字：{value}")
        value = int(value)
    if isinstance(value, int):
        return str(value).zfill(_SOURCE_ID_LENGTH) if value >= 0 else str(value)
    text = str(value).strip() if value is not None else ""
    return text.zfill(_SOURCE_ID_LENGTH) if text.isdigit() else text


def _system_number(source_id: str) -> str:
    value = str(source_id).strip()
    if len(value) != _SOURCE_ID_LENGTH or not value.isdigit():
        raise ValueError(f"源文件 ID 必须为 {_SOURCE_ID_LENGTH} 位数字：{value or '空'}")
    return value[1:]


class _SummaryTotals(dict):
    """Keep source locations alongside totals for actionable reconciliation errors."""
    def __init__(self, sheet_name: str):
        super().__init__()
        self.sheet_name = sheet_name
        self.rows: dict[tuple[str, str], list[int]] = {}


def _read_workbook(content: bytes) -> tuple[list[dict], dict[tuple[str, str], Decimal], list[str]]:
    book, sheet, transaction_header_row, summary_marker, summary_header_row = _locate_workbook(content)
    transaction_header = sheet.row_values(transaction_header_row)
    summary_header = sheet.row_values(summary_header_row)
    t_name, t_id, t_date, t_type, t_hours = (_header_index(transaction_header, label) for label in ("员工", "ID", "日期", "工资代码", "时数"))
    s_name, s_id, s_type, s_hours = (_header_index(summary_header, label) for label in ("员工", "ID", "工资代码", "时数"))
    records: list[dict] = []
    errors: list[str] = []
    for row_index in range(transaction_header_row + 1, summary_marker):
        row = sheet.row_values(row_index)
        leave_type = _cell(row, t_type)
        if leave_type not in _SICK_TYPES:
            continue
        source_name = _cell(row, t_name)
        try:
            source_id = _source_id(row[t_id])
            hours = _hours_value(row[t_hours])
            employee_no = _system_number(source_id)
            leave_date = _date_value(book, row[t_date])
        except (ValueError, IndexError) as exc:
            errors.append(f"事务区第 {row_index + 1} 行（工作表“{sheet.name}”）：{exc}")
            continue
        if hours <= 0 or hours % 4 != 0:
            errors.append(f"事务区第 {row_index + 1} 行（工作表“{sheet.name}”）：时数必须是大于 0 的 4 小时倍数")
            continue
        records.append({
            "row": row_index + 1, "sheet_name": sheet.name, "source_id": source_id, "employee_no": employee_no,
            "source_name": source_name, "name": _chinese_name(source_name), "date": leave_date,
            "leave_type": leave_type, "hours": hours, "days": hours / Decimal("8"),
        })
    summaries = _SummaryTotals(sheet.name)
    for row_index in range(summary_header_row + 1, sheet.nrows):
        row = sheet.row_values(row_index)
        summary_type = _cell(row, s_type)
        if summary_type not in {"病假时间总计", "无薪病假"}:
            continue
        try:
            # Same normalization as the transaction block so _reconcile keys match.
            source_id = _source_id(row[s_id])
            _system_number(source_id)
            hours = _hours_value(row[s_hours])
        except (ValueError, IndexError) as exc:
            errors.append(f"总数区第 {row_index + 1} 行（工作表“{sheet.name}”）：{exc}")
            continue
        summaries[(source_id, summary_type)] = hours
        summaries.rows.setdefault((source_id, summary_type), []).append(row_index + 1)
    return records, summaries, errors


def _reconcile(records: list[dict], summaries: dict[tuple[str, str], Decimal]) -> list[str]:
    totals: dict[tuple[str, str], Decimal] = {}
    for row in records:
        summary_type = "无薪病假" if row["leave_type"] == "无薪病假" else "病假时间总计"
        key = (row["source_id"], summary_type)
        totals[key] = totals.get(key, Decimal("0")) + row["hours"]
    errors = []
    for key in set(totals) | set(summaries):
        if totals.get(key, Decimal("0")) != summaries.get(key, Decimal("0")):
            summary_rows = getattr(summaries, "rows", {}).get(key, [])
            transaction_rows = [row["row"] for row in records if row["source_id"] == key[0] and ("无薪病假" if row["leave_type"] == "无薪病假" else "病假时间总计") == key[1] and "row" in row]
            location = ""
            if summary_rows:
                location = f"（工作表“{summaries.sheet_name}”总数区第 {'、'.join(map(str, summary_rows))} 行）"
            elif transaction_rows:
                location = f"（事务区第 {'、'.join(map(str, transaction_rows))} 行）"
            errors.append(f"总数区对账不一致{location}：ID {key[0]} 的 {key[1]}，事务区 {totals.get(key, 0)} 小时，总数区 {summaries.get(key, 0)} 小时")
    return errors


def _transaction_months(content: bytes) -> set[str]:
    """Use every transaction date to prove the workbook belongs to one month."""
    book, sheet, header_row, end, _ = _locate_workbook(content)
    header = sheet.row_values(header_row)
    date_index = _header_index(header, "日期")
    type_index = _header_index(header, "工资代码")
    months: set[str] = set()
    for index in range(header_row + 1, end):
        row = sheet.row_values(index)
        if not _cell(row, type_index):
            continue
        try:
            months.add(_date_value(book, row[date_index])[:7])
        except (ValueError, IndexError) as exc:
            raise HTTPException(400, f"工作表“{sheet.name}”事务区第 {index + 1} 行：日期无效，不能确认文件月份：{exc}") from exc
    return months


def _active_month_rows(db: Session, user: V2User, month: str) -> list[SickLeaveRecord]:
    query = db.query(SickLeaveRecord).join(Employee, SickLeaveRecord.employee_id == Employee.id).filter(
        SickLeaveRecord.attendance_month == month,
        SickLeaveRecord.status == "active",
    )
    allowed = scoped_hr_attraction_ids(db, user)
    if allowed is not None:
        query = query.filter(Employee.attraction_id.in_(allowed))
    return query.all()


@router.post("/sick-leave-imports/preview")
async def preview_sick_leave_import(
    workbook: UploadFile = File(...), month: str = Form(...), db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SICK_LEAVE_IMPORT")),
):
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "请选择有效的导入月份")
    parse_iso_date(f"{month}-01", "导入月份")
    if not (workbook.filename or "").lower().endswith(".xls"):
        raise HTTPException(400, "请上传 .xls 格式的员工事务文件")
    content = await workbook.read(_MAX_WORKBOOK_BYTES + 1)
    if len(content) > _MAX_WORKBOOK_BYTES:
        raise HTTPException(413, "员工事务文件不能超过10MB")
    records, summaries, errors = _read_workbook(content)
    errors.extend(_reconcile(records, summaries))
    months = _transaction_months(content)
    if months != {month}:
        errors.append("文件事务日期与所选月份不一致，或无法从空缺勤文件确认月份；不能覆盖")
        errors.extend(
            f"工作表“{row.get('sheet_name', '导入报表')}”第 {row['row']} 行：事务日期 {row['date']} 不属于所选月份 {month}"
            for row in records if row["date"][:7] != month
        )
    unmatched, matched, loa_protected = [], [], []
    for row in records:
        employee = db.query(Employee).filter(Employee.employee_no == row["employee_no"]).first()
        reason = ""
        if not employee:
            reason = "系统中不存在该员工号"
        elif employee.name != row["name"]:
            reason = f"姓名不匹配（系统：{employee.name}）"
        elif not employee.is_active:
            reason = "员工当前不在职"
        else:
            try:
                ensure_scoped_hr_employee(db, user, employee)
            except HTTPException:
                reason = "不在当前账号可导入的景点圈范围"
        if reason:
            unmatched.append({**row, "reason": reason})
        else:
            period = db.query(EmployeeLOAPeriod).filter(
                EmployeeLOAPeriod.employee_id == employee.id,
                EmployeeLOAPeriod.status != "cancelled",
                EmployeeLOAPeriod.starts_on <= row["date"],
                (EmployeeLOAPeriod.ends_on.is_(None) | (EmployeeLOAPeriod.ends_on >= row["date"])),
            ).first()
            if period:
                loa_protected.append({**row, "employee_id": employee.id, "reason": f"LOA保护：{period.starts_on} 至 {period.ends_on or '至今'}"})
            else:
                matched.append({**row, "employee_id": employee.id})
    token = secrets.token_urlsafe(24)
    old_rows = _active_month_rows(db, user, month)
    file_employee_ids = {row["employee_id"] for row in matched + loa_protected}
    removed_employee_ids = {row.employee_id for row in old_rows} - file_employee_ids
    _store_preview(token, {"created_at": datetime.now(), "filename": workbook.filename, "content": content, "month": month, "matched": matched, "unmatched": unmatched, "loa_protected": loa_protected, "errors": errors, "user_id": user.id})
    return {
        "token": token, "month": month, "blocking_errors": errors,
        "matched_employee_count": len({row["employee_id"] for row in matched}), "matched_record_count": len(matched),
        "replaced_record_count": len(old_rows), "absent_employee_count": len(removed_employee_ids),
        "unmatched": unmatched, "skipped_unmatched_count": len(unmatched),
        "loa_protected": loa_protected, "can_commit": not errors,
    }


@router.get("/sick-leave-imports/employee-targets")
def single_sick_leave_targets(
    keyword: str = "", attraction_id: int | None = None, limit: int = 30,
    db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SICK_LEAVE_IMPORT")),
):
    """The backup entrance permits lookup of every active employee, across roles/circles."""
    keyword = keyword.strip()
    limit = max(1, min(limit, 50))
    if not keyword:
        return {"items": [], "total": 0, "limit": limit}
    pattern = like_escaped_pattern(keyword)
    query = db.query(Employee).filter(
        Employee.is_active.is_(True),
        or_(Employee.name.like(pattern, escape="\\"), Employee.employee_no.like(pattern, escape="\\")),
    )
    if attraction_id is not None:
        query = query.filter(Employee.attraction_id == attraction_id)
    total = query.count()
    employees = query.order_by(
        case((Employee.employee_no == keyword, 0), (Employee.name == keyword, 1), else_=2),
        Employee.name, Employee.employee_no,
    ).limit(limit).all()
    circles = {circle.id: circle.name for circle in db.query(Attraction).all()}
    items = []
    for employee in employees:
        role = role_at(db, employee.id)
        items.append({
            "id": employee.id, "employee_no": employee.employee_no, "name": employee.name,
            "role_code": role.code if role else "", "role_name": role.name if role else "未分配角色",
            "attraction_id": employee.attraction_id, "attraction_name": circles.get(employee.attraction_id, ""),
            "group_id": None, "group_name": "",
        })
    return {"items": items, "total": total, "limit": limit}


@router.post("/sick-leave-imports/single")
def create_single_sick_leave(
    request: Request,
    employee_id: int = Form(...), leave_start_date: str = Form(...), leave_end_date: str = Form(...),
    leave_days: str = Form(...), leave_type: str = Form(...), note: str = Form(""),
    rest_day_confirmed: bool = Form(False), idempotency_key: str | None = Form(None),
    db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SICK_LEAVE_IMPORT")),
):
    start = parse_iso_date(leave_start_date, "病假开始日期")
    end = parse_iso_date(leave_end_date, "病假结束日期")
    if end < start or start.strftime("%Y-%m") != end.strftime("%Y-%m"):
        raise HTTPException(400, "病假日期无效，跨月请分开登记")
    if leave_type not in _SICK_TYPES:
        raise HTTPException(400, "请选择法定病假、全薪病假或无薪病假")
    try:
        days = Decimal(leave_days)
    except InvalidOperation as exc:
        raise HTTPException(400, "病假天数必须是数字") from exc
    if not days.is_finite() or days <= 0 or days * 2 != (days * 2).to_integral_value():
        raise HTTPException(400, "病假天数必须按0.5天递增")
    if days > (end - start).days + 1:
        raise HTTPException(400, "病假天数不能超过日期范围")
    note = note.strip()
    if len(note) > 300:
        raise HTTPException(400, "备注不能超过300字")
    request_key = normalize_request_key(idempotency_key)
    payload_digest = submission_payload_digest({
        "employee_id": employee_id, "start": start.isoformat(), "end": end.isoformat(),
        "days": str(days.normalize()), "type": leave_type, "note": note,
    })
    try:
        # Acquire SQLite's write lock before the overlap/replay checks so two
        # simultaneous registrations cannot both accept the same date interval.
        locked = db.execute(update(Employee).where(Employee.id == employee_id, Employee.is_active.is_(True)).values(updated_at=Employee.updated_at))
        if locked.rowcount != 1:
            raise HTTPException(400, "请选择存在且在职的员工")
        duplicate = existing_submission(db, user.id, "sick_leave_backup", request_key, SickLeaveRecord, payload_digest)
        if duplicate:
            result = {"ok": True, "record": sick_leave_payloads(db, [duplicate])[0], "duplicate": True}
            db.rollback()
            return result
        if end > start and not rest_day_confirmed:
            raise HTTPException(409, detail={"code": "SICK_LEAVE_REST_DAY_CONFIRMATION_REQUIRED", "message": "请确认病假天数中不含演职人员本休"})
        employee = db.get(Employee, employee_id)
        month = start.strftime("%Y-%m")
        ensure_month_open(db, month, employee.attraction_id, "登记单人病假")
        period = db.query(EmployeeLOAPeriod).filter(
            EmployeeLOAPeriod.employee_id == employee_id, EmployeeLOAPeriod.status != "cancelled",
            EmployeeLOAPeriod.starts_on <= end.isoformat(),
            or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= start.isoformat()),
        ).first()
        if period:
            raise HTTPException(409, detail={"code": "SICK_LEAVE_LOA_CONFLICT", "message": f"病假日期与该员工LOA期间（{period.starts_on} 至 {period.ends_on or '未结束'}）重叠，请核对LOA记录"})
        overlaps = active_sick_leave_overlaps(db, employee_id, start.isoformat(), end.isoformat())
        if overlaps:
            raise HTTPException(409, detail=sick_leave_overlap_detail(overlaps))
        role = role_at(db, employee_id, start)
        row = SickLeaveRecord(
            employee_id=employee_id, employee_no_snapshot=employee.employee_no, employee_name_snapshot=employee.name,
            employee_role_snapshot=role.name if role else "", attraction_id_snapshot=employee.attraction_id,
            attendance_month=month, leave_start_date=start.isoformat(), leave_end_date=end.isoformat(),
            leave_days=days, charged_days=days, leave_type=leave_type, import_source=_SINGLE_SOURCE,
            proof_file_id=None, note=note or None, status="active", submitted_by=user.id, submitted_by_name=user.name,
            is_violation=False,
        )
        db.add(row)
        db.flush()
        remember_submission(db, user.id, "sick_leave_backup", request_key, row.id, payload_digest)
        attendance = recalculate_attendance(db, employee, month)
        write_audit(db, user.employee, "单人备用病假登记", "sick_leave", row.id, after={
            "employee_id": employee_id, "employee_no": employee.employee_no, "attraction_id": employee.attraction_id,
            "month": month, "start": row.leave_start_date, "end": row.leave_end_date, "days": float(days),
            "leave_type": leave_type, "import_source": _SINGLE_SOURCE, "note": note,
            "attendance_score": float(attendance.final_score), "score_eligible": attendance.eligible,
        }, ip_address=client_ip(request))
        result = {"ok": True, "record": sick_leave_payloads(db, [row])[0], "attendance_score": float(attendance.final_score), "score_eligible": attendance.eligible, "duplicate": False}
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = existing_submission(db, user.id, "sick_leave_backup", request_key, SickLeaveRecord, payload_digest)
        if duplicate:
            return {"ok": True, "record": sick_leave_payloads(db, [duplicate])[0], "duplicate": True}
        raise
    except Exception:
        db.rollback()
        raise
    invalidate_data_caches()
    return result


@router.get("/sick-leave-imports/records")
def list_sick_leave_import_records(
    month: str | None = None,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("SICK_LEAVE_IMPORT")),
):
    query = db.query(SickLeaveRecord).filter(SickLeaveRecord.import_source.in_({"monthly_transaction_import", _SINGLE_SOURCE}))
    if month:
        parse_iso_date(f"{month}-01", "月份")
        query = query.filter(SickLeaveRecord.attendance_month == month)
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None:
        query = query.filter(or_(
            SickLeaveRecord.attraction_id_snapshot.in_(allowed_attractions),
            (SickLeaveRecord.import_source == _SINGLE_SOURCE) & (SickLeaveRecord.submitted_by == user.id),
        ))
    rows = query.order_by(SickLeaveRecord.submitted_at.desc(), SickLeaveRecord.id.desc()).limit(500).all()
    items = sick_leave_payloads(db, rows)
    circle_ids = {row.attraction_id_snapshot for row in rows if row.attraction_id_snapshot}
    circles = {circle.id: circle.name for circle in db.query(Attraction).filter(Attraction.id.in_(circle_ids)).all()} if circle_ids else {}
    for item, row in zip(items, rows):
        item["attraction_name"] = circles.get(row.attraction_id_snapshot, "")
        item["available_actions"] = []
    return {"items": items}


@router.post("/sick-leave-imports/commit")
def commit_sick_leave_import(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SICK_LEAVE_IMPORT"))):
    token = str(payload.get("token") or "")
    preview = _get_preview(token, user.id)
    if not preview:
        raise HTTPException(400, _PREVIEW_EXPIRED_MESSAGE)
    if preview["errors"]:
        raise HTTPException(400, "预检未通过，不能覆盖")
    if preview["unmatched"] and payload.get("reviewed_unmatched") is not True:
        raise HTTPException(400, "请登记人先复核未匹配员工及原因，再确认跳过")
    month = preview["month"]
    if payload.get("month") != month:
        raise HTTPException(400, "提交月份与预检月份不一致，请重新预检")
    employee_ids = sorted({row["employee_id"] for row in preview["matched"]})
    employees = {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
    if len(employees) != len(employee_ids) or any(not employee.is_active for employee in employees.values()):
        raise HTTPException(409, "员工资料已变更，请重新预检文件")
    try:
        replaced_record_count = 0
        replaced_manual_count = 0
        scope = scoped_hr_attraction_ids(db, user)
        for attraction_id in scope if scope is not None else (None,):
            ensure_month_open(db, month, attraction_id, "覆盖月度病假")
        loa_periods = db.query(EmployeeLOAPeriod).filter(
            EmployeeLOAPeriod.employee_id.in_(employee_ids),
            EmployeeLOAPeriod.status != "cancelled",
        ).all()
        loa_by_employee: dict[int, list[EmployeeLOAPeriod]] = {}
        for period in loa_periods:
            loa_by_employee.setdefault(period.employee_id, []).append(period)
        for item in preview["matched"]:
            if any(
                period.starts_on <= item["date"]
                and (period.ends_on is None or period.ends_on >= item["date"])
                for period in loa_by_employee.get(item["employee_id"], ())
            ):
                raise HTTPException(409, "预检后LOA记录已变化，请重新上传文件预检")
        old_rows = _active_month_rows(db, user, month)
        affected_ids = set(employees) | {row.employee_id for row in old_rows}
        affected_employees = {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(affected_ids)).all()} if affected_ids else {}
        for employee in affected_employees.values():
            ensure_scoped_hr_employee(db, user, employee)
            ensure_month_open(db, month, employee.attraction_id, "覆盖月度病假")
        replaced_record_count = len(old_rows)
        replaced_manual_count = sum(row.import_source != "monthly_transaction_import" for row in old_rows)
        absent_employee_count = len({row.employee_id for row in old_rows} - {row["employee_id"] for row in preview["matched"] + preview["loa_protected"]})
        covered_at = datetime.now()
        for old in old_rows:
            old.voided_from_status = old.status
            old.status = "covered"
            old.voided_by = user.id
            old.voided_by_name = user.name
            old.voided_at = covered_at
            old.void_reason = "以月度缺勤文件为准，原记录已覆盖（文件未列出者视为本月无缺勤）"
        for item in preview["matched"]:
            employee = employees[item["employee_id"]]
            role = role_at(db, employee.id)
            db.add(SickLeaveRecord(
                employee_id=employee.id, employee_no_snapshot=employee.employee_no, employee_name_snapshot=employee.name,
                employee_role_snapshot=role.name if role else "", attraction_id_snapshot=employee.attraction_id,
                attendance_month=month, leave_start_date=item["date"], leave_end_date=item["date"], leave_days=item["days"], charged_days=item["days"],
                proof_file_id=None, leave_type=item["leave_type"], import_source="monthly_transaction_import", status="active",
                submitted_by=user.id, submitted_by_name=user.name, is_violation=False,
            ))
        db.flush()
        for employee in affected_employees.values():
            recalculate_attendance(db, employee, month)
        write_audit(db, user.employee, "覆盖月度病假事务", "sick_leave_import", 0, after={"month": month, "file": preview["filename"], "employees": len(employees), "records": len(preview["matched"]), "replaced_records": replaced_record_count, "replaced_manual_records": replaced_manual_count, "absent_employees": absent_employee_count, "unmatched": len(preview["unmatched"]), "unmatched_reviewed_by": user.name if preview["unmatched"] else None, "unmatched_reasons": [{"row": row["row"], "employee_no": row["employee_no"], "reason": row["reason"]} for row in preview["unmatched"]], "loa_protected": len(preview["loa_protected"])})
        db.commit()
    except Exception:
        db.rollback()
        raise
    invalidate_data_caches()
    _drop_preview(token)
    return {"ok": True, "month": month, "covered_employee_count": len(employees), "covered_record_count": len(preview["matched"]), "replaced_record_count": replaced_record_count, "replaced_manual_count": replaced_manual_count, "absent_employee_count": absent_employee_count, "unmatched_count": len(preview["unmatched"]), "loa_protected_count": len(preview["loa_protected"])}


@router.get("/sick-leave-imports/{token}/unmatched-file")
def download_unmatched_sick_leave_file(token: str, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SICK_LEAVE_IMPORT"))):
    preview = _get_preview(token, user.id)
    if not preview:
        raise HTTPException(404, _PREVIEW_EXPIRED_MESSAGE)
    try:
        source = xlrd.open_workbook(file_contents=preview["content"])
    except (KeyError, xlrd.biffh.XLRDError) as exc:
        raise HTTPException(404, "原始预检文件已不可用，请重新上传") from exc
    workbook = Workbook()
    workbook.remove(workbook.active)
    red = PatternFill("solid", fgColor="FFC7CE")
    not_imported = [*preview["unmatched"], *preview["loa_protected"]]
    not_imported_by_row = {item["row"]: item["reason"] for item in not_imported}
    not_imported_source_ids = {item["source_id"] for item in not_imported}
    _, report_sheet, _, _, summary_header_row = _locate_workbook(preview["content"])
    for sheet_number, source_sheet in enumerate(source.sheets()):
        sheet = workbook.create_sheet(title=source_sheet.name[:31] or "Sheet")
        summary_marker = summary_id_column = None
        is_report_sheet = source_sheet.name == report_sheet.name
        if is_report_sheet:
            summary_marker = summary_header_row
            summary_id_column = _header_index(source_sheet.row_values(summary_header_row), "ID")
        for row_index in range(source_sheet.nrows):
            values = source_sheet.row_values(row_index)
            sheet.append(values)
            summary_matches_unimported = False
            if summary_marker is not None and summary_id_column is not None and row_index > summary_marker:
                try:
                    summary_matches_unimported = _source_id(values[summary_id_column]) in not_imported_source_ids
                except (IndexError, ValueError):
                    pass
            if is_report_sheet and (row_index + 1 in not_imported_by_row or summary_matches_unimported):
                for cell in sheet[sheet.max_row]:
                    cell.fill = red
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(max(len(str(cell.value or "")) for cell in column) + 2, 36)

    sheet = workbook.create_sheet("未覆盖说明")
    sheet.append(["文件行号", "源文件姓名", "源文件ID", "转换后系统员工号", "日期", "病假类型", "时数", "换算天数", "未覆盖原因"])
    for item in not_imported:
        sheet.append([item["row"], item["source_name"], item["source_id"], item["employee_no"], item["date"], item["leave_type"], float(item["hours"]), float(item["days"]), item["reason"]])
        for cell in sheet[sheet.max_row]:
            cell.fill = red
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(max(len(str(cell.value or "")) for cell in column) + 2, 36)
    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=\"sick-leave-unmatched.xlsx\""})
