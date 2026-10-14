"""Sealed self-only monthly bands, using the close's frozen circle and base role."""
from __future__ import annotations

import json
from hashlib import sha256
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session

from app.role_constants import SCORED_BASE_CODES, SUPERVISOR_SCORING_START_MONTH
from app.month_rank_policy import MIN_RANK_COHORT, MONTH_RANK_RULE_VERSION
from app.score_queries import employee_month_scores
from app.services.attendance import ensure_month_attendance, month_end
from app.services.identity import base_roles_at, employee_active_on
from app.v2_models import (
    Attraction, AttendanceMonthlyScore, DeductionRecord, Employee, EmployeeMonthOrganizationSnapshot,
    EmployeeMonthRankSnapshot, MonthClosure, MonthRankGeneration, RecognitionRecord,
)


RANK_TIERS = {
    90: ("荣耀王者", "每份投入，都值得肯定。"),
    80: ("傲世宗师", "点滴记录，汇成当月积累。"),
    70: ("超凡大师", "认真积累，记录每份投入。"),
    60: ("至尊星耀", "稳步向前，每一步都算数。"),
    50: ("永恒钻石", "点滴积累，收获更多进步。"),
    40: ("尊贵铂金", "持续投入，成长正在发生。"),
    30: ("荣耀黄金", "点滴努力，都在积累光芒。"),
    20: ("探索白银", "找到自己的节奏，继续向前。"),
    10: ("成长青铜", "一步一步，积累新的进步。"),
    0: ("启航新星", "每份努力都有价值，期待新的积累。"),
}

MONTH_OPENINGS = (
    "新年伊始", "岁序更新", "春意渐起", "春光正好", "初夏已至", "夏日渐盛",
    "盛夏时节", "夏末时分", "初秋已至", "秋意渐浓", "深秋时节", "岁末将至",
)


def build_month_comment(db: Session, snapshot) -> str:
    """Generated once at close, using only consecutive comparable sealed months."""
    year, number = map(int, snapshot.score_month.split("-"))
    previous_month = f"{year - 1:04d}-12" if number == 1 else f"{year:04d}-{number - 1:02d}"
    previous = sealed_self_score(db, previous_month, snapshot.employee_id)
    variant = int.from_bytes(sha256(f"{snapshot.employee_id}:{snapshot.score_month}".encode()).digest()[:2], "big") % 3
    previous_signature = supervisor_seal_context(db, previous_month)[0] if previous and previous.population == "supervisor" else None
    previous_ready = previous and (previous.population != "supervisor" or
        bool(previous_signature) and previous.comparison_scope_json == previous_signature)
    if previous_ready and previous.status == "ranked" and previous.base_role_code == snapshot.base_role_code and (
        previous.population == snapshot.population and
        (snapshot.population == "supervisor" or previous.attraction_id == snapshot.attraction_id)
    ):
        delta = snapshot.total_score - previous.total_score
        value = format(abs(delta), ".2f").rstrip("0").rstrip(".")
        if delta > 0:
            change = f"比上月增加{value}分。"
            ending = ("每一步积累，都值得肯定。", "继续保持自己的节奏。", "期待你的下一次进步。")[variant]
        elif delta < 0:
            change = f"本月较上月减少{value}分。"
            ending = ("调整节奏，从一个小目标继续积累。", "给自己一点时间，下一步继续向前。", "起伏也是成长的一部分，期待新的积累。")[variant]
        else:
            change = "本月分数与上月持平。"
            ending = ("稳稳积累，也是一种成长。", "保持投入，期待新的进步。", "认真走好每一步，积累属于自己的光芒。")[variant]
    else:
        change = ""
        ending = ("从本月出发，继续积累成长。", "每份投入都有价值，期待新的进步。", "找到自己的节奏，一步一步向前。")[variant]
    praise = (RANK_TIERS[snapshot.tier][1].rstrip("。") + "，" + ending
              if snapshot.tier >= 60 and not change.startswith("本月较上月减少") else ending)
    return f"{MONTH_OPENINGS[number - 1]}，{change}{praise}"


def rank_band(lower_count: int, cohort_size: int, equal_count: int = 1) -> int | None:
    if cohort_size < MIN_RANK_COHORT or equal_count == cohort_size:
        return None
    if not (0 <= lower_count < cohort_size and 1 <= equal_count <= cohort_size - lower_count):
        return None
    # Midpoint of the tied block, including self. Integer cross-multiplication
    # retains the existing strict decile boundaries without float rounding.
    return next((tier for tier in range(90, 0, -10)
                 if 50 * (2 * lower_count + equal_count) > tier * cohort_size), 0)


