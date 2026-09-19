"""Helpers, constants and lookups shared by more than one router module."""
from __future__ import annotations

import hashlib
import json
import os
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from fastapi import HTTPException, Request
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session
from app.v2_auth import V2User
from app.v2_models import Attraction, CircleTransferRequest, DeductionLevel, DeductionFollowUp, DeductionUpgradeRequest, DeductionRecord, DeductionType, Employee, GroupLeaderAssignment, MonthClosure, RecognitionRecord, Role, SickLeaveRecord, StoredFile, SubmissionRequest, SystemAlert, UserAccount, WorkGroup
from app.v2_services import FRONTLINE_CODES, GSM_CODES, LEADER_CODES, RECOGNIZER_CODES, active_group_leaders_bulk, groups_led_by, managed_attraction_ids, role_at


STATISTICS_CACHE_SECONDS = 2.0


_statistics_cache_lock = Lock()


_statistics_response_cache: dict[tuple, tuple[float, bytes]] = {}


PR_RANKING_CACHE_SECONDS = 5.0


_pr_ranking_cache_lock = Lock()


_pr_ranking_response_cache: dict[tuple, tuple[float, bytes]] = {}


PREVIEW_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})


PREVIEW_PDF_EXTENSIONS = frozenset({".pdf"})


def invalidate_data_caches() -> None:
    with _statistics_cache_lock:
        _statistics_response_cache.clear()
    with _pr_ranking_cache_lock:
        _pr_ranking_response_cache.clear()


def is_previewable_image(file_row: StoredFile | None) -> bool:
    """Only active image files use the protected in-page viewer."""
    return bool(file_row and file_row.status == "active" and file_row.extension.lower() in PREVIEW_IMAGE_EXTENSIONS)


def preview_kind(file_row: StoredFile | None) -> str:
    """Return the safe, browser-supported in-page preview type for a stored file."""
    if not file_row or file_row.status != "active":
        return ""
    suffix = file_row.extension.lower()
    if suffix in PREVIEW_IMAGE_EXTENSIONS:
        return "image"
    if suffix in PREVIEW_PDF_EXTENSIONS:
        return "pdf"
    return ""


ATTENDANCE_DEDUCTION_CODES = {
    "ATT_EARLY_CLOCK",
    "ATT_LATE_WITHIN_30",
    "ATT_LATE_OVER_30",
    "ATT_LATE_CLOCK",
    "ATT_EARLY_LEAVE_WITHIN_30",
    "ATT_EARLY_LEAVE_OVER_30",
    "ATT_MISSING_CLOCK",
    "ATT_REMOTE_CLOCK",
}


REPEAT_CONTROLLED_DEDUCTION_CODES = ATTENDANCE_DEDUCTION_CODES | {"SICK_LEAVE_VIOLATION"}


DIRECT_HIDDEN_DEDUCTION_CODES = {"ATT_LATE_OVER_30", "ATT_EARLY_LEAVE_OVER_30"}


DEDUCTION_TYPE_ORDER = (
    "SAFETY",
    "AUDIT",
    "COURTESY",
    "INCLUSION",
    "SHOW",
    "EFFICIENCY",
    "MSP",
    "COMPLAINT",
    "SICK_LEAVE_VIOLATION",
    "OTHER",
    "ATT_EARLY_CLOCK",
    "ATT_LATE_CLOCK",
    "ATT_LATE_OVER_30",
    "ATT_LATE_WITHIN_30",
    "ATT_EARLY_LEAVE_OVER_30",
    "ATT_EARLY_LEAVE_WITHIN_30",
    "ATT_MISSING_CLOCK",
    "ATT_REMOTE_CLOCK",
)


SPECIAL_RECOGNITION_TYPES = {
    "MSP": {"option_id": "special:MSP", "name": "MSP", "score": Decimal("3.00"), "monthly_limit": None},
    "COMMENDATION_LETTER": {
        "option_id": "special:COMMENDATION_LETTER",
        "name": "表扬信",
        "score": Decimal("3.00"),
        "monthly_limit": 1,
    },
}


DEDICATED_RECOGNITION_TYPE_CODES = {"POC"}


# Five ordinary recognition categories are capped from the agreed effective
# date.  Existing August history keeps its already-confirmed score unchanged.
MONTHLY_CATEGORY_CAP_CODES = {"SAFETY", "COURTESY", "INCLUSION", "EFFICIENCY", "SHOW"}


MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE = date(2026, 9, 1)


MONTHLY_CATEGORY_CAP_LIMIT = Decimal("5.00")


DEDUCTION_LEVEL_ORDER = {"STATEMENT": 1, "MEMO": 2, "WARNING_1": 3, "WARNING_2": 4}


NEXT_DEDUCTION_LEVEL = {"STATEMENT": "MEMO", "MEMO": "WARNING_1", "WARNING_1": "WARNING_2", "WARNING_2": "WARNING_2"}


CIRCLE_HR_MANAGED_ROLE_CODES = {"CM", "TR", "TA_SUPERVISOR", "SUPERVISOR"}


# Account-status visibility is intentionally broader than employee-edit
# authority: scoped HR may read the four frontline/leader roles across all
# circles, while the highest administrator may read every regular account.
REGULAR_ACCOUNT_ROLE_CODES = CIRCLE_HR_MANAGED_ROLE_CODES | {"TA_GSM", "GSM", "AM", "OM"}


