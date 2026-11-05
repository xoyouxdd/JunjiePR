"""Signer-only recognition rankings shared by the two issuing boards."""
from collections import defaultdict
import json
from math import ceil

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, load_only

from app.database.reference_data import EMPLOYEE_CIRCLES, LEGACY_CIRCLE_BY_VENUE
from app.recognition_policy import effective_recognition_credit, SPECIAL_RECOGNITION_TYPES
from app.employee_numbers import employee_number
from app.role_constants import FRONTLINE_CODES, GSM_CODES, LEADER_CODES
from app.services.identity import base_roles_at, roles_at, TEMPORARY_RECOGNIZER_LABEL
from app.v2_models import Attraction, Employee, RecognitionRecord, RecognitionType, Role


def issuer_ranking_payload(db: Session, user, start_value: str, end_value: str,
                           category: str, subtype_id: int | None, keyword: str,
                           page: int, page_size: int, attraction: Attraction | None,
                           population: str) -> dict:
    subtype = db.get(RecognitionType, subtype_id) if subtype_id else None
    if subtype_id and (not subtype or not subtype.active):
        raise HTTPException(400, "请选择有效认可类型")
    codes = GSM_CODES if category == "gsm_leader" else LEADER_CODES
    scoped_hr = user.role.code == "HR_CIRCLE"
    query = (db.query(RecognitionRecord)
             .join(Employee, Employee.id == RecognitionRecord.recognizer_employee_id)
             .join(RecognitionType, RecognitionType.id == RecognitionRecord.recognition_type_id)
             .filter(RecognitionRecord.status == "confirmed",
                     RecognitionRecord.recognition_date >= start_value,
                     RecognitionRecord.recognition_date <= end_value,
                     RecognitionRecord.recognizer_employee_id != RecognitionRecord.employee_id,
                     RecognitionRecord.recognizer_role_snapshot.notin_(
                         [TEMPORARY_RECOGNIZER_LABEL, "系统固定分值"]),
                     or_(RecognitionRecord.recognizer_role_code_snapshot.in_(codes),
                         RecognitionRecord.recognizer_role_code_snapshot.is_(None))))
    if attraction:
        # HR must never see records for recipients outside its authorized circle.
        query = query.filter(RecognitionRecord.home_attraction_id == attraction.id
                             if scoped_hr else Employee.attraction_id == attraction.id)
    if subtype:
        query = query.filter(RecognitionRecord.recognition_type_id == subtype.id)
    value = employee_number(keyword).lower()
    if value:
        # SQLite LOWER is ASCII-only. Match names with the same Unicode rules
        # as the other boards, then restrict the record query to those signers.
        signer_ids = [p.id for p in db.query(Employee.id, Employee.name, Employee.employee_no)
                      if value in p.name.lower() or value in p.employee_no.lower()]
        query = query.filter(Employee.id.in_(signer_ids))
    records = query.options(load_only(
        RecognitionRecord.employee_id, RecognitionRecord.employee_role_code_snapshot,
        RecognitionRecord.recognizer_employee_id, RecognitionRecord.recognizer_role_code_snapshot,
        RecognitionRecord.recognition_date, RecognitionRecord.occurred_attraction_id,
        RecognitionRecord.recognition_type_id,
        RecognitionRecord.fraction, RecognitionRecord.credited_fraction,
        RecognitionRecord.scoring_policy_json,
    )).order_by(RecognitionRecord.recognition_date, RecognitionRecord.id).all()
    legacy_fixed_ids = {r.id for r in db.query(RecognitionType.id).filter(
        RecognitionType.code.in_(SPECIAL_RECOGNITION_TYPES)).all()}
    signed_records = []
    for row in records:
        policy = json.loads(row.scoring_policy_json or '{}')
        # Use frozen policy, never today's category configuration. MSP/letters
        # may now be configured for real role-based signers; older unsnapshotted
        # rows of these types were fixed awards and have no personal signer.
        if policy.get('mode') == 'fixed':
            continue
        if row.recognition_type_id in legacy_fixed_ids and policy.get('mode') != 'role':
            continue
        signed_records.append(row)
    records = signed_records

    # Legacy snapshots are resolved at the event date in batches, not by today's role.
    missing_signers, missing_targets = defaultdict(set), defaultdict(set)
    allowed = FRONTLINE_CODES | {"SUPERVISOR"} if population == "all" else (
        {"SUPERVISOR"} if population == "supervisor" else FRONTLINE_CODES)
    for row in records:
        if row.recognizer_role_code_snapshot is None:
            missing_signers[row.recognition_date].add(row.recognizer_employee_id)
        if row.employee_role_code_snapshot not in FRONTLINE_CODES | {"SUPERVISOR"}:
            missing_targets[row.recognition_date].add(row.employee_id)
    historical_signers = {day: roles_at(db, ids, day) for day, ids in missing_signers.items()}
    historical_targets = {day: base_roles_at(db, ids, day) for day, ids in missing_targets.items()}
    employees = {p.id: p for p in db.query(Employee).filter(
        Employee.id.in_({r.recognizer_employee_id for r in records})).all()} if records else {}
    role_names = {r.code: r.name for r in db.query(Role).filter(Role.code.in_(codes)).all()}
    attractions = {a.id: a for a in db.query(Attraction).all()}
    circle_by_name = {a.name: a for a in attractions.values() if a.employee_circle}
    venue_columns = [{"key": str(circle_by_name[name].id), "name": name}
                     for name in EMPLOYEE_CIRCLES if name in circle_by_name]
    valid_keys = {c["key"] for c in venue_columns}
    aggregates = {}

    def empty(employee, code):
        return {"employee_id": employee.id, "employee_no": employee.employee_no,
                "employee_name": employee.name, "role_name": role_names.get(code, code),
                "group_label": "", "count": 0, "score": 0, "recent_date": "",
                "venue_counts": {c["key"]: 0 for c in venue_columns}}

    # Keep the existing supervisor roster, including those with no issued cards.
    if category == "leader":
        roster = db.query(Employee).filter(Employee.is_active.is_(True),
                                         Employee.attraction_id.in_([a.id for a in circle_by_name.values()]))
        if attraction:
            roster = roster.filter(Employee.attraction_id == attraction.id)
        people = roster.all()
        end_roles = roles_at(db, [p.id for p in people], end_value)
        for person in people:
            role = end_roles.get(person.id)
            if role and role.code in codes and (not value or value in person.name.lower() or value in person.employee_no.lower()):
                aggregates[person.id] = empty(person, role.code)

    for row in records:
        role = historical_signers.get(row.recognition_date, {}).get(row.recognizer_employee_id)
        code = row.recognizer_role_code_snapshot or (role.code if role else None)
        if code not in codes:
            continue
        target_role = historical_targets.get(row.recognition_date, {}).get(row.employee_id)
        target_code = row.employee_role_code_snapshot
        if target_code not in FRONTLINE_CODES | {"SUPERVISOR"}:
            target_code = target_role.code if target_role else None
        if target_code not in allowed:
            continue
        person = employees[row.recognizer_employee_id]
        if value and value not in person.name.lower() and value not in person.employee_no.lower():
            continue
        item = aggregates.setdefault(person.id, empty(person, code))
        item["count"] += 1
        item["score"] += effective_recognition_credit(row)
        item["recent_date"] = row.recognition_date
        item["role_name"] = role_names.get(code, code)
        venue = attractions.get(row.occurred_attraction_id)
        circle = circle_by_name.get(LEGACY_CIRCLE_BY_VENUE.get(venue.name, venue.name)) if venue else None
        key = str(circle.id) if circle and str(circle.id) in valid_keys else "other"
        item["venue_counts"][key] = item["venue_counts"].get(key, 0) + 1
    if any("other" in r["venue_counts"] for r in aggregates.values()):
        venue_columns.append({"key": "other", "name": "其他/未注明"})
    rows = list(aggregates.values())
    for row in rows:
        row["score"] = round(float(row["score"]), 2)
        for column in venue_columns:
            row["venue_counts"].setdefault(column["key"], 0)
    rows.sort(key=lambda r: (-r["count"], -r["score"], r["employee_no"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    offset = (page - 1) * page_size
    return {"category": category, "population": population,
            "population_name": {"all": "全部人群", "supervisor": "LEAD", "frontline": "CM/TR"}[population],
            "circle_scope": "recipient" if scoped_hr else "issuer",
            "venue_columns": venue_columns, "subtype_id": subtype_id,
            "subtype_name": subtype.name if subtype else "全部加分类别", "sort_by": "count",
            "start_date": start_value, "end_date": end_value,
            "attraction_id": attraction.id if attraction else None,
            "attraction_name": attraction.name if attraction else "全部景点圈",
            "total": len(rows), "page": page, "page_size": page_size,
            "pages": max(1, ceil(len(rows) / page_size)), "rows": rows[offset:offset + page_size]}