def population_status(values) -> str:
    if len(values) < MIN_RANK_COHORT:
        return "insufficient"
    return "same_scores" if values[0] == values[-1] else "ranked"


def prepare_rank_attendance(db: Session, month: str, attraction_id: int | None) -> None:
    """Include eligible zero-input employees before the organization is frozen."""
    query = db.query(Employee, EmployeeMonthOrganizationSnapshot).outerjoin(
        EmployeeMonthOrganizationSnapshot,
        and_(EmployeeMonthOrganizationSnapshot.employee_id == Employee.id,
             EmployeeMonthOrganizationSnapshot.score_month == month),
    )
    if attraction_id is not None:
        query = query.filter(func.coalesce(EmployeeMonthOrganizationSnapshot.attraction_id,
                                          Employee.attraction_id) == attraction_id)
    candidates = query.all()
    roles = base_roles_at(db, [employee.id for employee, _ in candidates], month_end(month))
    ids = []
    for employee, snapshot in candidates:
        role = roles.get(employee.id)
        code = snapshot.base_role_code if snapshot else (role.code if role else None)
        if code not in SCORED_BASE_CODES or (code == "SUPERVISOR" and month < SUPERVISOR_SCORING_START_MONTH):
            continue
        if employee_active_on(employee, month_end(month)):
            ids.append(employee.id)
    ensure_month_attendance(db, month, ids)
    db.flush()


def capture_month_ranks(db: Session, closure: MonthClosure) -> int:
    """Called inside the close transaction; never changes source score rows."""
    db.flush()
    generation = db.query(MonthRankGeneration).filter_by(closure_id=closure.id).first()
    if generation is None:
        generation = MonthRankGeneration(closure_id=closure.id, closed_at=closure.closed_at)
        db.add(generation)
        db.flush()
    else:
        db.query(EmployeeMonthRankSnapshot).filter_by(generation_id=generation.id).delete(synchronize_session=False)
        generation.closed_at = closure.closed_at
        generation.captured_at = datetime.now()
    query = db.query(EmployeeMonthOrganizationSnapshot).join(
        Attraction, Attraction.id == EmployeeMonthOrganizationSnapshot.attraction_id
    ).filter(EmployeeMonthOrganizationSnapshot.score_month == closure.closure_month,
             Attraction.employee_circle.is_(True))
    if closure.attraction_id is not None:
        query = query.filter(EmployeeMonthOrganizationSnapshot.attraction_id == closure.attraction_id)
    snapshots = [row for row in query.all() if row.base_role_code in SCORED_BASE_CODES
                 and not (row.base_role_code == "SUPERVISOR" and row.score_month < SUPERVISOR_SCORING_START_MONTH)]
    ids = [row.employee_id for row in snapshots]
    scores = {row["employee_id"]: row for row in employee_month_scores(db, closure.closure_month, ids)}
    categories = defaultdict(dict)
    if ids:
        for employee_id, name, value in db.query(
            RecognitionRecord.employee_id, RecognitionRecord.recognition_type_name,
            text("SUM(CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END)"),
        ).filter(RecognitionRecord.employee_id.in_(ids),
                 RecognitionRecord.recognition_month == closure.closure_month,
                 RecognitionRecord.status == "confirmed").group_by(
                     RecognitionRecord.employee_id, RecognitionRecord.recognition_type_name).all():
            categories[employee_id][name] = float(value or 0)
    cohorts = defaultdict(list)
    for organization in snapshots:
        score = scores.get(organization.employee_id)
        if score and not score["loa_excluded"]:
            population = "supervisor" if organization.base_role_code == "SUPERVISOR" else "frontline"
            cohorts[(organization.attraction_id, population)].append(Decimal(str(score["total_score"])))
    for values in cohorts.values():
        values.sort()
    captured = 0
    for organization in snapshots:
        score = scores.get(organization.employee_id)
        if score is None:
            continue  # No historical score input: do not invent a legacy participant.
        population = "supervisor" if organization.base_role_code == "SUPERVISOR" else "frontline"
        values = cohorts[(organization.attraction_id, population)]
        lower = bisect_left(values, Decimal(str(score["total_score"]))) if not score["loa_excluded"] else 0
        equal = bisect_right(values, Decimal(str(score["total_score"]))) - lower if not score["loa_excluded"] else 0
        tier = rank_band(lower, len(values), equal) if not score["loa_excluded"] else None
        excluded = bool(score["loa_excluded"])
        db.add(EmployeeMonthRankSnapshot(
            generation_id=generation.id, employee_id=organization.employee_id,
            score_month=closure.closure_month, attraction_id=organization.attraction_id,
            attraction_name=organization.attraction_name or "未设置景点圈",
            base_role_code=organization.base_role_code, population=population,
            status="excluded" if excluded else ("waiting_global" if population == "supervisor"
                else population_status(values)),
            tier=None if population == "supervisor" else tier,
            cohort_size=0 if population == "supervisor" else len(values),
            lower_count=0 if population == "supervisor" else lower,
            equal_count=0 if population == "supervisor" else equal,
            rule_version=MONTH_RANK_RULE_VERSION,
            **{field: Decimal(str(score[field])) for field in
               ("recognition_score", "attendance_score", "deduction_score", "total_score")},
            category_scores_json=json.dumps({name: 0 if excluded else value for name, value in
                                            categories[organization.employee_id].items()}, ensure_ascii=False),
        ))
        captured += 1
    db.flush()
    refresh_supervisor_month_ranks(db, closure.closure_month)
    for row in db.query(EmployeeMonthRankSnapshot).filter_by(generation_id=generation.id, status="ranked").all():
        if not row.comment_text:
            row.comment_text = build_month_comment(db, row)
    return captured


