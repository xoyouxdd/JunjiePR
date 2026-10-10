"""Versioned operational announcements; receipt evidence is never overwritten."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import difflib
import hashlib
import json

from fastapi import Depends, HTTPException
from sqlalchemy import or_, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.v2_auth import V2User, current_user
from app.v2_database import get_db
from app.v2_models import (Announcement, AnnouncementAsset, AnnouncementDelivery,
    AnnouncementFavorite, AnnouncementGrant, AnnouncementHandover, AnnouncementMedia,
    AnnouncementNotification, AnnouncementProject, AnnouncementProjectMember,
    AnnouncementVersion, Attraction, Employee, EmployeeLOAPeriod, GroupMembership, StoredFile, UserAccount, WorkGroup)
from app.v2_services import base_roles_at, direct_member_ids, duties_at_bulk, role_at

CATEGORIES = ["安全 & 合规", "演出 & 5S", "排班 & 效率", "礼仪 & MM", "HR & 活动"]
TIERS = {"frontline": "CM / TR", "lead": "TA LEAD / LEAD", "gsm": "TA GSM / GSM", "am": "AM", "hr": "HR"}
ROLE_TIERS = {"CM": "frontline", "TR": "frontline", "TA_SUPERVISOR": "lead", "SUPERVISOR": "lead",
              "TA_GSM": "gsm", "GSM": "gsm", "AM": "am", "HR_ADMIN": "hr", "HR_CIRCLE": "hr", "SYSTEM_ADMIN": "hr"}


def unpack(value):
    return json.loads(value or "[]")


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def admin(user):
    return user.has_role("SYSTEM_ADMIN") or "SYSTEM_ADMIN" in user.permissions


def lock(db):
    # SQLite serializes revision, confirmation and handover transitions.
    db.execute(text("BEGIN IMMEDIATE"))


def live_circles(db):
    return {x.id: x.name for x in db.query(Attraction).filter_by(active=True, employee_circle=True)}


def employee_roles(db, employee_id):
    base = base_roles_at(db, [employee_id]).get(employee_id)
    return ([base] if base else []) + duties_at_bulk(db, [employee_id]).get(employee_id, [])


def grant_config(db, employee_id):
    employee = db.get(Employee, employee_id)
    account = db.query(UserAccount).filter_by(employee_id=employee_id, enabled=True).first()
    today = date.today().isoformat()
    if not employee or not employee.is_active or not account or employee.hired_on and employee.hired_on > today or employee.terminated_on and employee.terminated_on <= today:
        return {"circles": [], "tiers": [], "categories": [], "projects": []}
    codes = {r.code for r in employee_roles(db, employee_id)}
    circles = live_circles(db)
    rows = db.query(AnnouncementGrant).filter_by(employee_id=employee_id).all()
    circle = next((r for r in rows if r.scope_kind == "circle"), None)
    def active(r):
        return r.enabled and r.starts_on <= today and (not r.ends_on or r.ends_on >= today)
    result = {"circles": [], "tiers": [], "categories": [], "projects": []}
    if codes & {"GSM", "TA_GSM", "AM"}:
        if circle:
            if active(circle):
                result.update(circles=sorted(set(unpack(circle.attraction_ids_json)) & circles.keys()),
                              tiers=unpack(circle.tiers_json), categories=unpack(circle.categories_json))
        else:
            default = list(circles) if codes & {"TA_GSM", "AM"} else ([employee.attraction_id] if employee.attraction_id in circles else [])
            result.update(circles=default, tiers=list(TIERS), categories=CATEGORIES)
    projects = {p.id: p for p in db.query(AnnouncementProject).filter_by(active=True)}
    for row in rows:
        if row.scope_kind == "project" and row.project_id in projects and active(row):
            result["projects"].append({"id": row.project_id, "name": projects[row.project_id].name,
                                       "tiers": unpack(row.tiers_json), "categories": unpack(row.categories_json)})
    return result


def require_publisher(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    config = grant_config(db, user.id)
    if not config["circles"] and not config["projects"]:
        raise HTTPException(403, "未获公告发布授权")
    return user


def validate_scope(db, employee_id, values):
    config = grant_config(db, employee_id)
    if values["scope_kind"] == "circle":
        selected = set(values["attraction_ids"])
        if not selected or values.get("project_id") is not None:
            raise HTTPException(400, "请选择景点圈，圈公告不能同时选择项目")
        if not selected <= set(config["circles"]):
            raise HTTPException(403, "发布范围超出授权")
        allowed_tiers, allowed_categories = config["tiers"], config["categories"]
    else:
        if values["attraction_ids"]:
            raise HTTPException(400, "项目公告不能同时选择景点圈")
        project = next((p for p in config["projects"] if p["id"] == values.get("project_id")), None)
        if not project:
            raise HTTPException(403, "未获该项目的公告授权")
        allowed_tiers, allowed_categories = project["tiers"], project["categories"]
    if not set(values["tiers"]) <= set(allowed_tiers) or values["category"] not in allowed_categories:
        raise HTTPException(403, "板块或接收层级超出授权")


def version_values(version):
    return {"scope_kind": version.scope_kind, "project_id": version.project_id,
            "attraction_ids": unpack(version.attraction_ids_json), "tiers": unpack(version.tiers_json),
            "category": version.category}


def current_version(db, announcement):
    return db.query(AnnouncementVersion).filter_by(announcement_id=announcement.id, number=announcement.current_version).one()


def notify(db, employee_id, announcement_id, event_key, kind, message):
    db.execute(insert(AnnouncementNotification).values(employee_id=employee_id, announcement_id=announcement_id,
        event_key=event_key, kind=kind, message=message, created_at=datetime.now()).on_conflict_do_nothing())


def audience_context(db):
    today = date.today().isoformat()
    employees = db.query(Employee).join(UserAccount, UserAccount.employee_id == Employee.id).filter(
        Employee.is_active.is_(True), UserAccount.enabled.is_(True),
        or_(Employee.hired_on.is_(None), Employee.hired_on <= today),
        or_(Employee.terminated_on.is_(None), Employee.terminated_on > today)).all()
    ids = [e.id for e in employees]
    bases, duties = base_roles_at(db, ids), duties_at_bulk(db, ids)
    loa = {r.employee_id for r in db.query(EmployeeLOAPeriod).filter(
        EmployeeLOAPeriod.status == "active", EmployeeLOAPeriod.starts_on <= today,
        or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= today))}
    projects = {p.id for p in db.query(AnnouncementProject).filter_by(active=True)}
    memberships = {}
    for member in db.query(AnnouncementProjectMember).filter_by(active=True):
        if member.project_id in projects:
            memberships.setdefault(member.project_id, set()).add(member.employee_id)
    context = {}
    for employee in employees:
        roles = ([bases[employee.id]] if bases.get(employee.id) else []) + duties.get(employee.id, [])
        context[employee.id] = {"employee": employee, "tiers": {ROLE_TIERS[r.code] for r in roles if r.code in ROLE_TIERS},
                                "role_label": " / ".join(r.name for r in roles), "loa": employee.id in loa}
    return context, memberships


def intended(context, memberships, version):
    circles, tiers = set(unpack(version.attraction_ids_json)), set(unpack(version.tiers_json))
    return {id for id, data in context.items() if data["tiers"] & tiers and (
        data["employee"].attraction_id in circles if version.scope_kind == "circle" else id in memberships.get(version.project_id, set()))}


def sync_current(db, initial_id=None):
    """Idempotent lazy reconciliation, also invoked by the existing scheduler."""
    if not db.connection().connection.driver_connection.in_transaction:
        lock(db)
    today = date.today().isoformat()
    context, memberships = audience_context(db)
    def grace(version):
        until = (date.today()+timedelta(days=3)).isoformat()
        return min(until, version.expires_on) if version.expires_on else until
    for announcement in db.query(Announcement).filter_by(status="published"):
        version = current_version(db, announcement)
        deliveries = db.query(AnnouncementDelivery).filter_by(version_id=version.id).all()
        if version.expires_on and version.expires_on < today:
            for delivery in deliveries:
                if not delivery.confirmed_at:
                    delivery.status = "expired"
            continue
        if version.effective_on > today:
            continue
        ids = intended(context, memberships, version)
        existing = {d.employee_id: d for d in deliveries}
        for id in ids:
            data = context[id]
            if id not in existing:
                employee = data["employee"]
                late = announcement.id != initial_id
                previous = version.number > 1 and db.query(AnnouncementDelivery.id).join(
                    AnnouncementVersion, AnnouncementVersion.id == AnnouncementDelivery.version_id).filter(
                    AnnouncementVersion.announcement_id == announcement.id,
                    AnnouncementVersion.number < version.number, AnnouncementDelivery.employee_id == id).first()
                reason = "版本更新" if previous else "适用公告补收" if late else "首次接收"
                due = version.due_on
                if late and due and due < today:
                    due = grace(version)
                db.execute(insert(AnnouncementDelivery).values(version_id=version.id, employee_id=id,
                    employee_no=employee.employee_no, employee_name=employee.name, attraction_id=employee.attraction_id,
                    role_label=data["role_label"], status="suspended" if data["loa"] else "pending",
                    reason="LOA暂缓" if data["loa"] else reason, due_on=due, delivered_at=datetime.now()).on_conflict_do_nothing())
                notify(db, id, announcement.id, f"updated:{version.id}" if previous else f"delivery:{version.id}",
                       "updated" if previous else "supplement" if late else "published",
                       f"{version.title} · V{version.number}" + (" · 请确认更新" if previous else " · 补收" if late else ""))
            else:
                delivery = existing[id]
                previous_status = delivery.status
                delivery.status = "suspended" if data["loa"] else ("completed" if delivery.confirmed_at else "pending")
                if not data["loa"] and delivery.reason == "LOA暂缓":
                    delivery.reason = "返岗补收"
                    if delivery.due_on and delivery.due_on < today:
                        delivery.due_on = grace(version)
                    if not delivery.confirmed_at:
                        notify(db, id, announcement.id, f"return:{version.id}:{today}", "supplement",
                               f"返岗补收：{version.title} · V{version.number}")
                elif not data["loa"] and previous_status == "exited" and not delivery.confirmed_at:
                    delivery.reason = "重新适用补收"
                    if delivery.due_on and delivery.due_on < today:
                        delivery.due_on = grace(version)
                    notify(db, id, announcement.id, f"rejoin:{version.id}:{today}", "supplement",
                           f"适用公告补收：{version.title} · V{version.number}")
                if data["loa"] and not delivery.confirmed_at:
                    delivery.reason = "LOA暂缓"
        for id, delivery in existing.items():
            if id not in ids:
                delivery.status = "exited"
    db.flush()


def record_ids(db, user, announcement, version):
    rows = db.query(AnnouncementDelivery).filter_by(version_id=version.id)
    if admin(user) or user.id in {announcement.owner_id, announcement.publisher_id}:
        return None
    if user.has_role("GSM", "TA_GSM", "AM"):
        config = grant_config(db, user.id)
        if version.scope_kind == "project" and version.project_id in {p["id"] for p in config["projects"]}:
            return None
        # Suspending publication does not remove ordinary management access.
        managed = set(live_circles(db)) if user.has_role("TA_GSM", "AM") else ({user.employee.attraction_id} | set(config["circles"]))
        ids = {d.employee_id for d in rows if d.attraction_id in managed} if version.scope_kind == "circle" else set()
        if ids:
            return ids
    if user.has_role("SUPERVISOR", "TA_SUPERVISOR"):
        ids = direct_member_ids(db, user.id, include_overseen=True)
        return ids & {d.employee_id for d in rows}
    return set()


def can_read_version(db, user, announcement, version):
    ids = record_ids(db, user, announcement, version)
    return admin(user) or user.id in {announcement.owner_id, announcement.publisher_id} or bool(
        db.query(AnnouncementDelivery.id).filter_by(version_id=version.id, employee_id=user.id).first()) or ids is None or bool(ids) or bool(
        db.query(AnnouncementHandover.id).filter_by(announcement_id=announcement.id, to_id=user.id, status="pending").first())


def attachment_allowed(db, user, file_id):
    for version in db.query(AnnouncementVersion):
        if file_id in {a["file_id"] for a in unpack(version.attachments_json)}:
            announcement = db.get(Announcement, version.announcement_id)
            if can_read_version(db, user, announcement, version):
                return True
    return False


def signature_allowed(db, user, delivery):
    version = db.get(AnnouncementVersion, delivery.version_id)
    announcement = db.get(Announcement, version.announcement_id)
    ids = record_ids(db, user, announcement, version)
    return delivery.employee_id == user.id or ids is None or delivery.employee_id in ids


def validate_attachments(db, user, attachments, previous=None):
    previous_ids = {a["file_id"] for a in unpack(previous.attachments_json)} if previous else set()
    for item in attachments:
        file = db.get(StoredFile, item["file_id"])
        media = db.query(AnnouncementMedia).filter_by(file_id=item["file_id"], owner_id=user.id).first()
        asset = db.query(AnnouncementAsset).filter_by(file_id=item["file_id"], owner_id=user.id).first()
        if not file or file.status != "active" or not (media or asset or file.id in previous_ids):
            raise HTTPException(400, "只能使用本人素材或该公告已有附件")
        if item["kind"] in {"cover", "image"} and file.extension.lower() not in {".png", ".jpg", ".webp"}:
            raise HTTPException(400, "封面和正文配图必须是图片")


def delivery_payload(db, row):
    circles = live_circles(db)
    return {"id": row.id, "employee_id": row.employee_id, "employee_no": row.employee_no, "employee_name": row.employee_name,
            "role_label": row.role_label, "attraction_id": row.attraction_id, "attraction_name": circles.get(row.attraction_id, ""),
            "status": row.status, "reason": row.reason, "due_on": row.due_on, "seen_at": row.seen_at,
            "confirmed_at": row.confirmed_at, "delivered_at": row.delivered_at,
            "signature_url": f"/api/files/{row.signature_file_id}?preview=true" if row.signature_file_id else None,
            "signing_statement": row.signing_statement}


def grouped_records(db, rows):
    """Current organization is a display aid; historical signing snapshots stay intact."""
    today = date.today().isoformat()
    memberships = db.query(GroupMembership, WorkGroup).join(WorkGroup).filter(
        GroupMembership.employee_id.in_({row.employee_id for row in rows}),
        GroupMembership.status == "active", GroupMembership.starts_on <= today,
        or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= today),
    ).order_by(GroupMembership.starts_on.desc(), GroupMembership.id.desc()).all()
    current = {}
    for membership, group in memberships:
        current.setdefault(membership.employee_id, group)
    items, groups = [], {}
    for row in rows:
        item = delivery_payload(db, row)
        group = current.get(row.employee_id)
        # A later cross-circle move must not expose the new circle's group via an old receipt.
        if group and group.attraction_id != row.attraction_id:
            group = None
        key = f"group:{group.id}" if group else f"ungrouped:{row.attraction_id or 0}"
        name = group.name if group else "未分组 / 管理人员"
        item.update(group_key=key, group_id=group.id if group else None, group_name=name)
        items.append(item)
        if key not in groups:
            groups[key] = {"key": key, "id": group.id if group else None, "name": name,
                "attraction_id": row.attraction_id, "attraction_name": item["attraction_name"],
                "code": group.code or group.name if group else "", "total": 0,
                "required": 0, "completed": 0, "pending": 0, "suspended": 0, "exited": 0}
        bucket = groups[key]
        bucket["total"] += 1
        if row.status in {"suspended", "exited"}:
            bucket[row.status] += 1
        else:
            bucket["required"] += 1
            bucket["completed" if row.confirmed_at else "pending"] += 1
    ordered = sorted(groups.values(), key=lambda g: (g["attraction_id"] or 0, g["id"] is None, g["code"], g["id"] or 0))
    rank = {g["key"]: index for index, g in enumerate(ordered)}
    items.sort(key=lambda item: (rank[item["group_key"]], item["employee_no"], item["id"]))
    return items, ordered


def handover_overview(db, announcement, user):
    rows = db.query(AnnouncementHandover).filter_by(announcement_id=announcement.id).order_by(
        AnnouncementHandover.requested_at.desc(), AnnouncementHandover.id.desc()).all()
    ids = {announcement.owner_id} | {id for h in rows for id in (h.from_id, h.to_id, h.requested_by)}
    people = {e.id: {"id": e.id, "name": e.name, "employee_no": e.employee_no}
              for e in db.query(Employee).filter(Employee.id.in_(ids))}
    return {"owner": people[announcement.owner_id], "history": [
        {"id": h.id, "from": people[h.from_id], "to": people[h.to_id], "requested_by": people[h.requested_by],
         "reason": h.reason, "status": h.status, "requested_at": h.requested_at, "completed_at": h.completed_at,
         "can_accept": h.status == "pending" and h.to_id == user.id and h.from_id == announcement.owner_id}
        for h in rows]}


def payload(db, user, announcement, version, detail=False):
    receipt = db.query(AnnouncementDelivery).filter_by(version_id=version.id, employee_id=user.id).first()
    favorite = db.query(AnnouncementFavorite.id).filter_by(announcement_id=announcement.id, employee_id=user.id).first()
    circles = live_circles(db)
    project = db.get(AnnouncementProject, version.project_id) if version.project_id else None
    today = date.today().isoformat()
    effective = announcement.status == "published" and version.number == announcement.current_version and version.effective_on <= today and (not version.expires_on or version.expires_on >= today)
    attachments = [{**a, "filename": db.get(StoredFile, a["file_id"]).original_filename,
                    "url": f"/api/files/{a['file_id']}"} for a in unpack(version.attachments_json)]
    result = {"id": announcement.id, "version_id": version.id, "version": version.number, "current_version": announcement.current_version,
        "title": version.title, "summary": version.summary, "category": version.category,
        "scope_kind": version.scope_kind, "attraction_ids": unpack(version.attraction_ids_json), "project_id": version.project_id,
        "scope_label": project.name if project else "、".join(circles.get(id, str(id)) for id in unpack(version.attraction_ids_json)),
        "tiers": unpack(version.tiers_json), "confirmation_level": version.confirmation_level,
        "effective_on": version.effective_on, "expires_on": version.expires_on, "due_on": receipt.due_on if receipt else version.due_on,
        "published_at": announcement.published_at, "updated_at": version.created_at, "owner_id": announcement.owner_id,
        "status": announcement.status, "effective": effective, "favorite": bool(favorite), "change_summary": version.change_summary,
        "receipt": delivery_payload(db, receipt) if receipt else None, "attachments": attachments,
        "can_edit": announcement.owner_id == user.id, "can_manage": record_ids(db, user, announcement, version) != set(),
        "withdrawn_reason": announcement.withdrawn_reason, "withdrawn_at": announcement.withdrawn_at}
    if detail:
        previous = db.query(AnnouncementVersion).filter_by(announcement_id=announcement.id, number=version.number-1).first()
        old_lines = previous.body.splitlines() if previous else []
        matcher = difflib.SequenceMatcher(a=old_lines, b=version.body.splitlines())
        changed = set()
        for tag, _, _, start, end in matcher.get_opcodes():
            if tag in {"insert", "replace"} and previous:
                changed.update(range(start, end))
        result.update(body=version.body, body_lines=[{"text": line, "changed": i in changed} for i, line in enumerate(version.body.splitlines())],
                      author=db.get(Employee, version.created_by).name,
                      versions=[{"number": v.number, "title": v.title, "created_at": v.created_at,
                                 "accessible": can_read_version(db, user, announcement, v)} for v in db.query(AnnouncementVersion).filter_by(announcement_id=announcement.id).order_by(AnnouncementVersion.number.desc())])
    return result


def summary(db, user):
    sync_current(db)
    pending = db.query(AnnouncementDelivery).filter_by(employee_id=user.id, status="pending").count()
    messages = db.query(AnnouncementNotification).filter_by(employee_id=user.id, read_at=None).count()
    handovers = db.query(AnnouncementHandover).filter_by(to_id=user.id, status="pending").count()
    db.commit()
    return {"pending": pending, "messages": messages, "handovers": handovers, "total": pending+messages+handovers}
