"""Pending-material lookup and business-conflict response."""
from __future__ import annotations

from app.record_payloads import deduction_payload
from app.v2_models import DeductionRecord
from fastapi import HTTPException
from sqlalchemy.orm import Session


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