def backfill_closed_rankings(db: Session) -> None:
    """One-shot generations from existing frozen organization/score evidence."""
    missing = db.query(MonthClosure).outerjoin(
        MonthRankGeneration, MonthRankGeneration.closure_id == MonthClosure.id,
    ).filter(MonthClosure.status == "closed", MonthClosure.closed_at.is_not(None),
             MonthRankGeneration.id.is_(None)).order_by(
                 MonthClosure.closure_month.asc(), MonthClosure.closed_at.asc(), MonthClosure.id.asc()).all()
    for closure in missing:
        capture_month_ranks(db, closure)
    months = [value for value, in db.query(EmployeeMonthRankSnapshot.score_month).filter(
        EmployeeMonthRankSnapshot.population == "supervisor").distinct().order_by(EmployeeMonthRankSnapshot.score_month.asc()).all()]
    for month in months:
        refresh_supervisor_month_ranks(db, month)
    for row in db.query(EmployeeMonthRankSnapshot).filter_by(status="ranked", comment_text="").all():
        row.comment_text = build_month_comment(db, row)
    db.commit()


def sealed_self_score(db: Session, month: str, employee_id: int):
    # Local import avoids a startup cycle through authentication/database setup.
    from app.month_closure import month_closure_scope

    rows = db.query(EmployeeMonthRankSnapshot, MonthRankGeneration, MonthClosure).join(
        MonthRankGeneration, MonthRankGeneration.id == EmployeeMonthRankSnapshot.generation_id,
    ).join(MonthClosure, MonthClosure.id == MonthRankGeneration.closure_id).filter(
        EmployeeMonthRankSnapshot.employee_id == employee_id,
        EmployeeMonthRankSnapshot.score_month == month, MonthClosure.status == "closed",
        MonthRankGeneration.closed_at == MonthClosure.closed_at,
    ).all()
    for snapshot, _, closure in rows:
        effective = month_closure_scope(db, month, snapshot.attraction_id)
        if effective and effective.id == closure.id:
            return snapshot
    return None


def supervisor_seal_context(db: Session, month: str) -> tuple[str, set[int]]:
    """Signature of all relevant circle closes; empty until every scope closes."""
    from app.month_closure import month_closure_scope

    if month < SUPERVISOR_SCORING_START_MONTH:
        return "", set()
    candidates = db.query(Employee, EmployeeMonthOrganizationSnapshot).outerjoin(
        EmployeeMonthOrganizationSnapshot,
        and_(EmployeeMonthOrganizationSnapshot.employee_id == Employee.id,
             EmployeeMonthOrganizationSnapshot.score_month == month),
    ).join(Attraction, Attraction.id == func.coalesce(
        EmployeeMonthOrganizationSnapshot.attraction_id, Employee.attraction_id)
    ).filter(Attraction.employee_circle.is_(True)).all()
    roles = base_roles_at(db, [person.id for person, _ in candidates], month_end(month))
    circles = set()
    for person, organization in candidates:
        base = organization.base_role_code if organization else getattr(roles.get(person.id), "code", None)
        if base == "SUPERVISOR" and (organization or employee_active_on(person, month_end(month))):
            circles.add(organization.attraction_id if organization else person.attraction_id)
    signature = []
    for circle in sorted(circles):
        closure = month_closure_scope(db, month, circle)
        if not closure or not closure.closed_at:
            return "", circles
        signature.append([circle, closure.id, closure.closed_at.isoformat()])
    return json.dumps(signature, separators=(",", ":")) if signature else "", circles


