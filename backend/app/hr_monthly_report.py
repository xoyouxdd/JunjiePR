"""Read-only monthly HR report data. Scoring stays owned by statistics_payload."""
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_

from app.routers.statistics import statistics_payload, trend_month_keys
from app.routers.declaration_statistics import declaration_payload, checked_month
from app.routers._shared import month_closure_scope
from app.v2_models import Attraction, RecognitionRecord, DeductionRecord, EmployeeMonthOrganizationSnapshot, GroupMembership, WorkGroup, Role
from app.v2_services import roles_at

REPORT_ROLES = frozenset({"GSM", "AM", "OM", "SYSTEM_ADMIN"})
TEMPLATES = {"forest": "森林都市", "minimal": "简约汇报", "warm": "温暖团队"}
SECTIONS = {"overview": "月度概览", "deductions": "减分分类", "recognitions": "加分分类", "issuers": "认可发放", "recognition_stats": "认可统计及趋势", "groups": "小组分排名", "excellent": "优秀员工", "rankings": "PR排名", "notes": "工作与规则说明", "photos": "团队活动", "birthdays": "生日祝福", "closing": "结束页"}


def require_report(user):
    # Explicit whitelist, independent from registration/review/void permissions.
    if user.role.code not in REPORT_ROLES or "HR_MONTHLY_REPORT" not in user.permissions:
        raise HTTPException(403, "无HR月报制作权限")
    return user


