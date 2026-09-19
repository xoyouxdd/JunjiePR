"""HR administration: audit log, health, alerts and score rules."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from app.v2_auth import V2User, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, AuditLog, CircleTransferRequest, GovernanceCase, RecognitionRecord, RecognitionScoreRule, Role, StoredFile, SystemAlert
from app.v2_services import RECOGNIZER_CODES, process_role_expirations, write_audit
from app.routers._shared import backup_health_payload, client_ip, month_closure_payload, parse_iso_date, visible_system_alerts

router = APIRouter()


@router.get("/admin/operations-health")
def operations_health(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SYSTEM_ADMIN"))):
    health = backup_health_payload()
    month = date.today().strftime("%Y-%m")
    circles = db.query(Attraction).filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True)).order_by(Attraction.name).all()
    closures = [month_closure_payload(db, month, circle.id, user) for circle in circles]
    return {
        "backup": health,
        "month": month,
        "month_closures": closures,
        "open_system_alerts": db.query(SystemAlert).filter(SystemAlert.status == "open").count(),
        "pending_circle_transfers": db.query(CircleTransferRequest).filter(CircleTransferRequest.status == "pending").count(),
        "governance": {
            "open_cases": db.query(GovernanceCase).filter(GovernanceCase.status == "open").count(),
            "overdue_cases": db.query(GovernanceCase).filter(GovernanceCase.status == "open", GovernanceCase.due_at < datetime.now()).count(),
            "overdue_recognition_reviews": db.query(RecognitionRecord).filter(RecognitionRecord.status == "pending", RecognitionRecord.submitted_at < datetime.now() - timedelta(hours=48)).count(),
            "retention_review_files": db.query(StoredFile).filter(StoredFile.status == "active", StoredFile.uploaded_at < datetime.now() - timedelta(days=730)).count(),
        },
    }


@router.get("/hr/alerts")
def hr_alerts(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    process_role_expirations(db)
    rows = visible_system_alerts(db, user)
    return [
        {"id": row.id, "type": row.alert_type, "message": row.message, "due_date": row.due_date or "", "status": row.status, "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S")}
        for row in rows
    ]


@router.get("/hr/score-rules")
def hr_score_rules(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    result = []
    for role_code in RECOGNIZER_CODES:
        role = db.query(Role).filter(Role.code == role_code).one()
        rule = db.query(RecognitionScoreRule).filter_by(role_id=role.id, active=True).order_by(RecognitionScoreRule.effective_date.desc()).first()
        result.append({"role_id": role.id, "role_code": role.code, "role_name": role.name, "score": float(rule.score) if rule else 0, "effective_date": rule.effective_date if rule else ""})
    return sorted(result, key=lambda item: item["role_id"])


@router.post("/hr/score-rules")
def update_score_rule(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    if user.role.code != "SYSTEM_ADMIN":
        raise HTTPException(403, "认可角色分值是全局规则，仅最高管理员可调整")
    role = db.query(Role).filter(Role.code == str(payload.get("role_code") or "")).first()
    if not role or role.code not in RECOGNIZER_CODES:
        raise HTTPException(400, "认可人角色无效")
    try:
        score = Decimal(str(payload.get("score"))).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise HTTPException(400, "分值格式错误") from exc
    if not score.is_finite() or score < 0:
        raise HTTPException(400, "分值必须是大于或等于0的有效数字")
    effective_date = str(payload.get("effective_date") or date.today().isoformat())
    parse_iso_date(effective_date, "生效日期")
    rule = db.query(RecognitionScoreRule).filter_by(role_id=role.id, effective_date=effective_date).first()
    before = {"score": float(rule.score), "active": rule.active} if rule else None
    if rule:
        rule.score = score
        rule.active = True
    else:
        rule = RecognitionScoreRule(role_id=role.id, score=score, effective_date=effective_date, active=True)
        db.add(rule)
    write_audit(db, user.employee, "修改认可角色分值", "recognition_score_rule", role.id, before=before, after={"role": role.name, "score": float(score), "effective_date": effective_date}, ip_address=client_ip(request))
    db.commit()
    return {"ok": True}


@router.get("/admin/logs")
def admin_logs(limit: int = Query(300, ge=1, le=1000), db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SYSTEM_ADMIN"))):
    return [
        {"id": row.id, "time": row.created_at.strftime("%Y-%m-%d %H:%M:%S"), "operator": row.operator_name or "系统", "action": row.action, "entity": f"{row.entity_type}:{row.entity_id or ''}", "reason": row.reason or ""}
        for row in db.query(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).all()
    ]
