"""Per-version trainer grants, independent short-lived codes and signing evidence."""
from datetime import date, datetime, timedelta
import hashlib
import secrets

from fastapi import HTTPException
from app.v2_models import (AnnouncementTrainingConfig as Config, AnnouncementTrainer as Trainer,
    AnnouncementTrainingCode as Code, AnnouncementTrainingReceipt as Evidence,
    AnnouncementTrainingAttempt as Attempt, AnnouncementDelivery, Employee)


def candidates(db, scope="all", circle_id=None):
    from app import announcements as domain
    context, _ = domain.audience_context(db)
    return [{"id": id, "name": d["employee"].name, "employee_no": d["employee"].employee_no,
             "attraction_id": d["employee"].attraction_id}
            for id, d in context.items() if not d["loa"]
            and any(r.code == "TR" for r in domain.employee_roles(db, id))
            and d["employee"].attraction_id in domain.live_circles(db)
            and (scope == "all" or d["employee"].attraction_id == circle_id)]


def settings(db, version_id):
    config = db.get(Config, version_id)
    return {"training_required": bool(config), "trainer_scope": config.trainer_scope if config else "circle",
            "trainer_circle_id": config.circle_id if config else None,
            "trainer_ids": [t.employee_id for t in db.query(Trainer).filter_by(version_id=version_id, active=True)] if config else []}


def set_trainers(db, version, scope, circle_id, ids):
    from app import announcements as domain
    if not set(ids) <= {e["id"] for e in candidates(db, scope, circle_id)}:
        raise HTTPException(400, "请选择范围内在职、账号启用且非LOA的TR")
    config = db.get(Config, version.id)
    if not config:
        config = Config(version_id=version.id, trainer_scope=scope, circle_id=circle_id)
        db.add(config)
    config.trainer_scope, config.circle_id = scope, circle_id
    existing = {t.employee_id: t for t in db.query(Trainer).filter_by(version_id=version.id)}
    for id, trainer in existing.items():
        was_active = trainer.active
        trainer.active = id in ids
        if not trainer.active:
            end_codes(db, version.id, id)
        elif not was_active:
            domain.notify(db, id, version.announcement_id, f"training:{version.id}:{datetime.now().isoformat()}", "training", f"请跟进培训：《{version.title}》V{version.number}")
    for id in ids:
        if id not in existing:
            db.add(Trainer(version_id=version.id, employee_id=id, active=True))
        if id not in existing:
            domain.notify(db, id, version.announcement_id, f"training:{version.id}:{datetime.now().isoformat()}",
                          "training", f"请跟进培训：《{version.title}》V{version.number}")
    db.flush()


def authorized(db, version_id, trainer_id):
    config = db.get(Config, version_id)
    grant = db.query(Trainer).filter_by(version_id=version_id, employee_id=trainer_id, active=True).first()
    allowed = bool(config and grant and trainer_id in {e["id"] for e in candidates(db, config.trainer_scope, config.circle_id)})
    return allowed


def reconcile(db):
    from app.v2_models import Announcement, AnnouncementVersion
    now = datetime.now()
    for code in db.query(Code).filter(Code.ended_at.is_(None), Code.expires_at > now):
        version = db.get(AnnouncementVersion, code.version_id)
        announcement = db.get(Announcement, version.announcement_id)
        if not effective(announcement, version) or not authorized(db, version.id, code.trainer_id):
            code.ended_at = now


def effective(announcement, version):
    today = date.today().isoformat()
    return (announcement.status == "published" and version.number == announcement.current_version
            and version.effective_on <= today and (not version.expires_on or version.expires_on >= today))


def end_codes(db, version_id, trainer_id=None):
    query = db.query(Code).filter_by(version_id=version_id, ended_at=None)
    if trainer_id is not None:
        query = query.filter_by(trainer_id=trainer_id)
    query.update({"ended_at": datetime.now()}, synchronize_session="fetch")


def hash_code(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def generate(db, announcement, version, user, hours, request_key):
    if not effective(announcement, version) or not authorized(db, version.id, user.id):
        raise HTTPException(403, "仅当前有效公告的授权TR可生成培训码")
    # An uncertain write must not create a second session on retry. The code is
    # shown only once; the client can see its session and explicitly replace it.
    prior = db.query(Code).filter_by(request_key=request_key).first()
    if prior:
        if prior.version_id != version.id or prior.trainer_id != user.id:
            raise HTTPException(409, "提交标识已使用")
        return {"duplicate": True, "id": prior.id, "expires_at": prior.expires_at}
    now = datetime.now()
    end_codes(db, version.id, user.id)
    active_hashes = {r.code_hash for r in db.query(Code).filter(Code.ended_at.is_(None), Code.expires_at > now)}
    for _ in range(100):
        raw = f"{secrets.randbelow(1_000_000):06d}"
        if hash_code(raw) not in active_hashes:
            break
    else:
        raise HTTPException(503, "暂时无法生成培训码，请稍后重试")
    until = now + timedelta(hours=hours)
    if version.expires_on:
        until = min(until, datetime.combine(date.fromisoformat(version.expires_on)+timedelta(days=1), datetime.min.time()))
    code = Code(version_id=version.id, trainer_id=user.id, code_hash=hash_code(raw),
                request_key=request_key, created_at=now, expires_at=until)
    db.add(code)
    db.flush()
    return {"id": code.id, "code": raw, "expires_at": until, "duplicate": False}


def validate_code(db, announcement, version, delivery, raw):
    if not effective(announcement, version) or delivery.status != "pending":
        raise HTTPException(409, "当前公告暂不可签收，请刷新")
    now = datetime.now()
    attempt = db.query(Attempt).filter_by(version_id=version.id, employee_id=delivery.employee_id).first()
    if attempt and attempt.locked_until and attempt.locked_until > now:
        raise HTTPException(429, "输错次数过多，请10分钟后再试")
    if attempt and attempt.locked_until:
        attempt.failures, attempt.locked_until = 0, None
    code = db.query(Code).filter_by(version_id=version.id, code_hash=hash_code(raw), ended_at=None).filter(Code.expires_at > now).first()
    if not code or not authorized(db, version.id, code.trainer_id):
        if code:
            code.ended_at = now
        if not attempt:
            attempt = Attempt(version_id=version.id, employee_id=delivery.employee_id, failures=0)
            db.add(attempt)
        attempt.failures += 1
        if attempt.failures >= 5:
            attempt.locked_until = now + timedelta(minutes=10)
        db.commit()  # Persist throttling even when the HTTP request is rejected.
        raise HTTPException(429 if attempt.locked_until else 400, "培训码无效或已失效；连续输错5次需等待10分钟")
    if attempt:
        attempt.failures, attempt.locked_until = 0, None
    return code


def evidence(db, delivery, code):
    trainer = db.get(Employee, code.trainer_id)
    row = db.get(Evidence, delivery.id)
    if not row:
        row = Evidence(delivery_id=delivery.id)
        db.add(row)
    row.code_id, row.trainer_id = code.id, trainer.id
    row.trainer_name, row.trainer_no, row.verified_at = trainer.name, trainer.employee_no, datetime.now()


def receipt_fields(db, delivery_id):
    row = db.get(Evidence, delivery_id)
    return {"trainer_id": row.trainer_id if row else None, "trainer_name": row.trainer_name if row else "",
            "trainer_no": row.trainer_no if row else "", "training_session_id": row.code_id if row else None,
            "training_verified_at": row.verified_at if row else None}