SCOPED_HR_ROLE_CODE = "HR_CIRCLE"


# Circle HR may only retune recognizer roles it manages; GSM/TA_GSM/AM/OM rules are global.
CIRCLE_HR_SCORE_RULE_ROLE_CODES = CIRCLE_HR_MANAGED_ROLE_CODES & RECOGNIZER_CODES


# Record status families shared by the list endpoints' filters.
RECOGNITION_FILTER_STATUSES = frozenset({"pending", "rejected", "confirmed"})


ATTENDANCE_FILTER_STATUSES = frozenset({"active", "void"})


DEDUCTION_FILTER_STATUSES = frozenset({"active", "void", "pending_upgrade", "pending_material", "material_processing", "material_failed"})


MONTH_CLOSE_ROLE_CODES = {"HR_CIRCLE", "SYSTEM_ADMIN"}


MATERIAL_COLLABORATOR_CODES = LEADER_CODES | GSM_CODES


MONTH_CLOSE_EFFECTIVE_DATE = "2026-09-01"


GROUP_DISPLAY_EFFECTIVE_DATE = "2026-09-01"


BACKUP_HEALTH_STATUS_PATH = Path(
    os.environ.get(
        "RECOGNITION_BACKUP_HEALTH_STATUS_FILE",
        r"C:\Server\zhaojunjie\backups\recognition-card-system-sqlite\backup-health-status.json",
    )
)


def scoped_hr_attraction_ids(db: Session, user: V2User) -> set[int] | None:
    """Return the circle restriction for a scoped HR account; global roles remain unrestricted."""
    if user.role.code != SCOPED_HR_ROLE_CODE:
        return None
    if not user.employee.attraction_id:
        raise HTTPException(403, "景点圈HR账号未配置景点圈")
    return {user.employee.attraction_id}


def ensure_scoped_hr_attraction(db: Session, user: V2User, attraction_id: int | None) -> None:
    allowed = scoped_hr_attraction_ids(db, user)
    if allowed is not None and attraction_id not in allowed:
        raise HTTPException(403, "景点圈HR只能操作所属景点圈")


def ensure_scoped_hr_employee(db: Session, user: V2User, employee: Employee) -> None:
    ensure_scoped_hr_attraction(db, user, employee.attraction_id)


