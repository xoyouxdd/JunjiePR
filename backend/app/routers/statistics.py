"""Statistics HTTP permissions, cache responses and export orchestration."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from io import BytesIO
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.access_policy import scoped_hr_attraction_ids
from app.data_cache import PR_RANKING_CACHE_SECONDS, STATISTICS_CACHE_SECONDS, _pr_ranking_cache_lock, _pr_ranking_response_cache, _statistics_cache_lock, _statistics_response_cache
from app.excel_export import append_supervisor_score_sheets, build_pr_rankings_workbook, build_statistics_workbook
from app.excel_export_utils import content_disposition
from app.score_queries import employee_month_scores
from app.v2_auth import V2User, current_user, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, AuditLog, Employee
from app.v2_watermark import watermark_workbook
from app.services.attendance import ensure_month_attendance
from app.services.audit import write_audit
from app.services.organization import direct_member_ids
from app.services.monthly_statistics import dashboard_payload, statistics_hierarchy, statistics_payload
from app.services.performance_details import member_records_payload, member_score_detail_payload, member_score_summary_payload, statistics_details_payload
from app.services.performance_rankings import gsm_recognizer_ranking_payload, pr_ranking_payload, ranking_employees, ranking_group_labels
from app.services.statistics_trends import TREND_DEFAULT_MONTHS, TREND_MAX_MONTHS, TREND_SCORE_FIELDS, statistics_trend_payload as _statistics_trend_payload, trend_month_keys

router = APIRouter()


@router.get("/dashboard")
def dashboard(month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    month = month or date.today().strftime("%Y-%m")
    return dashboard_payload(db, month, user)


@router.get("/member-records")
def member_records(
    month: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    record_type: str = "all",
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("MEMBER_RECORDS")),
):
    member_ids = direct_member_ids(db, user.id, include_overseen=True)
    return member_records_payload(db, member_ids, month, keyword, status, record_type)


@router.get("/member-score-summary")
def member_score_summary(
    month: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("MEMBER_RECORDS")),
):
    month = month or date.today().strftime("%Y-%m")
    member_ids = direct_member_ids(db, user.id, include_overseen=True)
    return member_score_summary_payload(db, month, member_ids, keyword, score_loader=employee_month_scores)


@router.get("/pr-rankings")
def pr_rankings(
    start_date: str,
    end_date: str,
    category: str = "overall",
    subtype_id: int | None = None,
    sort_by: str = "score",
    keyword: str = "",
    attraction_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
    population: str = "frontline",
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    page = max(1, page)
    page_size = min(max(10, page_size), 100)
    cache_key = (user.id, start_date, end_date, category, subtype_id, sort_by, keyword.strip(), page, page_size, attraction_id, population)
    with _pr_ranking_cache_lock:
        now = monotonic()
        cached = _pr_ranking_response_cache.get(cache_key)
        if cached and cached[0] > now:
            content = cached[1]
        else:
            payload = pr_ranking_payload(db, user, start_date, end_date, category, subtype_id, sort_by, keyword, page, page_size, attraction_id, population)
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            _pr_ranking_response_cache[cache_key] = (now + PR_RANKING_CACHE_SECONDS, content)
            if len(_pr_ranking_response_cache) > 256:
                _pr_ranking_response_cache.clear()
                _pr_ranking_response_cache[cache_key] = (now + PR_RANKING_CACHE_SECONDS, content)
    return Response(content=content, media_type="application/json")


@router.get("/pr-rankings/export")
def export_pr_rankings(
    start_date: str,
    end_date: str,
    category: str = "overall",
    subtype_id: int | None = None,
    sort_by: str = "score",
    keyword: str = "",
    attraction_id: int | None = None,
    population: str = "frontline",
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    if user.role.code not in {"GSM", "AM", "OM"}:
        raise HTTPException(403, "当前角色仅支持查询PR排名，不能导出景点圈数据")
    data = pr_ranking_payload(db, user, start_date, end_date, category, subtype_id, sort_by, keyword, 1, 5000, attraction_id, population)
    wb = build_pr_rankings_workbook(db, data, category)
    watermark_workbook(wb, user.employee.employee_no)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    write_audit(db, user.employee, "导出PR排名", "pr_ranking_export", category, after={"start_date": start_date, "end_date": end_date, "subtype_id": subtype_id, "sort_by": sort_by, "keyword": keyword, "row_count": len(data["rows"])})
    db.commit()
    ascii_filename = f"pr_rankings_{category}_{start_date}_{end_date}.xlsx"
    display_filename = f"PR排名_{category}_{start_date}_{end_date}.xlsx"
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": content_disposition(ascii_filename, display_filename)})


@router.get("/statistics")
def statistics(month: str, attraction_id: int | None = None, keyword: str | None = None, title: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("DATA_VIEW", "DATA_EXPORT"))):
    cache_key = (user.id, tuple(sorted(user.role_codes)), month, attraction_id, (keyword or "").strip(), (title or "").strip().upper())
    with _statistics_cache_lock:
        now = monotonic()
        cached = _statistics_response_cache.get(cache_key)
        if cached and cached[0] > now:
            content = cached[1]
        else:
            payload = statistics_payload(db, month, attraction_id, keyword, title, user)
            content = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda value: float(value) if isinstance(value, Decimal) else str(value),
            ).encode("utf-8")
            _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
            if len(_statistics_response_cache) > 128:
                _statistics_response_cache.clear()
                _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
    return Response(content=content, media_type="application/json")


def statistics_trend_payload(db: Session, end_month: str, months: int, attraction_id: int | None, title: str | None, user: V2User) -> dict:
    """Compatibility wrapper with an explicit monthly-statistics dependency."""
    return _statistics_trend_payload(db, end_month, months, attraction_id, title, user, payload_loader=statistics_payload)


@router.get("/statistics/trend")
def statistics_trend(month: str, months: int = TREND_DEFAULT_MONTHS, attraction_id: int | None = None, title: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("DATA_VIEW", "DATA_EXPORT"))):
    cache_key = ("trend", user.id, tuple(sorted(user.role_codes)), month, months, attraction_id, (title or "").strip().upper())
    with _statistics_cache_lock:
        now = monotonic()
        cached = _statistics_response_cache.get(cache_key)
        if cached and cached[0] > now:
            content = cached[1]
        else:
            payload = statistics_trend_payload(db, month, months, attraction_id, title, user)
            content = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda value: float(value) if isinstance(value, Decimal) else str(value),
            ).encode("utf-8")
            _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
            if len(_statistics_response_cache) > 128:
                _statistics_response_cache.clear()
                _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
    return Response(content=content, media_type="application/json")


@router.get("/statistics/details")
def statistics_details(
    month: str,
    employee_ids: str,
    effective_only: bool = False,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("DATA_VIEW", "DATA_EXPORT")),
):
    try:
        date.fromisoformat(f"{month}-01")
        selected_ids = list(dict.fromkeys(int(value) for value in employee_ids.split(",") if value.strip()))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "月份或员工参数格式不正确") from exc
    if not selected_ids or len(selected_ids) > 200:
        raise HTTPException(400, "每次可查询1至200名员工明细")
    employees = db.query(Employee).filter(Employee.id.in_(selected_ids)).all()
    if len(employees) != len(selected_ids):
        raise HTTPException(404, "部分员工不存在")
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None and any(employee.attraction_id not in allowed_attractions for employee in employees):
        raise HTTPException(403, "景点圈HR只能查看所属景点圈数据")
    details_savepoint = db.begin_nested()
    try:
        ensure_month_attendance(db, month, selected_ids)
        db.flush()
        details = statistics_details_payload(db, month, selected_ids)
    finally:
        if details_savepoint.is_active:
            details_savepoint.rollback()
    if effective_only:
        for detail in details.values():
            detail["all_records"] = [record for record in detail.get("all_records", []) if record.get("included")]
    return {"month": month, "details": details}


@router.get("/statistics/my-exports")
def my_statistics_exports(
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("DATA_EXPORT")),
):
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.operator_id == user.id, AuditLog.action == "导出统计数据")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(8)
        .all()
    )
    attraction_ids: set[int] = set()
    parsed_rows: list[tuple[AuditLog, dict]] = []
    for row in rows:
        try:
            details = json.loads(row.after_json or "{}")
        except (TypeError, ValueError):
            details = {}
        attraction_id = details.get("attraction_id")
        if isinstance(attraction_id, int):
            attraction_ids.add(attraction_id)
        parsed_rows.append((row, details))
    attraction_names = {
        attraction.id: attraction.name
        for attraction in db.query(Attraction).filter(Attraction.id.in_(attraction_ids)).all()
    } if attraction_ids else {}
    return {
        "items": [
            {
                "id": row.id,
                "exported_at": row.created_at.strftime("%Y-%m-%d %H:%M"),
                "month": row.entity_id or "",
                "attraction_name": attraction_names.get(details.get("attraction_id"), "全部景点圈"),
                "title": details.get("title") or "CM/TR全部",
                "keyword": details.get("keyword") or "",
                "employee_count": int(details.get("employee_count") or 0),
            }
            for row, details in parsed_rows
        ]
    }


@router.get("/statistics/export")
def export_statistics(month: str, attraction_id: int | None = None, keyword: str | None = None, title: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("DATA_EXPORT"))):
    data = statistics_payload(db, month, attraction_id, keyword, title, user, include_records=True)
    wb, attraction_label, title_label = build_statistics_workbook(
        db,
        data,
        month=month,
        attraction_id=attraction_id,
        keyword=keyword,
        exporter_no=user.employee.employee_no,
        exporter_name=user.name,
        exporter_role_name=user.role.name,
        exporter_role_code=user.role.code,
    )
    if data["title"] != "SUPERVISOR" and not (keyword or "").strip():
        supervisor_data = statistics_payload(db, month, attraction_id, None, "SUPERVISOR", user, include_records=True)
        append_supervisor_score_sheets(wb, db, supervisor_data, month)
    watermark_workbook(wb, user.employee.employee_no)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    write_audit(db, user.employee, "导出统计数据", "statistics_export", month, after={"attraction_id": attraction_id, "title": data["title"], "keyword": keyword or "", "employee_count": len(data["scores"])})
    db.commit()
    ascii_filename = f"recognition_v2_{month.replace('-', '_')}_circle-{attraction_id or 'all'}_{data['title'] or 'all'}.xlsx"
    display_filename = f"认可数据_{month}_{attraction_label}_{title_label}.xlsx"
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": content_disposition(ascii_filename, display_filename)})
