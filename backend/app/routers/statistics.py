"""Statistics HTTP permissions, cache responses and export orchestration."""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from io import BytesIO
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt
from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.access_policy import scoped_hr_attraction_ids
from app.date_utils import parse_score_month
from app.month_rank_policy import monthly_ranks_enabled
from app.role_constants import SCORED_BASE_CODES
from app.data_cache import PR_RANKING_CACHE_SECONDS, STATISTICS_CACHE_SECONDS, _pr_ranking_cache_lock, _pr_ranking_response_cache, _statistics_cache_lock, _statistics_response_cache
from app.excel_export import append_supervisor_score_sheets, build_pr_rankings_workbook, build_statistics_workbook
from app.excel_export_utils import content_disposition
from app.excel_presentation import finalize_statistics_workbook, save_export_workbook
from app.score_queries import employee_month_scores
from app.v2_auth import V2User, current_user, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, AuditLog, Employee, EmployeeAvatarPreference, EmployeeAvatarWardrobe, EmployeeHonorMemory, EmployeeHonorStyle, EmployeeMonthRankSnapshot
from app.services.avatars import avatar_catalog, own_avatar
from app.v2_watermark import watermark_workbook
from app.services.attendance import ensure_month_attendance
from app.services.audit import write_audit
from app.services.organization import direct_member_ids
from app.services.monthly_ranks import member_month_rank
from app.services.honors import honor_records, self_honors
from app.services.monthly_statistics import dashboard_payload, statistics_hierarchy, statistics_payload
from app.services.performance_details import member_records_payload, member_score_detail_payload, member_score_summary_payload, statistics_details_payload
from app.services.performance_rankings import gsm_recognizer_ranking_payload, pr_ranking_payload, ranking_employees, ranking_group_labels
from app.services.statistics_trends import TREND_DEFAULT_MONTHS, TREND_MAX_MONTHS, TREND_SCORE_FIELDS, statistics_trend_payload as _statistics_trend_payload, trend_month_keys

router = APIRouter()


class AvatarPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    avatar_key: str
    frame: str = "plain"


AVATAR_FRAMES = (("plain", "简洁"), ("autumn", "秋日金"), ("ocean", "晴空蓝"),
                 ("forest", "森林绿"), ("winter", "冬日冰蓝"), ("thumb", "点赞鼓励"),
                 ("sun", "暖阳能量"), ("spark", "星芒闪耀"), ("neon", "霓虹光环"), ("cosmic", "星际流光"))


def avatar_frame_choices(db, user):
    choices = [{"key": key, "label": label} for key, label in AVATAR_FRAMES]
    if monthly_ranks_enabled() and user.base_role.code in SCORED_BASE_CODES:
        months = db.query(EmployeeMonthRankSnapshot.score_month).filter(
            EmployeeMonthRankSnapshot.employee_id == user.id,
            EmployeeMonthRankSnapshot.score_month < date.today().strftime("%Y-%m")
        ).distinct().order_by(EmployeeMonthRankSnapshot.score_month.desc()).limit(12).all()
        for (month,) in months:
            rank = member_month_rank(db, month, user.employee)
            if rank.get("status") == "ranked" and rank.get("month_closed"):
                choices.append({"key": f"medal:{month}:{rank['tier']}", "label": f"{month} · {rank['label']}"})
    return choices


