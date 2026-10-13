"""Explicit shared responsibility by category and exact circle/project scope."""
from datetime import datetime

from fastapi import HTTPException

from app.v2_models import (Announcement, AnnouncementVersion, AnnouncementResponsibility,
    AnnouncementResponsibilityHandover, AnnouncementHandover, Employee)


def scope_key(version):
    from app import announcements as domain
    return domain.packed({"category": version.category, "scope_kind": version.scope_kind,
        "attraction_ids": sorted(domain.unpack(version.attraction_ids_json)) if version.scope_kind == "circle" else [],
        "project_id": version.project_id if version.scope_kind == "project" else None})


def current_rows(db, key=None):
    rows = db.query(Announcement, AnnouncementVersion).join(AnnouncementVersion,
        (AnnouncementVersion.announcement_id == Announcement.id) & (AnnouncementVersion.number == Announcement.current_version)).all()
    return [(a, v) for a, v in rows if key is None or scope_key(v) == key]


def responsibility(db, announcement):
    from app import announcements as domain
    return db.query(AnnouncementResponsibility).filter_by(scope_key=scope_key(domain.current_version(db, announcement))).first()


def member_ids(db, announcement):
    from app import announcements as domain
    group = responsibility(db, announcement)
    return domain.unpack(group.member_ids_json) if group else [announcement.owner_id]


def manages(db, user, announcement):
    # Membership is explicit; circle management and broad AM/GSM publication
    # grants alone never confer a colleague's editing rights.
    return user.id in member_ids(db, announcement)


def people(db, ids):
    lookup = {e.id: {"id": e.id, "name": e.name, "employee_no": e.employee_no}
        for e in db.query(Employee).filter(Employee.id.in_(ids))}
    return [lookup[id] for id in ids if id in lookup]


def snapshot(rows):
    return sorted([{"id": a.id, "version": a.current_version} for a, _ in rows], key=lambda x: x["id"])


def validate_member(db, employee_id, rows):
    from app import announcements as domain
    for _, version in rows:
        domain.validate_scope(db, employee_id, domain.version_values(version))


def candidates(db, rows):
    result = []
    for employee in db.query(Employee).filter_by(is_active=True):
        try:
            validate_member(db, employee.id, rows)
        except HTTPException:
            continue
        result.append({"id": employee.id, "name": employee.name, "employee_no": employee.employee_no})
    return result


def describe(db, key, rows, user):
    from app import announcements as domain
    group = db.query(AnnouncementResponsibility).filter_by(scope_key=key).first()
    members = domain.unpack(group.member_ids_json) if group else sorted({a.owner_id for a, _ in rows})
    version = rows[0][1]
    label = domain.payload(db, user, rows[0][0], version)["scope_label"]
    return {"key": key, "id": group.id if group else None, "revision": group.revision if group else 0,
        "category": version.category, "scope_kind": version.scope_kind, "scope_label": label,
        "owners": people(db, members), "shared": group is not None, "count": len(rows),
        "announcements": [{"id": a.id, "title": v.title, "version": a.current_version, "status": a.status} for a, v in rows],
        "can_handover": domain.admin(user) or (user.id in members and (group is not None or all(a.owner_id == user.id for a, _ in current_rows(db, key)))),
        "pending": db.query(AnnouncementResponsibilityHandover).filter_by(responsibility_id=group.id, status="pending").count() if group else 0}


def center_groups(db, user, all_rows):
    from app import announcements as domain
    buckets = {}
    for announcement, version in all_rows:
        if domain.admin(user) or domain.can_view_handover(db, user, announcement):
            buckets.setdefault(scope_key(version), []).append((announcement, version))
    return [describe(db, key, rows, user) for key, rows in buckets.items()]


def ensure_group(db, key, rows, user):
    from app import announcements as domain
    group = db.query(AnnouncementResponsibility).filter_by(scope_key=key).first()
    if not group:
        if not domain.admin(user) and not all(a.owner_id == user.id for a, _ in rows):
            raise HTTPException(403, "该板块有其他负责人，请最高管理员先设置共同负责人")
        members = sorted({a.owner_id for a, _ in rows})
        if len(members) > 2:
            raise HTTPException(400, "请最高管理员先设置一至两位共同负责人")
        group = AnnouncementResponsibility(scope_key=key, member_ids_json=domain.packed(members),
            changed_by=user.id, reason="初始化整组交接")
        db.add(group)
        db.flush()
    return group