def refresh_supervisor_month_ranks(db: Session, month: str) -> int:
    from app.month_closure import month_closure_scope

    signature, circles = supervisor_seal_context(db, month)
    if not signature:
        return 0
    rows = db.query(EmployeeMonthRankSnapshot, MonthRankGeneration, MonthClosure).join(
        MonthRankGeneration, MonthRankGeneration.id == EmployeeMonthRankSnapshot.generation_id,
    ).join(MonthClosure, MonthClosure.id == MonthRankGeneration.closure_id).filter(
        EmployeeMonthRankSnapshot.score_month == month,
        EmployeeMonthRankSnapshot.population == "supervisor", MonthClosure.status == "closed",
        MonthRankGeneration.closed_at == MonthClosure.closed_at,
    ).all()
    selected = []
    effective = {circle: month_closure_scope(db, month, circle) for circle in circles}
    for row, _, closure in rows:
        if row.attraction_id in effective and effective[row.attraction_id].id == closure.id:
            selected.append(row)
    if {row.attraction_id for row in selected} != circles:
        return 0  # A legacy scope lacks frozen scoring evidence.
    if any(row.rule_version != MONTH_RANK_RULE_VERSION for row in selected):
        return 0  # Preserve earlier calculations; never mix rule versions.
    values = sorted(row.total_score for row in selected if row.status != "excluded")
    for row in selected:
        if row.comparison_scope_json == signature:
            continue
        row.comparison_scope_json = signature
        row.cohort_size = len(values)
        if row.status == "excluded":
            continue
        row.lower_count = bisect_left(values, row.total_score)
        row.equal_count = bisect_right(values, row.total_score) - row.lower_count
        row.tier = rank_band(row.lower_count, len(values), row.equal_count)
        row.rule_version = MONTH_RANK_RULE_VERSION
        row.status = population_status(values)
        row.comment_text = build_month_comment(db, row) if row.status == "ranked" else ""
    db.flush()
    return len(selected)


def member_month_rank(db: Session, month: str, employee) -> dict:
    from app.month_closure import month_closure_scope

    organization = db.query(EmployeeMonthOrganizationSnapshot).filter_by(
        employee_id=employee.id, score_month=month).first()
    circle_id = organization.attraction_id if organization else employee.attraction_id
    selected = sealed_self_score(db, month, employee.id)
    closure = month_closure_scope(db, month, circle_id)
    closed = bool(closure)
    base = organization.base_role_code if organization else getattr(base_roles_at(db, [employee.id], month_end(month)).get(employee.id), "code", None)
    if base == "SUPERVISOR" and month < SUPERVISOR_SCORING_START_MONTH:
        return {"status": "unavailable", "month": month, "month_closed": closed,
                "message": "主管与TA GSM段位从2026年10月起提供。"}
    if selected is None and not closure:
        reopened = db.query(MonthClosure.id).filter(
            MonthClosure.closure_month == month, MonthClosure.status == "open",
            MonthClosure.reopened_at.is_not(None),
            or_(MonthClosure.attraction_id.is_(None), MonthClosure.attraction_id == circle_id),
        ).first()
        if reopened:
            return {"status": "reopened", "month": month, "month_closed": False,
                    "message": "该月份已重开，段位重新核算中。"}
    if selected is None:
        return {"status": "unavailable" if closed else "pending", "month": month,
                "month_closed": closed,
                "message": "该月暂无可用的月结段位数据。" if closed else (
                    "参与主管计分的各景点圈全部完成该月月结后，才能查看段位与评语。" if base == "SUPERVISOR"
                    else "所属景点圈完成该月月结后，才能查看段位与评语。")}
    if selected.rule_version != MONTH_RANK_RULE_VERSION:
        return {"status": "unavailable", "month": month, "month_closed": closed,
                "message": "该月结果使用较早计算规则，暂不展示游戏段位。"}
    result = {
        "status": selected.status, "month": selected.score_month, "month_closed": closed,
        "attraction_name": selected.attraction_name,
        "population_label": "主管" if selected.population == "supervisor" else "CM/TR",
        "comparison_scope": "all_circles" if selected.population == "supervisor" else "circle",
        "comparison_label": "全部景点圈" if selected.population == "supervisor" else selected.attraction_name,
    }
    if selected.population == "supervisor" and selected.status != "excluded":
        signature, circles = supervisor_seal_context(db, month)
        if not signature or selected.comparison_scope_json != signature:
            reopened = not signature and db.query(MonthClosure.id).filter(
                MonthClosure.closure_month == month, MonthClosure.status == "open",
                MonthClosure.reopened_at.is_not(None),
                or_(MonthClosure.attraction_id.is_(None), MonthClosure.attraction_id.in_(circles)),
            ).first()
            result.update(status="reopened" if reopened else "unavailable" if signature else "waiting_global",
                message="有参与圈重开月结，主管与TA GSM段位重新核算中。" if reopened else
                    "各圈已月结，暂无完整的主管组段位数据。" if signature else
                    "待参与主管计分的各景点圈全部月结后，统一展示主管与TA GSM段位。")
            return result
    if selected.status == "ranked":
        label, message = RANK_TIERS[selected.tier]
        result.update(tier=selected.tier, label=label, message=selected.comment_text or message,
                      rule_version=selected.rule_version)
    else:
        result["message"] = ("该月LOA，不参与游戏段位生成。" if selected.status == "excluded" else
                             "本月同类人员积分相同，不生成游戏段位。" if selected.status == "same_scores" else
                             "本月比较人数不足10人，暂不生成游戏段位。")
    return result


