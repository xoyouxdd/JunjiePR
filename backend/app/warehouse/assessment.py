"""Single-warehouse replenishment estimates and shared stock alerts."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Session, mapped_column

from app.database.connection import Base, get_db, write_transaction
from app.date_utils import months_between, subtract_calendar_months
from app.v2_auth import V2User, current_user
from app.v2_models import Employee
from app.warehouse.access import active_owner_ids, eligible_manager, is_manager, require_access, require_manager
from app.warehouse.models import (
    WarehouseBalance, WarehouseIssue, WarehouseIssueLine, WarehouseItem,
    WarehouseMovement, warehouse_now,
)


router = APIRouter(prefix="/warehouse")


class WarehouseThreshold(Base):
    __tablename__ = "warehouse_thresholds"

    item_id = mapped_column(ForeignKey(WarehouseItem.id), primary_key=True)
    minimum = mapped_column(Integer)
    yellow_upper = mapped_column(Integer)
    lead_days = mapped_column(Integer)
    safety_days = mapped_column(Integer)
    target_days = mapped_column(Integer)
    updated_by = mapped_column(ForeignKey(Employee.id), nullable=False)
    updated_at = mapped_column(DateTime, nullable=False)


class WarehouseAlert(Base):
    __tablename__ = "warehouse_alerts"

    id = mapped_column(Integer, primary_key=True)
    item_id = mapped_column(ForeignKey(WarehouseItem.id), nullable=False, index=True)
    level = mapped_column(String(12), nullable=False)
    status = mapped_column(String(24), nullable=False)
    assignee_id = mapped_column(ForeignKey(Employee.id))
    opened_at = mapped_column(DateTime, nullable=False)
    updated_at = mapped_column(DateTime, nullable=False)
    closed_at = mapped_column(DateTime)


Index("ux_warehouse_alert_active", WarehouseAlert.item_id, unique=True,
      sqlite_where=WarehouseAlert.closed_at.is_(None))


class ThresholdInput(BaseModel):
    minimum: int | None = Field(default=None, ge=1, le=10**8)
    yellow_upper: int | None = Field(default=None, ge=1, le=10**8)
    lead_days: int | None = Field(default=None, ge=1, le=365)
    safety_days: int | None = Field(default=None, ge=0, le=365)
    target_days: int | None = Field(default=None, ge=1, le=365)


class AlertInput(BaseModel):
    status: Literal["processing", "waiting_restock", "pending"]
    target_employee_id: int | None = None


def _totals(db: Session, item_ids: set[int]) -> dict[int, int]:
    if not item_ids:
        return {}
    rows = db.query(WarehouseBalance.item_id, func.sum(WarehouseBalance.quantity)).filter(
        WarehouseBalance.item_id.in_(item_ids),
    ).group_by(WarehouseBalance.item_id)
    return {item_id: int(quantity) for item_id, quantity in rows}


def stock_level(stock: int, setting: WarehouseThreshold | None) -> str:
    if stock <= 0:
        return "empty"
    if setting is None or setting.minimum is None:
        return "unset"
    if stock < setting.minimum:
        return "low"
    return "near" if stock <= (setting.yellow_upper or setting.minimum) else "healthy"


def sync_alerts(db: Session, item_ids) -> None:
    """Update active alerts inside the same writer transaction as a stock change."""
    db.flush()
    ids = set(item_ids)
    if not ids:
        return
    items = db.query(WarehouseItem).filter(WarehouseItem.id.in_(ids)).all()
    stock = _totals(db, ids)
    settings = {row.item_id: row for row in db.query(WarehouseThreshold).filter(WarehouseThreshold.item_id.in_(ids))}
    current = {row.item_id: row for row in db.query(WarehouseAlert).filter(
        WarehouseAlert.item_id.in_(ids), WarehouseAlert.closed_at.is_(None),
    )}
    now = warehouse_now()
    for item in items:
        alert = current.get(item.id)
        level = stock_level(stock.get(item.id, 0), settings.get(item.id))
        if item.active and item.opened_at and level in {"empty", "low"}:
            if alert is None:
                db.add(WarehouseAlert(item_id=item.id, level=level, status="pending", opened_at=now, updated_at=now))
            elif alert.level != level:
                alert.level = level
                alert.updated_at = now
        elif alert is not None:
            alert.status = "closed"
            alert.updated_at = alert.closed_at = now


def release_owner_alerts(db: Session, employee_id: int) -> None:
    db.query(WarehouseAlert).filter(
        WarehouseAlert.assignee_id == employee_id, WarehouseAlert.closed_at.is_(None),
    ).update({
        WarehouseAlert.assignee_id: None,
        WarehouseAlert.status: "pending",
        WarehouseAlert.updated_at: warehouse_now(),
    }, synchronize_session="fetch")


def _complete_start(opened_at: datetime) -> date:
    return opened_at.date() + timedelta(days=opened_at.time() != time.min)


def _usage_payload(db: Session, items: list, now: datetime) -> dict:
    ids = {item.id for item in items}
    result = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    if not ids:
        return result
    start = subtract_calendar_months(now.date().replace(day=1), 5)
    usage_day = func.date(func.coalesce(WarehouseMovement.usage_at, WarehouseMovement.occurred_at))
    entries = db.query(WarehouseMovement.item_id, usage_day,
                       func.sum(WarehouseMovement.usage_delta), func.sum(WarehouseMovement.direct_quantity)).filter(
        WarehouseMovement.item_id.in_(ids), usage_day >= start.isoformat(), usage_day <= now.date().isoformat(),
        (WarehouseMovement.usage_delta != 0) | (WarehouseMovement.direct_quantity != 0),
    ).group_by(WarehouseMovement.item_id, usage_day)
    for item_id, day, used, direct in entries:
        result[item_id][date.fromisoformat(day)] = [int(used or 0), int(direct or 0)]
    return result


def _stock_changes(db: Session, ids: set[int], since: date) -> dict:
    result = defaultdict(lambda: defaultdict(int))
    if not ids:
        return result
    day = func.date(WarehouseMovement.occurred_at)
    for item_id, value, delta in db.query(WarehouseMovement.item_id, day, func.sum(WarehouseMovement.delta)).filter(
        WarehouseMovement.item_id.in_(ids), WarehouseMovement.occurred_at >= datetime.combine(since, time.min),
    ).group_by(WarehouseMovement.item_id, day):
        result[item_id][date.fromisoformat(value)] = int(delta)
    return result


def _estimate(stock, complete_days, out_days, under_review, usage_30, setting):
    if complete_days < 30:
        return None, None, None, None, "积累满 30 个完整自然日后开始评估。"
    daily = usage_30 / 30
    if under_review:
        return round(daily, 3), None, None, None, "货位有待核对记录，请先核实库存。"
    if out_days:
        return round(daily, 3), None, None, None, "近 30 天出现缺货，估算交由负责人判断。"
    if daily <= 0:
        return 0, None, None, None, "近 30 天暂无有效领用，先查看实际记录。"
    coverage = round(stock / daily, 1)
    if not setting or not setting.lead_days:
        return round(daily, 3), coverage, None, None, "设置到货天数后可计算建议补货点。"
    suggestion = ceil(usage_30 * (setting.lead_days + (setting.safety_days or 0)) / 30)
    at_arrival = max(0, stock - daily * setting.lead_days)
    quantity = ceil(max(0, (daily * setting.target_days) - at_arrival) - 1e-9) if setting.target_days else None
    return round(daily, 3), coverage, suggestion, quantity, None


def _settings_payload(setting) -> dict:
    return {key: getattr(setting, key, None) for key in (
        "minimum", "yellow_upper", "lead_days", "safety_days", "target_days",
    )}


def assessment_payload(db: Session, now: datetime | None = None) -> dict:
    now = now or warehouse_now()
    today = now.date()
    from_30 = today - timedelta(days=30)
    from_7 = today - timedelta(days=7)
    items = db.query(WarehouseItem).filter(WarehouseItem.active.is_(True)).order_by(WarehouseItem.name, WarehouseItem.id).all()
    ids = {row.id for row in items}
    stock = _totals(db, ids)
    settings = {row.item_id: row for row in db.query(WarehouseThreshold).filter(WarehouseThreshold.item_id.in_(ids))}
    reviewed = {row[0] for row in db.query(WarehouseIssueLine.item_id).join(
        WarehouseIssue, WarehouseIssue.id == WarehouseIssueLine.issue_id,
    ).filter(WarehouseIssue.status != "closed", WarehouseIssueLine.item_id.in_(ids)).distinct()}
    usages = _usage_payload(db, items, now)
    changes = _stock_changes(db, ids, from_30)
    months = months_between(subtract_calendar_months(today.replace(day=1), 5), today)
    rows = []
    for item in items:
        current = stock.get(item.id, 0)
        setting = settings.get(item.id)
        complete_from = _complete_start(item.opened_at) if item.opened_at else today
        tracked = max(0, (today - complete_from).days)
        daily_rows = usages[item.id]
        summary = {month: [0, 0] for month in months}
        usage_7 = usage_30 = direct_7 = direct_30 = 0
        for day, (used, direct) in daily_rows.items():
            month = day.strftime("%Y-%m")
            if month in summary:
                summary[month][0] += used
                summary[month][1] += direct
            if from_30 <= day < today:
                usage_30 += used
                direct_30 += direct
            if from_7 <= day < today:
                usage_7 += used
                direct_7 += direct

        current_by_day = current - sum(changes[item.id].values())
        out_days = 0
        for elapsed in range(30):
            day = from_30 + timedelta(days=elapsed)
            current_by_day += changes[item.id].get(day, 0)
            if day >= complete_from and current_by_day <= 0:
                out_days += 1
        daily, coverage, suggested, quantity, reason = _estimate(
            current, tracked, out_days, item.id in reviewed, usage_30, setting,
        )
        rows.append({
            "item_id": item.id, "name": item.name, "spec": item.spec, "unit": item.unit, "stock": current,
            "level": stock_level(current, setting) if item.opened_at else "unset", "days_tracked": tracked,
            "days_out_of_stock": out_days, "under_review": item.id in reviewed, "usage_7": usage_7, "usage_30": usage_30,
            "direct_7": direct_7, "direct_30": direct_30, "usage_today": daily_rows[today][0],
            "direct_today": daily_rows[today][1], "daily_average": daily, "coverage_days": coverage,
            "predicted_days": coverage, "suggested_point": suggested, "suggested_quantity": quantity,
            "estimate_reason": reason, **_settings_payload(setting),
            "months": [{"month": month, "usage": used, "direct": direct} for month, (used, direct) in summary.items()],
        })
    return {"items": rows, "as_of": now.isoformat(timespec="seconds") + "+08:00"}


def action_counts(db: Session, user: V2User) -> tuple[int, int, int]:
    if not is_manager(db, user):
        return 0, 0, 0
    issues = db.query(WarehouseIssue.id).filter(WarehouseIssue.status != "closed").count()
    alerts = dict(db.query(WarehouseAlert.level, func.count(WarehouseAlert.id)).filter(
        WarehouseAlert.closed_at.is_(None),
    ).group_by(WarehouseAlert.level).all())
    return issues, alerts.get("low", 0), alerts.get("empty", 0)


def _alerts_payload(db: Session) -> dict:
    rows = db.query(WarehouseAlert, WarehouseItem).join(WarehouseItem, WarehouseItem.id == WarehouseAlert.item_id).filter(
        WarehouseAlert.closed_at.is_(None),
    ).order_by((WarehouseAlert.level == "empty").desc(), WarehouseAlert.opened_at).all()
    totals = _totals(db, {item.id for _, item in rows})
    assignee_ids = {alert.assignee_id for alert, _ in rows if alert.assignee_id}
    if assignee_ids:
        assignee_ids.intersection_update(active_owner_ids(db))
    names = dict(db.query(Employee.id, Employee.name).filter(Employee.id.in_(assignee_ids)).all()) if assignee_ids else {}
    return {"items": [{
        "id": alert.id, "item_id": item.id, "name": item.name, "unit": item.unit,
        "stock": totals.get(item.id, 0), "level": alert.level,
        "status": alert.status if not alert.assignee_id or alert.assignee_id in names else "pending",
        "assignee_id": alert.assignee_id if alert.assignee_id in names else None, "assignee_name": names.get(alert.assignee_id),
        "opened_at": alert.opened_at.isoformat(timespec="seconds") + "+08:00",
        "updated_at": alert.updated_at.isoformat(timespec="seconds") + "+08:00",
    } for alert, item in rows]}


@router.get("/assessment")
def get_assessment(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    require_access(user)
    return assessment_payload(db)


@router.put("/assessment/{item_id}")
def update_assessment(item_id: int, body: ThresholdInput, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if body.yellow_upper is not None and (body.minimum is None or body.yellow_upper < body.minimum):
        raise HTTPException(422, "黄色提醒上限须大于或等于最低库存线。")
    with write_transaction(db):
        require_manager(db, user)
        item = db.get(WarehouseItem, item_id)
        if not item or not item.active:
            raise HTTPException(404, "该物资已停用或不存在。")
        row = db.get(WarehouseThreshold, item_id)
        if row is None:
            row = WarehouseThreshold(item_id=item_id)
            db.add(row)
        for name, value in body.model_dump().items():
            setattr(row, name, value)
        row.updated_by = user.id
        row.updated_at = warehouse_now()
        sync_alerts(db, {item_id})
        db.commit()
        return {"item_id": item_id, **_settings_payload(row)}


@router.get("/alerts")
def get_alerts(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    require_manager(db, user)
    return _alerts_payload(db)


@router.patch("/alerts/{item_id}")
def update_alert(item_id: int, body: AlertInput, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    with write_transaction(db):
        require_manager(db, user)
        row = db.query(WarehouseAlert).filter(WarehouseAlert.item_id == item_id, WarehouseAlert.closed_at.is_(None)).one_or_none()
        if not row:
            raise HTTPException(404, "该库存待办已关闭。")
        if row.assignee_id not in {None, user.id} and eligible_manager(db, row.assignee_id):
            raise HTTPException(409, "已有负责人接手，请联系对方交接。")
        if body.target_employee_id is not None:
            if not eligible_manager(db, body.target_employee_id):
                raise HTTPException(422, "请选择当前仓库负责人。")
            row.assignee_id = body.target_employee_id
        else:
            row.assignee_id = None if body.status == "pending" else user.id
        row.status = body.status
        row.updated_at = warehouse_now()
        db.commit()
        return _alerts_payload(db)