def cancel_pending(db, group):
    from app import announcements as domain
    for batch in db.query(AnnouncementResponsibilityHandover).filter_by(responsibility_id=group.id, status="pending"):
        batch.status = "cancelled"
        for history in db.query(AnnouncementHandover).filter(AnnouncementHandover.id.in_(domain.unpack(batch.handover_ids_json))):
            history.status = "cancelled"


def start(db, key, user, from_id, to_id, reason, force, expected_revision, expected_snapshot):
    from app import announcements as domain
    rows = current_rows(db, key)
    if not rows:
        raise HTTPException(404, "该板块没有公告")
    existing = db.query(AnnouncementResponsibility).filter_by(scope_key=key).first()
    if (existing.revision if existing else 0) != expected_revision:
        raise HTTPException(409, "负责人已变更，请刷新后交接")
    group = ensure_group(db, key, rows, user)
    if snapshot(rows) != expected_snapshot:
        raise HTTPException(409, "板块公告已变化，请刷新核对后交接")
    members = domain.unpack(group.member_ids_json)
    if force and not domain.admin(user):
        raise HTTPException(403, "仅最高管理员可兜底交接")
    if not force and from_id != user.id:
        raise HTTPException(403, "只能交接自己的职责")
    if from_id not in members or to_id in members or not reason.strip():
        raise HTTPException(400, "请选择离任负责人、新继任者并填写交接原因")
    validate_member(db, to_id, rows)
    if not force and any(b.from_id != from_id for b in db.query(AnnouncementResponsibilityHandover).filter_by(responsibility_id=group.id, status="pending")):
        raise HTTPException(409, "本组另一位负责人有待接受交接，请先处理")
    cancel_pending(db, group)
    for old in db.query(AnnouncementHandover).filter(AnnouncementHandover.announcement_id.in_([a.id for a, _ in rows]), AnnouncementHandover.status == "pending"):
        old.status = "cancelled"
    histories = []
    for announcement, _ in rows:
        history = AnnouncementHandover(announcement_id=announcement.id, from_id=from_id, to_id=to_id,
            requested_by=user.id, reason=reason.strip(), status="pending")
        db.add(history)
        histories.append(history)
    db.flush()
    batch = AnnouncementResponsibilityHandover(responsibility_id=group.id, expected_revision=group.revision,
        from_id=from_id, to_id=to_id, snapshot_json=domain.packed(snapshot(rows)),
        handover_ids_json=domain.packed([h.id for h in histories]), status="pending")
    db.add(batch)
    db.flush()
    if force:
        complete(db, batch, forced=True, actor_id=user.id)
    for employee_id in {from_id, to_id}:
        domain.notify(db, employee_id, rows[0][0].id, f"group-handover:{batch.id}", "handover",
            f"{rows[0][1].category} · {len(rows)}份公告" + ("已整组交接" if force else "整组交接待接受"))
    return batch, histories


def complete(db, batch, forced=False, actor_id=None):
    from app import announcements as domain
    group = db.get(AnnouncementResponsibility, batch.responsibility_id)
    rows = current_rows(db, group.scope_key)
    members = domain.unpack(group.member_ids_json)
    if batch.status != "pending":
        raise HTTPException(409, "交接已处理")
    if group.revision != batch.expected_revision or batch.from_id not in members or batch.to_id in members or snapshot(rows) != domain.unpack(batch.snapshot_json):
        raise HTTPException(409, "负责人或板块公告已变化，请重新核对交接")
    validate_member(db, batch.to_id, rows)
    group.member_ids_json = domain.packed([batch.to_id if id == batch.from_id else id for id in members])
    group.revision += 1
    group.changed_by, group.changed_at = actor_id or batch.to_id, datetime.now()
    group.reason = db.get(AnnouncementHandover, domain.unpack(batch.handover_ids_json)[0]).reason
    batch.status, batch.completed_at = ("forced" if forced else "accepted"), datetime.now()
    for history in db.query(AnnouncementHandover).filter(AnnouncementHandover.id.in_(domain.unpack(batch.handover_ids_json))):
        history.status, history.completed_at = batch.status, batch.completed_at
    for announcement, _ in rows:
        if announcement.owner_id == batch.from_id:
            announcement.owner_id = batch.to_id
    domain.notify(db, batch.from_id, rows[0][0].id, f"group-accepted:{batch.id}", "handover", f"{rows[0][1].category}整组交接已完成")
    return group, rows


def batch_for_history(db, history_id):
    from app import announcements as domain
    return next((b for b in db.query(AnnouncementResponsibilityHandover)
        if history_id in domain.unpack(b.handover_ids_json)), None)