def ensure_operational_target_scope(user: V2User, employee: Employee) -> None:
    """Backend authority for deduction/absence target selection.

    Search UI is only a convenience: TA主管 is confined to its own circle;
    主管、TA GSM、GSM may support active frontline staff across circles.
    """
    if user.role.code == "TA_SUPERVISOR" and employee.attraction_id != user.employee.attraction_id:
        raise HTTPException(403, "TA主管仅可登记本景点圈CM/TR")
    if user.role.code not in {"TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "HR_CIRCLE", "SYSTEM_ADMIN"}:
        raise HTTPException(403, "当前角色无权登记该员工")


def visible_system_alerts(db: Session, user: V2User, *, limit: int = 200) -> list[SystemAlert]:
    """Return only alerts inside the caller's existing HR scope, then page."""
    query = db.query(SystemAlert)
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None:
        group_ids = [
            row[0]
            for row in db.query(WorkGroup.id).filter(WorkGroup.attraction_id.in_(allowed_attractions)).all()
        ]
        employee_ids = [
            row[0]
            for row in db.query(Employee.id).filter(Employee.attraction_id.in_(allowed_attractions)).all()
        ]
        scope_clauses = []
        if group_ids:
            scope_clauses.append(SystemAlert.group_id.in_(group_ids))
        if employee_ids:
            scope_clauses.append(and_(SystemAlert.group_id.is_(None), SystemAlert.employee_id.in_(employee_ids)))
        query = query.filter(or_(*scope_clauses) if scope_clauses else SystemAlert.id == -1)
    return query.order_by(SystemAlert.status.asc(), SystemAlert.created_at.desc(), SystemAlert.id.desc()).limit(limit).all()


def backup_health_payload() -> dict:
    """Read the fixed server health report without exposing paths or webhook data."""
    default = {
        "available": False,
        "ok": False,
        "status": "未发现健康报告",
        "checked_at_utc": "",
        "task": {},
        "latest_backup": None,
        "issues": [{"code": "health_report_missing", "message": "未发现备份健康报告，请检查每日备份巡检任务。"}],
        "alert": {"configured": False, "attempted": False, "delivered": False, "last_alert_at_utc": ""},
    }
    try:
        raw = json.loads(BACKUP_HEALTH_STATUS_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError, TypeError):
        default["status"] = "健康报告无法读取"
        default["issues"] = [{"code": "health_report_invalid", "message": "备份健康报告无法读取，请检查巡检任务。"}]
        return default
    if not isinstance(raw, dict):
        default["status"] = "健康报告格式无效"
        default["issues"] = [{"code": "health_report_invalid", "message": "备份健康报告格式无效，请检查巡检任务。"}]
        return default
    task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
    latest = raw.get("latest_backup") if isinstance(raw.get("latest_backup"), dict) else None
    alert = raw.get("alert") if isinstance(raw.get("alert"), dict) else {}
    issues = raw.get("issues") if isinstance(raw.get("issues"), list) else []
    return {
        "available": True,
        "ok": bool(raw.get("ok")),
        "status": "正常" if bool(raw.get("ok")) else "需处理",
        "checked_at_utc": str(raw.get("checked_at_utc") or ""),
        "task": {
            "exists": bool(task.get("exists")),
            "enabled": bool(task.get("enabled")),
            "state": str(task.get("state") or "未知"),
            "last_run_time_utc": str(task.get("last_run_time_utc") or ""),
            "next_run_time_utc": str(task.get("next_run_time_utc") or ""),
            "last_task_result": task.get("last_task_result"),
        },
        "latest_backup": None if latest is None else {
            "file_name": Path(str(latest.get("file_name") or "")).name,
            "created_at_utc": str(latest.get("created_at_utc") or ""),
            "age_hours": latest.get("age_hours"),
            "size_bytes": latest.get("size_bytes"),
            "manifest_verified": bool(latest.get("manifest_verified")),
            "sha256_verified": bool(latest.get("sha256_verified")),
            "size_verified": bool(latest.get("size_verified")),
            "quick_check": str(latest.get("quick_check") or ""),
        },
        "issues": [
            {"code": str(item.get("code") or "unknown"), "message": str(item.get("message") or "")}
            for item in issues if isinstance(item, dict)
        ][:20],
        "alert": {
            "configured": bool(alert.get("configured")),
            "attempted": bool(alert.get("attempted")),
            "delivered": bool(alert.get("delivered")),
            "last_alert_at_utc": str(alert.get("last_alert_at_utc") or ""),
        },
    }


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def parse_iso_date(value: str, label: str = "日期") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{label}格式必须为YYYY-MM-DD") from exc


def parse_score_month(value: str) -> str:
    month = str(value or "").strip()
    if len(month) != 7:
        raise HTTPException(400, "月份格式必须为YYYY-MM")
    try:
        parsed = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise HTTPException(400, "月份格式必须为YYYY-MM") from exc
    normalized = parsed.strftime("%Y-%m")
    if month != normalized:
        raise HTTPException(400, "月份格式必须为YYYY-MM")
    return normalized


def month_closure_scope(db: Session, month: str, attraction_id: int | None) -> MonthClosure | None:
    query = db.query(MonthClosure).filter(
        MonthClosure.closure_month == month,
        MonthClosure.status == "closed",
    )
    if attraction_id is None:
        return query.filter(MonthClosure.attraction_id.is_(None)).first()
    return query.filter(or_(MonthClosure.attraction_id.is_(None), MonthClosure.attraction_id == attraction_id)).order_by(
        MonthClosure.attraction_id.is_(None).desc()
    ).first()


def ensure_month_open(db: Session, month: str, attraction_id: int | None, operation: str) -> None:
    month = parse_score_month(month)
    closure = month_closure_scope(db, month, attraction_id)
    if not closure:
        return
    attraction = db.get(Attraction, closure.attraction_id) if closure.attraction_id else None
    scope_name = attraction.name if attraction else "全部景点圈"
    raise HTTPException(
        423,
        detail={
            "code": "MONTH_CLOSED",
            "message": f"{month} · {scope_name}已月结，不能{operation}。如需更正，请由该景点圈HR填写原因后临时开放。",
            "month": month,
            "attraction_id": closure.attraction_id,
            "attraction_name": scope_name,
            "closed_at": closure.closed_at.strftime("%Y-%m-%d %H:%M:%S") if closure.closed_at else "",
            "closed_by_name": closure.closed_by_name or "",
        },
    )


def month_close_checklist(db: Session, month: str, attraction_id: int | None) -> list[dict]:
    """Only unresolved work that can alter this month blocks the close."""
    month_start = date.fromisoformat(f"{month}-01")
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    employee_query = db.query(Employee.id)
    if attraction_id is not None:
        employee_query = employee_query.filter(Employee.attraction_id == attraction_id)
    employee_ids = [row[0] for row in employee_query.all()]
    pending_recognitions = db.query(RecognitionRecord).filter(
        RecognitionRecord.recognition_month == month,
        RecognitionRecord.status == "pending",
        *(() if attraction_id is None else (RecognitionRecord.home_attraction_id == attraction_id,)),
    ).count()
    unresolved_materials = db.query(DeductionRecord).filter(
        DeductionRecord.deduction_month == month,
        DeductionRecord.status.in_({"pending_material", "material_processing", "material_failed"}),
        *(() if attraction_id is None else (DeductionRecord.attraction_id_snapshot == attraction_id,)),
    ).count()
    upgrade_query = db.query(DeductionUpgradeRequest).join(
        DeductionRecord, DeductionRecord.id == DeductionUpgradeRequest.second_deduction_id
    ).filter(
        DeductionRecord.deduction_month == month,
        DeductionUpgradeRequest.status == "pending",
    )
    if attraction_id is not None:
        upgrade_query = upgrade_query.filter(DeductionRecord.attraction_id_snapshot == attraction_id)
    upgrade_requests = upgrade_query.count()
    follow_up_query = db.query(DeductionFollowUp).filter(
        DeductionFollowUp.occurred_on.like(f"{month}%"),
        DeductionFollowUp.status == "pending",
    )
    if attraction_id is not None:
        follow_up_query = follow_up_query.filter(
            DeductionFollowUp.employee_id.in_(employee_ids) if employee_ids else DeductionFollowUp.id == -1
        )
    follow_ups = follow_up_query.count()
    transfer_query = db.query(CircleTransferRequest).filter(
        CircleTransferRequest.status == "pending",
        CircleTransferRequest.requested_at >= datetime.combine(month_start, datetime.min.time()),
        CircleTransferRequest.requested_at < datetime.combine(month_end, datetime.min.time()),
    )
    if attraction_id is not None:
        transfer_query = transfer_query.filter(
            or_(CircleTransferRequest.source_attraction_id == attraction_id, CircleTransferRequest.target_attraction_id == attraction_id)
        )
    pending_transfers = transfer_query.count()
    return [
        {"code": "recognition_review", "name": "待复核签卡", "count": pending_recognitions, "target": None, "responsible": "请联系对应主管完成复核"},
        {"code": "deduction_material", "name": "待补/生成失败材料", "count": unresolved_materials, "target": None, "responsible": "请联系登记人或主管补充材料"},
        {"code": "deduction_upgrade", "name": "待升级工单", "count": upgrade_requests, "target": None, "responsible": "请联系被分配的TA GSM或GSM处理"},
        {"code": "deduction_follow_up", "name": "重复违规待跟进", "count": follow_ups, "target": None, "responsible": "请联系对应主管完成跟进"},
        {"code": "circle_transfer", "name": "待确认跨圈调动", "count": pending_transfers, "target": "circleTransfers", "responsible": "由目标景点圈HR确认"},
    ]


def month_closure_payload(db: Session, month: str, attraction_id: int | None, user: V2User) -> dict:
    global_row = db.query(MonthClosure).filter_by(
        closure_month=month, attraction_id=None, status="closed"
    ).first()
    scope_row = None
    if attraction_id is not None:
        scope_row = db.query(MonthClosure).filter_by(
            closure_month=month, attraction_id=attraction_id, status="closed"
        ).first()
    effective = global_row or scope_row
    attraction = db.get(Attraction, attraction_id) if attraction_id else None
    effective_attraction = db.get(Attraction, effective.attraction_id) if effective and effective.attraction_id else None
    can_close = False
    if not effective and user.role.code in MONTH_CLOSE_ROLE_CODES:
        if user.role.code == "SYSTEM_ADMIN":
            can_close = True
        elif attraction_id is not None and attraction_id in managed_attraction_ids(db, user.id):
            can_close = True
    return {
        "month": month,
        "attraction_id": attraction_id,
        "attraction_name": attraction.name if attraction else "全部景点圈",
        "status": "closed" if effective else "open",
        "is_closed": bool(effective),
        "effective_scope": effective_attraction.name if effective_attraction else ("全部景点圈" if effective else ""),
        "closed_by_name": effective.closed_by_name if effective else "",
        "closed_at": effective.closed_at.strftime("%Y-%m-%d %H:%M:%S") if effective and effective.closed_at else "",
        "close_reason": effective.close_reason if effective else "",
        "can_close": can_close,
        "can_reopen": bool(effective and (user.role.code == "SYSTEM_ADMIN" or (user.role.code == "HR_CIRCLE" and attraction_id is not None and attraction_id in scoped_hr_attraction_ids(db, user)))),
        "reopen_target_attraction_id": effective.attraction_id if effective else attraction_id,
        "checklist": month_close_checklist(db, month, attraction_id),
    }


def normalize_request_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 100:
        raise HTTPException(400, "重复提交标识过长")
    return key


def submission_payload_digest(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def existing_submission(
    db: Session,
    actor_id: int,
    operation: str,
    request_key: str | None,
    model,
    payload_digest: str | None = None,
):
    if not request_key:
        return None
    request_row = db.query(SubmissionRequest).filter_by(
        actor_id=actor_id,
        operation=operation,
        request_key=request_key,
    ).first()
    if not request_row:
        return None
    stored = (request_row.payload_digest or "").strip()
    if payload_digest and stored and stored != payload_digest:
        raise HTTPException(
            409,
            {
                "code": "IDEMPOTENCY_PAYLOAD_CONFLICT",
                "message": "同一提交键不能用于不同内容，请刷新页面后重新提交。",
            },
        )
    return db.get(model, request_row.entity_id)


def remember_submission(
    db: Session,
    actor_id: int,
    operation: str,
    request_key: str | None,
    entity_id: int,
    payload_digest: str | None = None,
) -> None:
    if request_key:
        db.add(
            SubmissionRequest(
                actor_id=actor_id,
                operation=operation,
                request_key=request_key,
                entity_id=entity_id,
                payload_digest=payload_digest,
            )
        )
        db.flush()


def subtract_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def add_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def group_display_metadata_bulk(db: Session, group_ids: list[int] | set[int], on_date: str | None = None) -> dict[int, dict]:
    """Resolve presentation-only group labels without ever renaming historic groups.

    Since 2026-09-01, a workgroup is displayed under its current leader.  The
    prior leader remains a one-month visual aid only; IDs, memberships,
    snapshots and historic exports all continue to use their original records.
    """
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids if group_id))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    try:
        as_of = date.fromisoformat(value)
    except ValueError:
        as_of = date.today()
        value = as_of.isoformat()
    groups = {row.id: row for row in db.query(WorkGroup).filter(WorkGroup.id.in_(ids)).all()}
    active = active_group_leaders_bulk(db, ids, value)
    # The old leader only matters during the 1-calendar-month transition
    # window.  It is intentionally calculated on read and never stored on the
    # WorkGroup itself.
    transition_start = add_calendar_months(as_of, -1).isoformat()
    ended_rows = (
        db.query(GroupLeaderAssignment)
        .filter(
            GroupLeaderAssignment.group_id.in_(ids),
            GroupLeaderAssignment.status == "ended",
            GroupLeaderAssignment.ends_on.is_not(None),
            GroupLeaderAssignment.ends_on >= transition_start,
            GroupLeaderAssignment.ends_on <= value,
        )
        .order_by(GroupLeaderAssignment.group_id, GroupLeaderAssignment.ends_on.desc(), GroupLeaderAssignment.id.desc())
        .all()
    )
    latest_ended: dict[int, GroupLeaderAssignment] = {}
    for row in ended_rows:
        latest_ended.setdefault(row.group_id, row)
    leader_ids = {
        assignment.leader_employee_id
        for assignment in [*active.values(), *latest_ended.values()]
        if assignment
    }
    employees = {
        row.id: row for row in db.query(Employee).filter(Employee.id.in_(leader_ids)).all()
    } if leader_ids else {}
    enabled = as_of >= date.fromisoformat(GROUP_DISPLAY_EFFECTIVE_DATE)
    result: dict[int, dict] = {}
    for group_id in ids:
        group = groups.get(group_id)
        current_assignment = active.get(group_id)
        current = employees.get(current_assignment.leader_employee_id) if current_assignment else None
        previous_assignment = latest_ended.get(group_id) if enabled else None
        previous = employees.get(previous_assignment.leader_employee_id) if previous_assignment else None
        previous_until = ""
        if previous_assignment and previous_assignment.ends_on:
            previous_until = add_calendar_months(date.fromisoformat(previous_assignment.ends_on), 1).isoformat()
            if as_of > date.fromisoformat(previous_until) or (current and previous and current.id == previous.id):
                previous = None
                previous_until = ""
        result[group_id] = {
            "name": f"{current.name}工作组" if enabled and current else (group.name if group else ""),
            "leader_name": current.name if current else "",
            "previous_leader_name": previous.name if previous else "",
            "previous_leader_until": previous_until if previous else "",
        }
    return result


