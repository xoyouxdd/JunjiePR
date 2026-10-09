"""Month-close scope, mutation gate and unresolved-work checklist."""
from __future__ import annotations

from app.access_policy import scoped_hr_attraction_ids
from app.date_utils import parse_score_month
from app.services.organization import managed_attraction_ids
from app.v2_auth import V2User
from app.v2_models import Attraction, CircleTransferRequest, DeductionFollowUp, DeductionRecord, DeductionUpgradeRequest, Employee, MonthClosure, RecognitionRecord
from datetime import date, datetime, timedelta
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session


MONTH_CLOSE_ROLE_CODES = {"HR_CIRCLE", "SYSTEM_ADMIN"}


MONTH_CLOSE_EFFECTIVE_DATE = "2026-09-01"


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
