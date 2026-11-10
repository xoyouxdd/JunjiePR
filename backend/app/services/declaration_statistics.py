"""Read-only declaration statistics shared by API exports and HR reports."""
from __future__ import annotations
from app.employee_numbers import employee_number

import re
from collections import Counter, defaultdict
from datetime import date

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.date_utils import subtract_calendar_months
from app.deduction_policy import deduction_counts_for_score, unused_statement_upgrade_filters
from app.role_constants import FRONTLINE_CODES
from app.score_policy import loa_month_exclusion
from app.services.identity import base_roles_at
from app.services.scoring_policies import category
from app.services.upgrade_recovery import upgrade_work_status_name

from app.v2_models import (
    Attraction, DeductionLevel, DeductionRecord, DeductionType,
    DeductionUpgradeRequest, Employee, StoredFile, UserAccount,
)

STATUS_LABELS = {"active": "已生效", "pending_material": "待补材料", "material_processing": "待补材料", "material_failed": "待补材料", "pending_upgrade": "审核中", "void": "已作废"}
MATERIAL_LABELS = {"ready": "材料已就绪", "missing": "待补材料", "processing": "材料处理中", "failed": "材料处理失败"}
UPGRADE_LABELS = {"pending": "审核中", "awaiting_reviewer": "材料已就绪 · 待重新指派", "material_processing": "材料处理中", "source_first": "已升级（原声明）", "source_second": "升级来源（不计分）", "result": "升级结果", "rejected": "审核不通过", "eligible": "可升级"}


