"""Recognition-point reconciliation. Caller owns transaction and serialization."""
from collections import Counter
from hashlib import sha256
import json

from app.activity_points_models import ActivityPointsEntry, RecognitionPointsState
from app.database.reference_data import EMPLOYEE_CIRCLES, LEGACY_CIRCLE_BY_VENUE
from app.recognition_policy import SPECIAL_RECOGNITION_TYPES
from app.services.identity import base_role_at, role_at, TEMPORARY_RECOGNIZER_LABEL
from app.v2_models import Attraction, RecognitionRecord, RecognitionType

LEVELS = {"CM": 0, "TR": 0, "TA_SUPERVISOR": 1, "SUPERVISOR": 1,
          "TA_GSM": 2, "GSM": 2, "AM": 3, "OM": 4}

START_POLICY_VERSION = "circle-starts-2026-09-10-11-v1"
START_POLICY = {"热力追踪": {"frontline": "2026-09-01", "management": "2026-10-01"},
                "矮人迷宫": {"frontline": "2026-11-01", "management": "2026-11-01"},
                "小熊罐子": {"frontline": "2026-11-01", "management": "2026-11-01"}}
START_REASONS = {"before_start_date", "missing_circle_history"}


def recognition_circle(db, row):
    """Use the recipient's frozen home circle, never today's circle or event venue."""
    name = row.home_attraction_name
    if not name and row.home_attraction_id:
        attraction = db.get(Attraction, row.home_attraction_id)
        name = attraction.name if attraction else None
    name = LEGACY_CIRCLE_BY_VENUE.get(name, name)
    return name if name in EMPLOYEE_CIRCLES else None


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def source_key(row):
    return digest([row.employee_id, row.recognizer_employee_id, row.recognition_date,
        row.employee_role_code_snapshot, row.recognizer_role_code_snapshot,
        row.recognizer_base_role_code_snapshot, row.recognizer_role_snapshot,
        row.recognition_type_id, row.scoring_policy_json])


def entitlement(db, row):
    target = row.employee_role_code_snapshot
    if not target or target in {"TA_SUPERVISOR", "TA_GSM"}:
        role = base_role_at(db, row.employee_id, row.recognition_date)
        target = role.code if role else None
    signer = row.recognizer_role_code_snapshot
    if not signer:
        role = role_at(db, row.recognizer_employee_id, row.recognition_date)
        signer = role.code if role else None
    base = row.recognizer_base_role_code_snapshot
    if not base:
        role = base_role_at(db, row.recognizer_employee_id, row.recognition_date)
        base = role.code if role else None
    reason = "eligible"
    category = db.get(RecognitionType, row.recognition_type_id)
    policy = json.loads(row.scoring_policy_json or "{}")
    if row.employee_id == row.recognizer_employee_id:
        reason = "self_recognition"
    elif any(code and code.startswith("HR") for code in (target, signer, base)):
        reason = "hr_excluded"
    elif row.recognizer_role_snapshot == "系统固定分值" or policy.get("mode") == "fixed" or (
        category and category.code in SPECIAL_RECOGNITION_TYPES and policy.get("mode") != "role"
    ):
        reason = "no_personal_signer"
    elif not target or not signer:
        reason = "missing_history"
    elif row.recognizer_role_snapshot == TEMPORARY_RECOGNIZER_LABEL:
        # The frozen label proves this recognition passed PR's dated qualification check.
        signer = "SUPERVISOR"
    if reason == "eligible" and (target not in LEVELS or signer not in LEVELS):
        reason = "ineligible_role"
    points = min(3, max(0, LEVELS[signer] - LEVELS[target])) if reason == "eligible" else 0
    if reason == "eligible" and not points:
        reason = "no_level_difference"
    return points, signer, target, reason


def plan(db, row, state=None):
    key = source_key(row)
    if state and state.source_key == key and state.reason != "missing_history":
        points, signer, target, reason = state.entitlement, state.signer_code, state.recipient_code, state.reason
        # Reapply date policy without thawing the frozen historical identities.
        if reason in START_REASONS:
            reason = "eligible" if points else "no_level_difference"
    else:
        points, signer, target, reason = entitlement(db, row)
    if reason == "eligible":
        circle = recognition_circle(db, row)
        start = START_POLICY[circle]["frontline" if target in {"CM", "TR"} else "management"] if circle else None
        if not start:
            reason = "missing_circle_history"
        elif row.recognition_date < start:
            reason = "before_start_date"
    return {"source_key": key, "entitlement": points, "signer_code": signer,
        "recipient_code": target, "reason": reason,
        "applied_points": points if row.status == "confirmed" and reason == "eligible" else 0}