EMPLOYEE_TARGET_PERMISSIONS = {
    "recognition": {"EMPLOYEE_ADD"},
    "deduction": {"DEDUCTION_DIRECT", "DEDUCTION_ALL"},
    "attendance": {"SICK_REGISTER"},
    "circle_transfer": {"HR_MANAGE"},
    "poc": {"POC_ISSUE"},
}


def like_escaped_pattern(keyword: str, escape_char: str = "\\") -> str:
    """Wrap a user keyword in a LIKE pattern with wildcards escaped."""
    value = str(keyword or "").strip()
    escaped = value.replace(escape_char, escape_char * 2).replace("%", escape_char + "%").replace("_", escape_char + "_")
    return f"%{escaped}%"


def search_employee_targets(
    db: Session,
    *,
    keyword: str = "",
    attraction_id: int | None = None,
    limit: int = 30,
) -> dict:
    """Return active, enabled CM/TR targets in one bounded SQLite query."""
    today_value = date.today().isoformat()
    normalized = str(keyword or "").strip()
    escaped = normalized.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    params = {
        "today": today_value,
        "keyword": normalized,
        "pattern": f"%{escaped}%",
        "prefix": f"{escaped}%",
        "attraction_id": attraction_id,
        "limit": max(1, min(int(limit or 30), 500)),
    }
    rows = db.execute(
        text(
            """
            WITH ranked_roles AS (
                SELECT era.employee_id, era.role_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY era.employee_id
                           ORDER BY era.starts_on DESC, era.id DESC
                       ) AS row_number
                FROM employee_role_assignments era
                WHERE era.status != 'cancelled'
                  AND era.starts_on <= :today
                  AND (era.ends_on IS NULL OR era.ends_on >= :today)
            ), ranked_memberships AS (
                SELECT gm.employee_id, gm.group_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY gm.employee_id
                           ORDER BY gm.starts_on DESC, gm.id DESC
                       ) AS row_number
                FROM group_memberships gm
                WHERE gm.status = 'active'
                  AND gm.starts_on <= :today
                  AND (gm.ends_on IS NULL OR gm.ends_on >= :today)
            )
            SELECT e.id, e.employee_no, e.name,
                   r.code AS role_code, r.name AS role_name,
                   e.attraction_id, COALESCE(a.name, '') AS attraction_name,
                   wg.id AS group_id, COALESCE(wg.name, '') AS group_name,
                   COUNT(*) OVER() AS total_count
            FROM employees e
            JOIN ranked_roles rr ON rr.employee_id = e.id AND rr.row_number = 1
            JOIN roles r ON r.id = rr.role_id AND r.active = 1 AND r.code IN ('CM', 'TR')
            JOIN user_accounts ua ON ua.employee_id = e.id AND ua.enabled = 1
            LEFT JOIN attractions a ON a.id = e.attraction_id
            LEFT JOIN ranked_memberships rm ON rm.employee_id = e.id AND rm.row_number = 1
            LEFT JOIN work_groups wg ON wg.id = rm.group_id
            WHERE e.is_active = 1
              AND (:attraction_id IS NULL OR e.attraction_id = :attraction_id)
              AND (
                  :keyword = ''
                  OR e.name LIKE :pattern ESCAPE '!'
                  OR e.employee_no LIKE :pattern ESCAPE '!'
              )
            ORDER BY
                CASE
                    WHEN e.employee_no = :keyword THEN 0
                    WHEN e.name = :keyword THEN 1
                    WHEN e.employee_no LIKE :prefix ESCAPE '!' THEN 2
                    WHEN e.name LIKE :prefix ESCAPE '!' THEN 3
                    ELSE 4
                END,
                e.name ASC, e.employee_no ASC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    total = int(rows[0]["total_count"]) if rows else 0
    group_display = group_display_metadata_bulk(db, {row["group_id"] for row in rows if row["group_id"]}, today_value)
    return {
        "items": [
            {
                "id": row["id"],
                "employee_no": row["employee_no"],
                "name": row["name"],
                "role_code": row["role_code"],
                "role_name": row["role_name"],
                "attraction_id": row["attraction_id"],
                "attraction_name": row["attraction_name"],
                "group_id": row["group_id"],
                "group_name": group_display.get(row["group_id"], {}).get("name", row["group_name"]),
            }
            for row in rows
        ],
        "total": total,
        "limit": params["limit"],
    }


def ensure_enabled_frontline_target(db: Session, employee_id: int, action_name: str) -> tuple[Employee, Role]:
    target = db.get(Employee, employee_id)
    target_role = role_at(db, employee_id) if target else None
    account_enabled = bool(
        target
        and db.query(UserAccount.id)
        .filter(UserAccount.employee_id == target.id, UserAccount.enabled.is_(True))
        .first()
    )
    if not target or not target.is_active or not account_enabled or not target_role or target_role.code not in FRONTLINE_CODES:
        raise HTTPException(400, f"只能为在职、账号启用的CM/TR登记{action_name}")
    return target, target_role


def effective_recognition_credit(row: RecognitionRecord) -> Decimal:
    """Legacy records before the 2026-09-01 cap retain their raw score."""
    if row.recognition_date and date.fromisoformat(row.recognition_date) < MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE:
        return Decimal(row.fraction or 0)
    return Decimal(row.credited_fraction or 0)


def void_operator_snapshot(db: Session, user: V2User) -> tuple[str, str, str]:
    if user.role.code in FRONTLINE_CODES:
        scope = f"本人账号（{user.employee.employee_no}）"
    elif user.role.code in LEADER_CODES:
        group_names = [group.name for group in groups_led_by(db, user.id)]
        scope = f"直属小组：{'、'.join(group_names)}" if group_names else "直属小组：未配置"
    elif user.role.code in GSM_CODES:
        attraction_ids = sorted(managed_attraction_ids(db, user.id))
        attraction_names = [
            row.name
            for row in db.query(Attraction).filter(Attraction.id.in_(attraction_ids)).order_by(Attraction.name).all()
        ] if attraction_ids else []
        scope = f"景点圈：{'、'.join(attraction_names)}" if attraction_names else "景点圈：未配置"
    elif user.role.code == "HR_ADMIN":
        scope = "HR人员与组织管理"
    else:
        scope = "全局系统权限"
    return user.role.code, user.role.name, scope


def recognition_payload(row: RecognitionRecord) -> dict:
    status_names = {"pending": "待复核", "confirmed": "已确认", "rejected": "不通过", "void": "已撤回"}
    attachment = row.attachments[0] if row.attachments else None
    attachment_file = attachment.file if attachment else None
    return {
        "record_type": "recognition",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no,
        "employee_name": row.employee_name,
        "recognition_date": row.recognition_date,
        "recognition_type": row.recognition_type_name,
        "content": row.content,
        "recognizer_name": row.recognizer_name,
        "recognizer_role": row.recognizer_role_snapshot,
        "operator_id": row.operator_employee_id,
        "operator_name": row.operator_name,
        "source": row.source,
        "entry_label": f"{row.operator_name}录入" if row.source == "manager" else "",
        "fraction": float(row.fraction or 0),
        "credited_fraction": float(effective_recognition_credit(row)),
        "monthly_cap_status": row.monthly_cap_status or "not_applicable",
        "monthly_cap_reason": row.monthly_cap_reason or "",
        "monthly_cap_limit": float(row.monthly_cap_limit or 0),
        "monthly_cap_confirmed_before": float(row.monthly_cap_confirmed_before or 0),
        "poc_period_type": row.poc_period_type or "",
        "poc_period_key": row.poc_period_key or "",
        "poc_reason": row.poc_reason or "",
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        "review_note": row.review_note or "",
        "same_day_duplicate": bool(row.same_day_duplicate_group),
        "same_day_duplicate_sequence": int(row.same_day_duplicate_sequence or 0),
        "same_day_duplicate_label": (f"今日已有同类登记（第{int(row.same_day_duplicate_sequence or 0)}次）" if row.same_day_duplicate_group else ""),
        "voided_by_name": row.voided_by_name or "",
        "voided_by_role_code": row.voided_by_role_code or "",
        "voided_by_role_name": row.voided_by_role_name or "",
        "void_permission_scope": row.void_permission_scope_snapshot or "",
        "voided_from_status": row.voided_from_status or "",
        "voided_at": row.voided_at.strftime("%Y-%m-%d %H:%M:%S") if row.voided_at else "",
        "void_reason": row.void_reason or "",
        "has_image": bool(attachment),
        "image_url": f"/api/files/{attachment.file_id}" if attachment else "",
        "image_is_previewable": is_previewable_image(attachment_file),
        "image_preview_kind": preview_kind(attachment_file),
        "available_actions": ["withdraw"] if row.status != "void" else [],
    }


def deduction_payload(row: DeductionRecord) -> dict:
    status_names = {"active": "已扣分", "void": "已作废", "pending_upgrade": "待升级审核", "pending_material": "待补充材料（未扣分）", "material_processing": "材料生成中", "material_failed": "材料生成失败"}
    upgrade_state_names = {"pending": "待升级审核", "source_first": "已参与升级", "source_second": "已用于升级，不计分", "result": "升级结果", "rejected": "审核不通过"}
    return {
        "record_type": "deduction",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no,
        "employee_name": row.employee_name,
        "deduction_type": row.deduction_type_name,
        "deduction_level": row.deduction_level_name,
        "points": float(row.points),
        "actual_points": float(row.points),
        "occurred_on": row.occurred_on,
        "description": row.description,
        "submitter_id": row.submitter_id,
        "submitter_name": row.submitter_name,
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        "void_reason": row.void_reason or "",
        "voided_by_name": row.voided_by_name or "",
        "voided_by_role_code": row.voided_by_role_code or "",
        "voided_by_role_name": row.voided_by_role_name or "",
        "void_permission_scope": row.void_permission_scope_snapshot or "",
        "voided_from_status": row.voided_from_status or "",
        "voided_at": row.voided_at.strftime("%Y-%m-%d %H:%M:%S") if row.voided_at else "",
        "material_status": row.material_status or "ready",
        "material_error": row.material_error or "",
        "material_source_type": row.material_source_type or "pdf",
        "material_job_id": row.material_job_id or 0,
        "material_uploaded_by_name": row.material_uploaded_by_name or "",
        "material_uploaded_at": row.material_uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if row.material_uploaded_at else "",
        "material_revision": int(row.material_revision or 1),
        "legacy_upgrade_excluded": bool(row.legacy_upgrade_excluded),
        "legacy_upgrade_note": row.legacy_upgrade_note or "",
        "document_url": "" if row.upgrade_role == "result" or (row.material_status or "ready") != "ready" else f"/api/files/{row.document_file_id}",
        # Declarations are accepted only as PDF documents when the record is created.
        "document_preview_kind": "" if row.upgrade_role == "result" or (row.material_status or "ready") != "ready" else "pdf",
        "upgrade_request_id": row.upgrade_request_id or 0,
        "upgrade_role": row.upgrade_role or "",
        "upgrade_state": row.upgrade_state or "",
        "upgrade_state_name": upgrade_state_names.get(row.upgrade_state or "", ""),
        "available_actions": (["supplement_material"] if row.status in {"pending_material", "material_failed"} else []) + (["void"] if row.status == "active" and not row.upgrade_request_id else []),
    }


def pending_material_conflict(
    db: Session,
    *,
    employee_id: int,
    deduction_type_id: int,
    deduction_level_id: int,
    occurred_on: str,
) -> DeductionRecord | None:
    """Return the one unfinished declaration that must be completed before another is entered."""
    return (
        db.query(DeductionRecord)
        .filter(
            DeductionRecord.employee_id == employee_id,
            DeductionRecord.deduction_type_id == deduction_type_id,
            DeductionRecord.deduction_level_id == deduction_level_id,
            DeductionRecord.occurred_on == occurred_on,
            DeductionRecord.status.in_({"pending_material", "material_failed"}),
        )
        .order_by(DeductionRecord.submitted_at.asc(), DeductionRecord.id.asc())
        .first()
    )


def raise_pending_material_conflict(row: DeductionRecord) -> None:
    raise HTTPException(
        409,
        detail={
            "code": "PENDING_MATERIAL_EXISTS",
            "message": "该员工同日、同类型、同等级的声明已有待补材料记录，请先补充该记录材料后再登记第二条。",
            "record": deduction_payload(row),
        },
    )


def deduction_follow_up_payload(row: DeductionFollowUp) -> dict:
    status_names = {"pending": "待经理跟进", "issued": "已开具"}
    return {
        "record_type": "follow_up",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no,
        "employee_name": row.employee_name,
        "deduction_type": row.deduction_type_name,
        "occurred_on": row.occurred_on,
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "issued_by_name": row.issued_by_name or "",
        "issued_at": row.issued_at.strftime("%Y-%m-%d %H:%M:%S") if row.issued_at else "",
        "issued_deduction_id": row.issued_deduction_id,
        "available_actions": [],
    }


def direct_only_deduction_user(user: V2User) -> bool:
    return "DEDUCTION_DIRECT" in user.permissions and "DEDUCTION_ALL" not in user.permissions


def ensure_deduction_type_allowed(user: V2User, deduction_type: DeductionType) -> None:
    if direct_only_deduction_user(user) and deduction_type.code in DIRECT_HIDDEN_DEDUCTION_CODES:
        raise HTTPException(403, "TA主管、主管不能登记迟到30分钟以上或早退30分钟以上")


UPGRADE_REVIEWER_CODES = {"GSM", "TA_GSM"}


def statement_upgrade_candidate(db: Session, employee_id: int, deduction_type_id: int, occurred_on: str) -> DeductionRecord | None:
    """Return the oldest still-eligible same-type statement in the 3-month window."""
    statement = db.query(DeductionLevel).filter_by(code="STATEMENT", active=True).first()
    if not statement:
        return None
    window_start = subtract_calendar_months(parse_iso_date(occurred_on, "事件日期"), 3).isoformat()
    return (
        db.query(DeductionRecord)
        .filter(
            DeductionRecord.employee_id == employee_id,
            DeductionRecord.deduction_type_id == deduction_type_id,
            DeductionRecord.deduction_level_id == statement.id,
            DeductionRecord.status == "active",
            DeductionRecord.legacy_upgrade_excluded.is_(False),
            or_(DeductionRecord.upgrade_state.is_(None), DeductionRecord.upgrade_state == "eligible"),
            DeductionRecord.occurred_on >= window_start,
            DeductionRecord.occurred_on <= occurred_on,
        )
        .order_by(DeductionRecord.occurred_on.asc(), DeductionRecord.id.asc())
        .first()
    )


def statement_upgrade_reviewer_options(db: Session) -> list[dict]:
    rows = (
        db.query(Employee, UserAccount)
        .join(UserAccount, UserAccount.employee_id == Employee.id)
        .filter(Employee.is_active.is_(True), UserAccount.enabled.is_(True))
        .order_by(Employee.name, Employee.employee_no)
        .all()
    )
    result = []
    for employee, _account in rows:
        role = role_at(db, employee.id)
        if role and role.code in UPGRADE_REVIEWER_CODES:
            result.append({"id": employee.id, "name": employee.name, "employee_no": employee.employee_no, "role_code": role.code, "role_name": role.name})
    return result


def sick_leave_payload(db: Session, row: SickLeaveRecord, *, employees: dict[int, Employee] | None = None, files: dict[int, StoredFile] | None = None) -> dict:
    employee = (employees or {}).get(row.employee_id) or db.get(Employee, row.employee_id)
    proof_file = (files or {}).get(row.proof_file_id) or db.get(StoredFile, row.proof_file_id)
    status_names = {"active": "已生效", "void": "已作废"}
    return {
        "record_type": "sick_leave",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no_snapshot or (employee.employee_no if employee else ""),
        "employee_name": row.employee_name_snapshot or (employee.name if employee else "未知员工"),
        "attendance_month": row.attendance_month,
        "leave_start_date": row.leave_start_date,
        "leave_end_date": row.leave_end_date,
        "leave_days": float(row.leave_days),
        "charged_days": float(row.charged_days),
        "note": row.note or "",
        "submitter_id": row.submitted_by,
        "submitter_name": row.submitted_by_name,
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        "void_reason": row.void_reason or "",
        "voided_by_name": row.voided_by_name or "",
        "voided_by_role_code": row.voided_by_role_code or "",
        "voided_by_role_name": row.voided_by_role_name or "",
        "void_permission_scope": row.void_permission_scope_snapshot or "",
        "voided_from_status": row.voided_from_status or "",
        "voided_at": row.voided_at.strftime("%Y-%m-%d %H:%M:%S") if row.voided_at else "",
        "proof_url": f"/api/files/{row.proof_file_id}",
        "proof_is_previewable": is_previewable_image(proof_file),
        "proof_preview_kind": preview_kind(proof_file),
        "is_violation": bool(row.is_violation),
        "violation_deduction_id": row.violation_deduction_id or 0,
        "available_actions": ["void"] if row.status == "active" else [],
    }


def sick_leave_payloads(db: Session, rows: list[SickLeaveRecord]) -> list[dict]:
    """Serialize many sick-leave rows with batched employee/proof lookups."""
    employee_ids = {row.employee_id for row in rows}
    file_ids = {row.proof_file_id for row in rows}
    employees = (
        {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
        if employee_ids
        else {}
    )
    files = (
        {file_row.id: file_row for file_row in db.query(StoredFile).filter(StoredFile.id.in_(file_ids)).all()}
        if file_ids
        else {}
    )
    return [sick_leave_payload(db, row, employees=employees, files=files) for row in rows]


def months_between(start: date, end: date) -> list[str]:
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return result


def primary_gsm_for_attraction(db: Session, attraction_id: int, on_date: str | None = None) -> tuple[Employee | None, Role | None]:
    candidates = gsm_candidates_for_attraction(db, attraction_id, on_date)
    return candidates[0] if candidates else (None, None)