def checked_month(month: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(400, "月份格式无效")
    return month


def _open_statement_slots(db, selected_ids, levels, types):
    statement = next((level for level in levels.values() if level.code == "STATEMENT" and level.active), None)
    has_result_level = any(level.active and level.code in {"MEMO", "WARNING_1"} for level in levels.values())
    if not statement or not selected_ids or not has_result_level:
        return set()
    today = date.today()
    day = today.isoformat()
    candidates = (
        db.query(DeductionRecord.id, DeductionRecord.employee_id, DeductionType.code)
        .join(DeductionType, DeductionType.id == DeductionRecord.deduction_type_id)
        .join(Employee, Employee.id == DeductionRecord.employee_id)
        .join(UserAccount, UserAccount.employee_id == Employee.id)
        .filter(
            DeductionRecord.id.in_(selected_ids),
            *unused_statement_upgrade_filters(statement.id),
            DeductionRecord.occurred_on.between(subtract_calendar_months(today, 3).isoformat(), day),
            Employee.is_active.is_(True),
            UserAccount.enabled.is_(True),
        ).all()
    )
    if not candidates:
        return set()
    source_types = set()
    for kind in types.values():
        if kind.active:
            policy = category(db, "deduction", "all", kind.code, day)
            if policy["active"] and policy["repeat"] == "upgrade":
                source_types.update(policy["group"])
    sources = [(record_id, employee_id) for record_id, employee_id, code in candidates if code in source_types]
    roles = base_roles_at(db, {employee_id for _, employee_id in sources}, today)
    frontline_ids = {employee_id for employee_id, role in roles.items() if role and role.code in FRONTLINE_CODES}
    return {record_id for record_id, employee_id in sources if employee_id in frontline_ids}


def declaration_payload(
    db: Session, month: str, attraction_id: int | None, level_id: int | None,
    type_id: int | None, status: str, keyword: str, *, include_upgrade_slots: bool = False,
) -> dict:
    checked_month(month)
    if status and status not in {*STATUS_LABELS.values(), "current"}:
        raise HTTPException(400, "业务状态无效")
    attraction_rows = db.query(Attraction).all()
    attractions = {row.id: row.name for row in attraction_rows}
    levels = {row.id: row for row in db.query(DeductionLevel).all()}
    types = {row.id: row for row in db.query(DeductionType).all()}
    if attraction_id is not None and attraction_id not in attractions:
        raise HTTPException(400, "景点圈无效")
    if level_id is not None and level_id not in levels:
        raise HTTPException(400, "处分等级无效")
    if type_id is not None and type_id not in types:
        raise HTTPException(400, "处分类型无效")

    # Approved upgrades store both the second statement and its final result.
    # The result represents the second event; retaining both would double count.
    query = db.query(DeductionRecord).filter(DeductionRecord.deduction_month == month)
    if attraction_id is not None:
        query = query.filter(DeductionRecord.attraction_id_snapshot == attraction_id)
    if level_id is not None:
        query = query.filter(DeductionRecord.deduction_level_id == level_id)
    if type_id is not None:
        query = query.filter(DeductionRecord.deduction_type_id == type_id)
    needle = employee_number(keyword).casefold()
    selected = query.order_by(DeductionRecord.occurred_on.desc(), DeductionRecord.id.desc()).all()
    selected = [row for row in selected
                if (not status or (status == "current" and STATUS_LABELS.get(row.status) in {"已生效", "审核中", "待补材料"})
                    or STATUS_LABELS.get(row.status, row.status) == status)
                and (not needle or needle in row.employee_name.casefold() or needle in row.employee_no.casefold())]
    approved_seconds = {
        second_id for (second_id,) in db.query(DeductionUpgradeRequest.second_deduction_id)
        .join(DeductionRecord, DeductionRecord.id == DeductionUpgradeRequest.second_deduction_id)
        .filter(DeductionUpgradeRequest.status == "approved", DeductionUpgradeRequest.result_deduction_id.isnot(None),
                DeductionRecord.deduction_month == month).all()
    }
    # Relationships are keyed by the work order, including legacy results whose
    # own upgrade_request_id was not populated. Resolve outside the current
    # month/level/type filters so a source can always reach its actual result.
    selected_ids = {row.id for row in selected}
    open_slots = _open_statement_slots(db, selected_ids, levels, types) if include_upgrade_slots else set()
    upgrades = db.query(DeductionUpgradeRequest).filter(or_(
        DeductionUpgradeRequest.first_deduction_id.in_(selected_ids),
        DeductionUpgradeRequest.second_deduction_id.in_(selected_ids),
        DeductionUpgradeRequest.result_deduction_id.in_(selected_ids),
    )).all() if selected_ids else []
    # A released first statement can have multiple rejected work orders and
    # one current pair. Keep every historical edge reachable from either side,
    # even when the entry point is a filtered result or a rejected second row.
    if upgrades:
        first_ids = {work.first_deduction_id for work in upgrades}
        upgrades = db.query(DeductionUpgradeRequest).filter(
            DeductionUpgradeRequest.first_deduction_id.in_(first_ids)).order_by(DeductionUpgradeRequest.id).all()
    related_ids = {rid for work in upgrades for rid in
                   (work.first_deduction_id, work.second_deduction_id, work.result_deduction_id) if rid}
    all_rows = {row.id: row for row in selected}
    if related_ids - selected_ids:
        all_rows.update({row.id: row for row in db.query(DeductionRecord)
                         .filter(DeductionRecord.id.in_(related_ids - selected_ids)).all()})
    # Apply the existing monthly performance policy to each record's own month,
    # including linked sources outside the current query month. Read-only only.
    loa_record_ids = {rid for (rid,) in db.query(DeductionRecord.id).filter(
        DeductionRecord.id.in_(all_rows),
        loa_month_exclusion(DeductionRecord.employee_id, DeductionRecord.deduction_month),
    ).all()} if all_rows else set()
    file_ids = {row.document_file_id for row in all_rows.values() if row.document_file_id}
    files = {row.id: row for row in db.query(StoredFile).filter(StoredFile.id.in_(file_ids)).all()} if file_ids else {}
    links = defaultdict(list)
    for work in upgrades:
        if work.status == "approved" and work.result_deduction_id:
            approved_seconds.add(work.second_deduction_id)
        status_name = upgrade_work_status_name(work, all_rows.get(work.second_deduction_id))
        members = [("source_first", work.first_deduction_id), ("source_second", work.second_deduction_id), ("result", work.result_deduction_id)]
        for role, rid in members:
            if rid in all_rows:
                links[rid].append({"request_id": work.id, "status": work.status, "status_name": status_name, "role": role,
                                   "records": [{"id": mid, "role": kind, "level_name": all_rows[mid].deduction_level_name}
                                               for kind, mid in members if mid in all_rows]})

    def present(row):
        business_status = STATUS_LABELS.get(row.status, row.status)
        material = files.get(row.document_file_id)
        document_url = f"/api/files/{row.document_file_id}" if material and material.status == "active" and row.material_status == "ready" and row.upgrade_role != "result" else ""
        level = levels.get(row.deduction_level_id)
        registered_points = float(row.points)
        loa_excluded = row.id in loa_record_ids
        actual_points = registered_points if deduction_counts_for_score(row) and not loa_excluded else 0
        if row.status == "void":
            score_status = "已作废 · 不计分"
        elif row.status != "active":
            score_status = business_status + " · 未扣分"
        elif row.upgrade_role == "source_second":
            score_status = "第二次声明 · 未扣分"
        elif row.upgrade_role == "source_first":
            score_status = "首次声明 · " + ("已扣分" if actual_points else "未扣分")
        elif row.upgrade_role == "result":
            score_status = "升级结果 · " + ("已扣分" if actual_points else "未扣分")
        else:
            score_status = "已扣分" if actual_points else "未扣分"
        if loa_excluded:
            score_status += " · LOA当月不计分"
        return {
            "id": row.id, "employee_id": row.employee_id, "employee_name": row.employee_name,
            "employee_no": row.employee_no, "attraction_id": row.attraction_id_snapshot,
            "attraction_name": attractions.get(row.attraction_id_snapshot, "未记录原景点"),
            "occurred_on": row.occurred_on, "type_id": row.deduction_type_id,
            "type_name": row.deduction_type_name, "level_id": row.deduction_level_id,
            "level_code": level.code if level else "OTHER", "level_name": row.deduction_level_name,
            "description": row.description, "submitter_name": row.submitter_name,
            "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
            "material_status": MATERIAL_LABELS.get(row.material_status, row.material_status),
            "upgrade_status": UPGRADE_LABELS.get(row.upgrade_state or "", row.upgrade_state or "无"),
            "business_status": business_status, "raw_status": row.status,
            "voided_at": row.voided_at.isoformat() if row.voided_at else None,
            "upgrade_request_id": row.upgrade_request_id or 0, "upgrade_role": row.upgrade_role, "document_url": document_url,
            "registered_points": registered_points, "actual_points": actual_points,
            "deduction_month": row.deduction_month, "loa_excluded": loa_excluded, "score_status": score_status,
            "void_reason": row.void_reason or "", "upgrade_links": links[row.id],
            "upgrade_slot_open": row.id in open_slots,
            "counted_in_statistics": row.id not in approved_seconds,
        }

    details = [present(row) for row in selected]
    # Keep existing statistics, exports and HR consumers unchanged. Display-only
    # source statements must never enter their event counts a second time.
    records = [row for row in details if row["counted_in_statistics"]]

    level_counts = Counter(row["level_id"] for row in records)
    circle_rows = defaultdict(list)
    for row in records:
        circle_rows[row["attraction_id"]].append(row)
    circles = []
    for circle_id, rows in sorted(circle_rows.items(), key=lambda item: (item[0] is None, attractions.get(item[0], ""))):
        circles.append({
            "attraction_id": circle_id, "attraction_name": attractions.get(circle_id, "未记录原景点"),
            "record_count": len(rows), "employee_count": len({row["employee_id"] for row in rows}),
            "levels": [{"id": item.id, "code": item.code, "name": item.name, "count": sum(row["level_id"] == item.id for row in rows)} for item in levels.values() if any(row["level_id"] == item.id for row in rows)],
            "statuses": {name: sum(row["business_status"] == name for row in rows) for name in dict.fromkeys(STATUS_LABELS.values())},
        })
    return {
        "month": month, "total_records": len(records),
        "employee_count": len({row["employee_id"] for row in records}),
        "levels": [{"id": item.id, "code": item.code, "name": item.name, "count": level_counts[item.id]} for item in sorted(levels.values(), key=lambda item: (item.points, item.id))],
        "statuses": {name: sum(row["business_status"] == name for row in records) for name in dict.fromkeys(STATUS_LABELS.values())},
        "attractions": [{"id": item.id, "name": item.name} for item in sorted(attraction_rows, key=lambda item: item.name)
                        if item.employee_circle or item.id in circle_rows],
        "types": [{"id": item.id, "name": item.name, "active": item.active} for item in sorted(types.values(), key=lambda item: item.name)],
        "circles": circles, "records": records,
        "detail_records": details,
        "related_records": [present(all_rows[rid]) for rid in sorted(related_ids) if rid in all_rows],
    }