def sync_recognition(db, row, *, origin="live"):
    db.flush()
    state = db.get(RecognitionPointsState, row.id)
    desired = plan(db, row, state)
    old_points = state.applied_points if state else 0
    old_employee = state.employee_id if state else row.employee_id
    revision = (state.revision if state else 0) + 1
    deltas = Counter({old_employee: -old_points})
    deltas[row.employee_id] += desired["applied_points"]
    detail = {"signer_name": row.recognizer_name, "signer_code": desired["signer_code"],
        "recipient_code": desired["recipient_code"], "category": row.recognition_type_name,
        "recognition_date": row.recognition_date, "status": row.status, "reason": desired["reason"],
        "home_circle": recognition_circle(db, row), "start_policy": START_POLICY_VERSION}
    for employee_id, delta in deltas.items():
        if delta:
            db.add(ActivityPointsEntry(employee_id=employee_id, recognition_id=row.id,
                revision=revision, delta=delta, kind="recognition", origin=origin,
                business_date=row.recognition_date, detail_json=json.dumps(detail, ensure_ascii=False)))
    if not state:
        state = RecognitionPointsState(recognition_id=row.id)
        db.add(state)
    changed = any(getattr(state, key, None) != value for key, value in desired.items()) or state.employee_id != row.employee_id
    if changed:
        state.employee_id = row.employee_id
        state.revision = revision
        for key, value in desired.items():
            setattr(state, key, value)
    db.flush()
    return sum(deltas.values())


def backfill_preview(db):
    states = {s.recognition_id: s for s in db.query(RecognitionPointsState)}
    people, reasons, pending, basis = Counter(), Counter(), [], []
    changed = 0
    for row in db.query(RecognitionRecord).order_by(RecognitionRecord.id):
        state = states.get(row.id)
        desired = plan(db, row, state)
        old = state.applied_points if state else 0
        delta = desired["applied_points"] - old
        people[row.employee_id] += desired["applied_points"]
        reasons[desired["reason"]] += 1
        if desired["reason"] in {"missing_history", "missing_circle_history"} and row.status == "confirmed":
            pending.append({"recognition_id": row.id, "employee_no": row.employee_no, "date": row.recognition_date,
                            "reason": desired["reason"]})
        changed += bool(delta or (state and state.employee_id != row.employee_id))
        basis.append([START_POLICY_VERSION, row.id, row.employee_id, row.status, desired,
                      recognition_circle(db, row), row.home_attraction_id, row.home_attraction_name, state.revision if state else None,
                      state.employee_id if state else None, old])
    return {"fingerprint": digest(basis), "recognitions": len(basis), "changed_recognitions": changed,
        "employees": sum(v > 0 for v in people.values()), "total_points": sum(people.values()),
        "reasons": dict(reasons), "pending_review": pending,
        "start_policy_version": START_POLICY_VERSION, "start_policy": START_POLICY}


def backfill_apply(db, fingerprint):
    preview = backfill_preview(db)
    if fingerprint != preview["fingerprint"]:
        from fastapi import HTTPException
        raise HTTPException(409, "认可或积点已变化，请重新预览历史补计")
    delta = 0
    for row in db.query(RecognitionRecord).order_by(RecognitionRecord.id):
        delta += sync_recognition(db, row, origin="historical_backfill")
    return {**preview, "added_points": delta}


def personal_card(db, employee_id, page=1, page_size=20):
    from app.services.activity_queries import pending_redemption
    from app.services.activities import balances
    snapshot = balances(db, employee_id)
    query = db.query(ActivityPointsEntry).filter_by(employee_id=employee_id)
    earned, balance = snapshot['earned'], snapshot['balance']
    total = query.count()
    entries = query.order_by(ActivityPointsEntry.id.desc()).offset((page-1)*page_size).limit(page_size).all()
    frozen = snapshot['frozen']
    return {"earned": earned, "used": earned-balance, "available": balance-frozen, "frozen": frozen,
        "expires": False, "redemption_enabled": True, "pending_redemption": pending_redemption(db, employee_id),
        "total": total, "page": page,
        "page_size": page_size, "items": [{"id": e.id, "recognition_id": e.recognition_id,
            "delta": e.delta, "origin": e.origin, "business_date": e.business_date,
            "created_at": e.created_at.isoformat(), **json.loads(e.detail_json)} for e in entries]}