def report_data(db, month, attraction_id=None, *, with_trend=True):
    checked_month(month)
    circles = db.query(Attraction).filter(Attraction.employee_circle.is_(True)).order_by(Attraction.id).all()
    names = {a.id: a.name for a in circles}
    if attraction_id is not None and attraction_id not in names:
        raise HTTPException(400, "请选择有效景点圈")
    ids = {attraction_id} if attraction_id is not None else set(names)
    end = f"{month}-{monthrange(int(month[:4]), int(month[5:]))[1]:02d}"
    # This service has its own authorization. No bypass of existing API guards.
    stats = statistics_payload(db, month, attraction_id, user=None, include_records=True, include_hierarchy=False)
    scores = [dict(r) for r in stats["scores"] if r["attraction_id"] in ids]
    roles = roles_at(db, [r["employee_id"] for r in scores], end)
    for r in scores:
        r["category"] = roles[r["employee_id"]].code if roles.get(r["employee_id"]) else "OTHER"
    recs = db.query(RecognitionRecord).filter(RecognitionRecord.recognition_month == month, RecognitionRecord.home_attraction_id.in_(ids), RecognitionRecord.status == "confirmed").all()
    declarations = declaration_payload(db, month, attraction_id, None, None, "", "")
    deduction_rows = [r for r in declarations["records"] if r["attraction_id"] in ids]
    deductions = [r for r in deduction_rows if r["raw_status"] == "active"]
    deduction_ids = {r["id"] for r in deductions}
    deduction_objects = db.query(DeductionRecord).filter(DeductionRecord.id.in_(deduction_ids)).all() if deduction_ids else []
    role_names = {r.name: r.code for r in db.query(Role).all()}
    def classification(records, kind):
        counts = Counter()
        for r in records:
            circle = r.home_attraction_id if kind == "recognition" else r.attraction_id_snapshot
            category = str(r.employee_role_code_snapshot or r.employee_role_snapshot) if kind == "recognition" else str(r.employee_role_snapshot)
            category = role_names.get(category, category)
            category = category if category in {"CM", "TR"} else "LEAD/其他"
            type_name = r.recognition_type_name if kind == "recognition" else r.deduction_type_name
            counts[(circle, category, type_name)] += 1
        return [{"attraction_name": names[c], "category": role, "type": type_name, "count": count} for (c, role, type_name), count in sorted(counts.items())]
    issuers = Counter((r.recognizer_employee_id, r.recognizer_name) for r in recs)
    issuer_rows = [{"employee_id": eid, "name": name, "count": n} for (eid, name), n in sorted(issuers.items(), key=lambda x: (-x[1], x[0][0]))]
    circle_rows = []
    for cid in sorted(ids):
        n = sum(r["attraction_id"] == cid for r in scores)
        count = sum(r.home_attraction_id == cid for r in recs)
        closure = month_closure_scope(db, month, cid)
        circle_rows.append({"id": cid, "name": names[cid], "employee_count": n, "recognition_count": count, "recognition_rate": round(count / n * 100, 2) if n else None, "closed": bool(closure and closure.status == "closed")})
    snapshots = {r.employee_id: r for r in db.query(EmployeeMonthOrganizationSnapshot).filter(EmployeeMonthOrganizationSnapshot.score_month == month).all()}
    memberships = {r.employee_id: r.group_id for r in db.query(GroupMembership).filter(GroupMembership.starts_on <= end, or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= end), GroupMembership.status == "active").order_by(GroupMembership.starts_on, GroupMembership.id).all()}
    groups = {r.id: r for r in db.query(WorkGroup).all()}
    grouped = defaultdict(list)
    for r in scores:
        if r["category"] not in {"CM", "TR"}:
            continue
        snap = snapshots.get(r["employee_id"])
        gid = snap.group_id if snap else memberships.get(r["employee_id"])
        if gid:
            grouped[(r["attraction_id"], gid)].append(r)
    group_rows = []
    for (cid, gid), members in grouped.items():
        snap = next((snapshots.get(r["employee_id"]) for r in members if snapshots.get(r["employee_id"])), None)
        total = round(sum(r["total_score"] for r in members), 2)
        group_rows.append({"group_id": gid, "name": snap.group_name if snap else groups[gid].name, "attraction_name": names[cid], "employee_count": len(members), "total_score": total, "average": round(total / len(members), 2)})
    group_rows.sort(key=lambda r: (-r["average"], r["group_id"]))
    rankings = []
    for cid in sorted(ids):
        for category, role_codes in (("CM", {"CM"}), ("TR", {"TR"}), ("LEAD", {"SUPERVISOR", "TA_SUPERVISOR"})):
            rows = [r for r in scores if r["attraction_id"] == cid and r["category"] in role_codes]
            rows.sort(key=lambda r: (-r["total_score"], r["employee_no"]))
            rankings.append({"attraction_id": cid, "attraction_name": names[cid], "category": category, "rows": [{**r, "rank": i + 1} for i, r in enumerate(rows)]})
    candidates = [{**r, "recommendation": "月度PR排名候选"} for ranking in rankings for r in ranking["rows"][:3]]
    count = len(recs)
    population = len(scores)
    data = {"month": month, "attraction_id": attraction_id, "scope_name": names.get(attraction_id, "全部景点圈"), "generated_at": datetime.now().isoformat(timespec="seconds"), "draft": not all(r["closed"] for r in circle_rows), "organization_basis": stats["organization_basis"], "summary": {**stats["summary"], "employee_count": population, "recognition_count": count, "deduction_count": len(deductions), "recognition_rate": round(count / population * 100, 2) if population else None}, "circles": circle_rows, "recognitions": classification(recs, "recognition"), "deductions": classification(deduction_objects, "deduction"), "deduction_statuses": dict(Counter(r["business_status"] for r in deduction_rows)), "issuers": issuer_rows, "groups": group_rows, "rankings": rankings, "candidates": candidates, "loa_count": len([r for r in stats["loa_rows"] if r["attraction_id"] in ids]), "warnings": ["未月结标为草稿；无封存快照的员工使用当前归属。", "分类图为有效记录次数，不代表实际计分。", "认可率=确认认可数÷非LOA统计人数×100%，可超过100%。", "发放排名按签卡人归属，不重复累计代录人。"]}
    # Restrict score totals to real employee circles, as with the trend endpoint.
    for field in ("recognition_score", "attendance_score", "deduction_score", "total_score"):
        data["summary"][field] = round(sum(r[field] for r in scores), 2)
    data["trend"] = []
    if with_trend:
        for key in trend_month_keys(month, 3):
            prior = data if key == month else report_data(db, key, attraction_id, with_trend=False)
            data["trend"].append({"month": key, "rate": prior["summary"]["recognition_rate"], "draft": prior["draft"]})
    return data