@router.get("/self-avatar")
def get_self_avatar(response: Response, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.base_role.code in {"HR_CIRCLE", "HR_ADMIN"}:
        raise HTTPException(403, "HR账号不使用个人头像装扮")
    if getattr(user, "rotation_temporary", False):
        raise HTTPException(403, "临时轮岗账号不支持员工头像装扮")
    response.headers.update({"Cache-Control": "private, no-store", "Vary": "Cookie"})
    return {**own_avatar(db, user.id), "frames": avatar_frame_choices(db, user)}


@router.put("/self-avatar")
def save_self_avatar(payload: AvatarPreferenceUpdate, response: Response,
                     db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.base_role.code in {"HR_CIRCLE", "HR_ADMIN"}:
        raise HTTPException(403, "HR账号不使用个人头像装扮")
    if getattr(user, "rotation_temporary", False):
        raise HTTPException(403, "临时轮岗账号不支持员工头像装扮")
    response.headers.update({"Cache-Control": "private, no-store", "Vary": "Cookie"})
    if payload.avatar_key and payload.avatar_key not in avatar_catalog():
        raise HTTPException(422, "请选择头像库中的有效头像")
    # Saving ordinary frames never loads monthly ranks. A medal frame validates
    # only its selected month; opening the wardrobe loads the bounded history.
    frames = [{"key": key} for key, _ in AVATAR_FRAMES]
    match = re.fullmatch(r"medal:(\d{4}-(?:0[1-9]|1[0-2])):(0|10|20|30|40|50|60|70|80|90)", payload.frame)
    if match and monthly_ranks_enabled() and user.base_role.code in SCORED_BASE_CODES:
        month, tier = match.groups()
        if month < date.today().strftime("%Y-%m"):
            rank = member_month_rank(db, month, user.employee)
            if rank.get("status") == "ranked" and rank.get("month_closed") and rank.get("tier") == int(tier):
                frames.append({"key": payload.frame})
    if payload.frame not in {item["key"] for item in frames}:
        raise HTTPException(422, "请选择可用边框；历史月度勋章可能已重开或失效")
    row = db.get(EmployeeAvatarPreference, user.id)
    if row is None:
        row = EmployeeAvatarPreference(employee_id=user.id)
        db.add(row)
    row.avatar_key, row.frame = payload.avatar_key, payload.frame
    write_audit(db, user.employee, "avatar_preference", "employee", user.id, after=payload.model_dump())
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "头像刚刚被其他页面更新，请重新打开后保存")
    return own_avatar(db, user.id)


class AvatarOutfit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    avatar_key: str
    frame: str


class AvatarWardrobeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    favorites: list[str]
    recent: list[str]
    presets: list[AvatarOutfit | None]


def _wardrobe_access(user):
    if user.base_role.code in {"HR_CIRCLE", "HR_ADMIN"} or getattr(user, "rotation_temporary", False):
        raise HTTPException(403, "该账号不支持员工头像装扮")


@router.get("/self-avatar-wardrobe")
def get_avatar_wardrobe(response: Response, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    _wardrobe_access(user)
    response.headers.update({"Cache-Control": "private, no-store", "Vary": "Cookie"})
    row = db.get(EmployeeAvatarWardrobe, user.id)
    return json.loads(row.data) if row else {"favorites": [], "recent": [], "presets": [None, None, None]}


@router.put("/self-avatar-wardrobe")
def save_avatar_wardrobe(payload: AvatarWardrobeUpdate, response: Response,
                         db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    _wardrobe_access(user)
    response.headers.update({"Cache-Control": "private, no-store", "Vary": "Cookie"})
    catalog = avatar_catalog()
    if len(payload.favorites) > 100 or len(payload.recent) > 6 or len(payload.presets) != 3:
        raise HTTPException(422, "头像收藏或搭配数量无效")
    if any(k not in catalog for k in payload.favorites + payload.recent):
        raise HTTPException(422, "请选择头像库中的有效头像")
    allowed = {key for key, _ in AVATAR_FRAMES}
    for outfit in payload.presets:
        if outfit and (outfit.avatar_key not in catalog or (outfit.frame not in allowed and not re.fullmatch(r"medal:\d{4}-(?:0[1-9]|1[0-2]):(?:0|10|20|30|40|50|60|70|80|90)", outfit.frame))):
            raise HTTPException(422, "搭配中的头像或边框无效")
    data = payload.model_dump()
    data["favorites"] = list(dict.fromkeys(data["favorites"]))
    data["recent"] = list(dict.fromkeys(data["recent"]))
    row = db.get(EmployeeAvatarWardrobe, user.id)
    if row is None:
        row = EmployeeAvatarWardrobe(employee_id=user.id)
        db.add(row)
    row.data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    db.commit()
    return data


class HonorPreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skin: Literal["sea", "gold", "moon", "ice", "jade", "copper", "forest", "minimal"] | None = None
    selected_record_id: StrictInt | None = None
    favorite: StrictBool | None = None


def honor_month(response: Response, month: str | None, user: V2User) -> str:
    headers = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
    response.headers.update(headers)
    if not monthly_ranks_enabled():
        raise HTTPException(404, "月度荣誉功能暂不可用", headers=headers)
    if user.base_role.code not in SCORED_BASE_CODES:
        raise HTTPException(403, "仅可查看本人的月度荣誉", headers=headers)
    value = parse_score_month(month or date.today().strftime("%Y-%m"))
    if value > date.today().strftime("%Y-%m"):
        raise HTTPException(400, "仅可查看当前及历史月份", headers=headers)
    return value


@router.get("/self-honors")
def get_self_honors(response: Response, month: str | None = None,
                    db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    return self_honors(db, honor_month(response, month, user), user.employee)


@router.put("/self-honors")
def save_self_honors(payload: HonorPreferenceUpdate, response: Response, month: str | None = None,
                     db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    month = honor_month(response, month, user)
    fields = payload.model_fields_set
    if not fields:
        raise HTTPException(400, "请选择要保存的装扮或回顾")
    if ("skin" in fields and payload.skin is None) or ("favorite" in fields and payload.favorite is None):
        raise HTTPException(422, "装扮和收藏状态不能为空")
    if payload.selected_record_id is not None:
        ids = {row.id for row in honor_records(db, month, user.id)}
        if payload.selected_record_id not in ids:
            raise HTTPException(400, "只能选择本人本月已确认的有效认可")
    if "favorite" in fields and payload.favorite:
        current = self_honors(db, month, user.employee)
        if current["letter_state"] != "ready":
            raise HTTPException(409, "本月回顾尚未生成，暂不能收藏")
    if payload.skin is not None:
        style = db.get(EmployeeHonorStyle, user.id)
        if style is None:
            style = EmployeeHonorStyle(employee_id=user.id)
            db.add(style)
        style.skin = payload.skin
    if fields & {"selected_record_id", "favorite"}:
        memory = db.get(EmployeeHonorMemory, (user.id, month))
        if memory is None:
            memory = EmployeeHonorMemory(employee_id=user.id, score_month=month)
            db.add(memory)
        if "selected_record_id" in fields:
            memory.selected_record_id = payload.selected_record_id
        if "favorite" in fields:
            memory.favorite = payload.favorite
    write_audit(db, user.employee, "honor_preference", "employee", user.id,
                after={"month": month, **payload.model_dump(exclude_unset=True)})
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "设置刚刚被其他页面更新，请刷新后重试")
    return self_honors(db, month, user.employee)


@router.get("/dashboard")
def dashboard(month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    month = parse_score_month(month or date.today().strftime("%Y-%m"))
    return dashboard_payload(db, month, user)


@router.get("/self-month-rank")
def self_month_rank(response: Response, month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    private_headers = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
    response.headers.update(private_headers)
    if not monthly_ranks_enabled():
        raise HTTPException(404, "游戏段位功能尚未开放", headers=private_headers)
    if user.base_role.code not in SCORED_BASE_CODES:
        raise HTTPException(403, "仅可查看本人计分身份的游戏段位", headers=private_headers)
    month = parse_score_month(month or date.today().strftime("%Y-%m"))
    if month > date.today().strftime("%Y-%m"):
        raise HTTPException(400, "仅可查看当前及历史月份", headers=private_headers)
    return member_month_rank(db, month, user.employee)


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
    month = parse_score_month(month or date.today().strftime("%Y-%m"))
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
    if data["total"] > len(data["rows"]):
        data = pr_ranking_payload(db, user, start_date, end_date, category, subtype_id, sort_by, keyword, 1, data["total"], attraction_id, population)
    if data["total"] != len(data["rows"]):
        raise HTTPException(409, "排名数据已变化，请重新导出以取得完整记录")
    wb = build_pr_rankings_workbook(db, data, category)
    watermark_workbook(wb, user.employee.employee_no)
    output = BytesIO()
    save_export_workbook(wb, output)
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
    finalize_statistics_workbook(wb)
    watermark_workbook(wb, user.employee.employee_no)
    output = BytesIO()
    save_export_workbook(wb, output)
    output.seek(0)
    write_audit(db, user.employee, "导出统计数据", "statistics_export", month, after={"attraction_id": attraction_id, "title": data["title"], "keyword": keyword or "", "employee_count": len(data["scores"])})
    db.commit()
    ascii_filename = f"recognition_v2_{month.replace('-', '_')}_circle-{attraction_id or 'all'}_{data['title'] or 'all'}.xlsx"
    display_filename = f"认可数据_{month}_{attraction_label}_{title_label}.xlsx"
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": content_disposition(ascii_filename, display_filename)})
