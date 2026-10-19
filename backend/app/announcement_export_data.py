"""Export-only facts and grouping, restricted to already authorized receipts."""
from datetime import date, datetime

from sqlalchemy import or_

from app.v2_models import Employee, EmployeeLOAPeriod, GroupLeaderAssignment, UserAccount


def time_text(value):
    return value.isoformat(sep=" ", timespec="minutes") if isinstance(value, datetime) else str(value or "未记录")


def prepare_export_data(db, data):
    # Never add a delivery from outside records()'s permission-filtered items.
    items = [dict(row) for row in data["items"]]
    ids = {row["employee_id"] for row in items}
    people = {p.id: p for p in db.query(Employee).filter(Employee.id.in_(ids))} if ids else {}
    accounts = {a.employee_id: a for a in db.query(UserAccount).filter(UserAccount.employee_id.in_(ids))} if ids else {}
    today = date.today().isoformat()
    loa_ids = {p.employee_id for p in db.query(EmployeeLOAPeriod).filter(
        EmployeeLOAPeriod.employee_id.in_(ids), EmployeeLOAPeriod.status == "active",
        EmployeeLOAPeriod.starts_on <= today,
        or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= today))} if ids else set()
    published = data["announcement"].get("updated_at") or data["announcement"].get("published_at")
    if isinstance(published, str):
        published = datetime.fromisoformat(published)
    for row in items:
        person, account = people.get(row["employee_id"]), accounts.get(row["employee_id"])
        facts = []
        if person and person.account_deleted_at and not account:
            facts.append(f"账号已删除（{time_text(person.account_deleted_at)}），档案保留")
        elif account and not account.enabled:
            facts.append(f"当前账号停用；停用时间 {time_text(account.disabled_at)}")
        elif not account:
            facts.append("当前未找到账号")
        if person and not person.is_active:
            facts.append("当前员工档案停用")
        if person and person.terminated_on and person.terminated_on <= today:
            facts.append(f"档案离职日期 {person.terminated_on}")
        if row["employee_id"] in loa_ids:
            facts.append("当前有有效LOA记录")
        if account and published and account.created_at and account.created_at > published:
            facts.append(f"本版发布后账号新建（{time_text(account.created_at)}）")
        if row["status"] == "exited":
            facts.append("已退出本公告适用范围")
        elif row["status"] == "suspended":
            facts.append("签收暂缓" + ("（LOA）" if row.get("reason") == "LOA暂缓" else ""))
        elif not row.get("confirmed_at"):
            if row.get("reason") and row["reason"] != "首次接收":
                facts.append(f"接收来源：{row['reason']}（{time_text(row.get('delivered_at'))}）")
            facts.append(f"已阅读（{time_text(row['seen_at'])}），尚未签收" if row.get("seen_at") else "尚无阅读及签收记录")
            if not facts[:-1]:
                facts.append("未签原因未记录")
        else:
            facts.append("本人账号签收")
        row["status_note"] = "；".join(facts)

    groups = {g["id"]: g for g in data.get("groups", []) if g.get("id") is not None}
    assignments = db.query(GroupLeaderAssignment).filter(
        GroupLeaderAssignment.group_id.in_(groups),
        GroupLeaderAssignment.status == "active", GroupLeaderAssignment.starts_on <= today,
        or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= today),
        GroupLeaderAssignment.leader_type.in_({"formal", "acting"}),
    ).order_by(GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc()).all() if groups and ids else []
    current, by_person = set(), {}
    for assignment in assignments:
        key = (assignment.group_id, assignment.leader_type)
        if key not in current:
            current.add(key)
            if assignment.leader_employee_id in ids:
                by_person.setdefault(assignment.leader_employee_id, []).append(assignment)
    result = []
    for row in items:
        own = [a for a in by_person.get(row["employee_id"], [])
               if groups[a.group_id]["attraction_id"] == row["attraction_id"]]
        if not own:
            result.append(row)
            continue
        # Prefer the existing membership, otherwise the formal assignment.
        own.sort(key=lambda a: (a.group_id != row.get("group_id"), a.leader_type != "formal", a.group_id))
        placements = {}
        for assignment in own:
            placements.setdefault(assignment.group_id, []).append(assignment.leader_type)
        for index, (group_id, types) in enumerate(placements.items()):
            group = groups[group_id]
            role = " / ".join(name for kind, name in [("formal","组长"),("acting","代理组长")] if kind in types)
            result.append({**row, "group_id": group["id"], "group_key": group["key"], "group_name": group["name"],
                           "export_role": role, "export_reference": index > 0})
    return {**data, "items": result}