def member_month_query_context(db: Session, month: str, employee) -> dict:
    """Own historical months and frozen identity; no people/circle filters."""
    today = date.today()
    current = today.strftime("%Y-%m")
    current_index = today.year * 12 + today.month - 1
    months = {month}
    for offset in range(3):
        year, index = divmod(current_index - offset, 12)
        months.add(f"{year:04d}-{index + 1:02d}")
    for model, column in (
        (AttendanceMonthlyScore, AttendanceMonthlyScore.attendance_month),
        (RecognitionRecord, RecognitionRecord.recognition_month),
        (DeductionRecord, DeductionRecord.deduction_month),
        (EmployeeMonthOrganizationSnapshot, EmployeeMonthOrganizationSnapshot.score_month),
    ):
        months.update(value for value, in db.query(column).filter(
            model.employee_id == employee.id, column <= current).distinct().all())
    organizations = {row.score_month: row for row in db.query(EmployeeMonthOrganizationSnapshot).filter(
        EmployeeMonthOrganizationSnapshot.employee_id == employee.id,
        EmployeeMonthOrganizationSnapshot.score_month.in_(months)).all()}
    circles = {row.attraction_id for row in organizations.values() if row.attraction_id is not None}
    if employee.attraction_id is not None:
        circles.add(employee.attraction_id)
    closures = db.query(MonthClosure).filter(
        MonthClosure.closure_month.in_(months),
        or_(MonthClosure.attraction_id.is_(None), MonthClosure.attraction_id.in_(circles)),
    ).all()
    by_scope = {(row.closure_month, row.attraction_id): row for row in closures}
    options = []
    for value in sorted(months, reverse=True):
        organization = organizations.get(value)
        circle = organization.attraction_id if organization else employee.attraction_id
        scope = [row for row in (by_scope.get((value, None)), by_scope.get((value, circle))) if row]
        status = ("closed" if any(row.status == "closed" for row in scope)
                  else "reopened" if any(row.reopened_at is not None for row in scope) else "pending")
        options.append({"month": value, "status": status, "status_label": {
            "closed": "已月结", "reopened": "重新核算中", "pending": "待月结",
        }[status]})
    organization = organizations.get(month)
    selected = None
    if organization:
        label = {"CM": "CM", "TR": "TR", "SUPERVISOR": "主管"}.get(
            organization.base_role_code, organization.base_role_code or "未记录")
        duty = {"TA_SUPERVISOR": "代理TA主管", "TA_GSM": "代理TA GSM"}.get(organization.acting_duty_code)
        selected = {
            "attraction_name": organization.attraction_name,
            "base_role_code": organization.base_role_code,
            "role_label": f"{label} · {duty}" if duty else label,
            "group_name": organization.group_name, "leader_name": organization.leader_name,
        }
    return {"query_months": options, "month_organization": selected}
