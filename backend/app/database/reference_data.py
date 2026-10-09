"""Reference catalog and permission/score seeds reapplied safely at each startup."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text


ROLE_DEFINITIONS = (
    ("CM", "CM", 10, False, True),
    ("TR", "TR", 10, False, True),
    ("TA_SUPERVISOR", "TA主管", 20, True, False),
    ("SUPERVISOR", "主管", 20, True, True),
    ("TA_GSM", "TA GSM", 30, False, False),
    ("GSM", "GSM", 30, False, False),
    ("AM", "AM", 40, False, False),
    ("OM", "OM", 50, False, False),
    ("HR_ADMIN", "HR管理员", 90, False, False),
    ("HR_CIRCLE", "景点圈HR", 90, False, False),
    ("SYSTEM_ADMIN", "最高管理员", 100, False, False),
)

PERMISSION_DEFINITIONS = {
    "SELF_RECOGNITION": "本人快速登记",
    "EMPLOYEE_ADD": "为CM/TR代录加分",
    "SICK_REGISTER": "病假登记",
    "SICK_LEAVE_IMPORT": "缺勤登记",
    "DECLARATION_STATS_VIEW": "查看声明登记统计",
    "DECLARATION_STATS_EXPORT": "导出声明登记统计",
    "HR_MONTHLY_REPORT": "制作及导出所有景点圈HR月报",
    "LOA_REGISTER": "登记长期病假",
    "DEDUCTION_DIRECT": "所有CM/TR声明扣分",
    "DEDUCTION_ALL": "所有CM/TR全部等级扣分",
    "REVIEW_DIRECT": "直属组员复核",
    "MEMBER_RECORDS": "直属组员记录",
    "DATA_VIEW": "查看三个景点圈统计数据",
    "DATA_EXPORT": "导出三个景点圈数据",
    "HR_MANAGE": "HR人员和组织管理",
    "PASSWORD_RESET": "重置员工账号密码",
    "POC_ISSUE": "开具POC特别贡献认可",
    "REVIEW_SUPERVISOR": "复核主管本人登记",
    "SUPERVISOR_SCORE": "为主管加分和扣分",
    "SYSTEM_ADMIN": "系统紧急纠错",
}

ROLE_PERMISSION_CODES = {
    "CM": ("SELF_RECOGNITION",),
    "TR": ("SELF_RECOGNITION",),
    "TA_SUPERVISOR": ("EMPLOYEE_ADD", "DEDUCTION_DIRECT", "REVIEW_DIRECT", "MEMBER_RECORDS", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
    "SUPERVISOR": ("SELF_RECOGNITION", "EMPLOYEE_ADD", "DEDUCTION_DIRECT", "REVIEW_DIRECT", "MEMBER_RECORDS", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
    "TA_GSM": ("EMPLOYEE_ADD", "DEDUCTION_ALL", "SUPERVISOR_SCORE", "DATA_VIEW", "POC_ISSUE", "LOA_REGISTER", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
    "GSM": ("EMPLOYEE_ADD", "DEDUCTION_ALL", "SUPERVISOR_SCORE", "REVIEW_SUPERVISOR", "DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET", "POC_ISSUE", "SICK_LEAVE_IMPORT", "LOA_REGISTER", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
    # AM reviews only supervisors acting as TA GSM; OM reviews none.
    "AM": ("REVIEW_SUPERVISOR", "DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET", "POC_ISSUE", "LOA_REGISTER", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
    "OM": ("DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET", "POC_ISSUE", "LOA_REGISTER", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
    "HR_ADMIN": ("HR_MANAGE", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
    "HR_CIRCLE": (
        "DATA_VIEW",
        "DATA_EXPORT",
        "HR_MANAGE",
        "PASSWORD_RESET",
        "SICK_LEAVE_IMPORT",
        "LOA_REGISTER",
        "DECLARATION_STATS_VIEW",
        "DECLARATION_STATS_EXPORT",
    ),
    "SYSTEM_ADMIN": ("SYSTEM_ADMIN", "HR_MANAGE", "DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET", "SICK_LEAVE_IMPORT", "DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"),
}

for _report_role in ("GSM", "AM", "OM", "SYSTEM_ADMIN"):
    ROLE_PERMISSION_CODES[_report_role] += ("HR_MONTHLY_REPORT",)

# Seed only creates these when a role has no score rules at all. Later edits
# go through the admin API; startup must not insert a new "today" default.
INITIAL_SCORE_RULE_EFFECTIVE_DATE = "2020-01-01"
DEFAULT_RECOGNIZER_SCORES = {
    "TA_SUPERVISOR": Decimal("0.50"),
    "SUPERVISOR": Decimal("0.50"),
    "TA_GSM": Decimal("1.00"),
    "GSM": Decimal("1.00"),
    "AM": Decimal("1.50"),
    "OM": Decimal("1.50"),
}

EMPLOYEE_CIRCLES = ("热力追踪", "矮人迷宫", "小熊罐子")
RECOGNITION_VENUES = (
    "热力追踪",
    "七个小矮人矿山车",
    "爱丽丝梦游仙境迷宫",
    "小熊维尼历险记",
    "旋转疯蜜罐",
)
LEGACY_CIRCLE_BY_VENUE = {
    "热力追踪": "热力追踪",
    "七个小矮人矿山车": "矮人迷宫",
    "爱丽丝梦游仙境迷宫": "矮人迷宫",
    "小熊维尼历险记": "小熊罐子",
    "旋转蜂蜜罐": "小熊罐子",
    "旋转疯蜜罐": "小熊罐子",
}


def ensure_attraction_catalog(db) -> None:
    from app.v2_models import Attraction, DeductionRecord, Employee, ManagementScope, RecognitionRecord, WorkGroup, GroupTransfer

    rows = {row.name: row for row in db.query(Attraction).all()}
    for name in (*EMPLOYEE_CIRCLES, *RECOGNITION_VENUES):
        if name not in rows:
            row = Attraction(name=name, active=True)
            db.add(row)
            rows[name] = row
    db.flush()

    for row in rows.values():
        row.employee_circle = row.name in EMPLOYEE_CIRCLES
        row.recognition_venue = row.name in RECOGNITION_VENUES
        if row.employee_circle or row.recognition_venue:
            row.active = True

    circles = {name: rows[name] for name in EMPLOYEE_CIRCLES}
    for legacy_name, circle_name in LEGACY_CIRCLE_BY_VENUE.items():
        legacy = rows.get(legacy_name)
        circle = circles[circle_name]
        if not legacy or legacy.id == circle.id:
            continue
        db.query(Employee).filter(Employee.attraction_id == legacy.id).update(
            {Employee.attraction_id: circle.id}, synchronize_session=False
        )
        for scope in db.query(ManagementScope).filter(ManagementScope.attraction_id == legacy.id).all():
            params = {
                "employee_id": scope.employee_id,
                "circle_id": circle.id,
                "starts_on": scope.starts_on,
                "ends_on": scope.ends_on,
                "created_at": scope.created_at,
            }
            db.execute(
                text(
                    "INSERT OR IGNORE INTO management_scopes "
                    "(employee_id, attraction_id, starts_on, ends_on, created_at) "
                    "VALUES (:employee_id, :circle_id, :starts_on, :ends_on, :created_at)"
                ),
                params,
            )
            db.execute(
                text(
                    "UPDATE management_scopes SET ends_on = CASE "
                    "WHEN ends_on IS NULL OR :ends_on IS NULL THEN NULL "
                    "WHEN ends_on < :ends_on THEN :ends_on ELSE ends_on END "
                    "WHERE employee_id=:employee_id AND attraction_id=:circle_id AND starts_on=:starts_on"
                ),
                params,
            )
            db.delete(scope)
        db.query(WorkGroup).filter(WorkGroup.attraction_id == legacy.id).update(
            {WorkGroup.attraction_id: circle.id}, synchronize_session=False
        )
        db.query(GroupTransfer).filter(GroupTransfer.attraction_id == legacy.id).update(
            {GroupTransfer.attraction_id: circle.id}, synchronize_session=False
        )
        db.query(DeductionRecord).filter(DeductionRecord.attraction_id_snapshot == legacy.id).update(
            {DeductionRecord.attraction_id_snapshot: circle.id}, synchronize_session=False
        )
        db.query(RecognitionRecord).filter(RecognitionRecord.home_attraction_id == legacy.id).update(
            {
                RecognitionRecord.home_attraction_id: circle.id,
                RecognitionRecord.home_attraction_name: circle.name,
            },
            synchronize_session=False,
        )
    db.commit()


def seed_reference_data(db) -> None:
    from app.v2_models import (
        AttendanceRule,
        DeductionLevel,
        DeductionType,
        Permission,
        RecognitionScoreRule,
        RecognitionType,
        Role,
        RolePermission,
    )

    ensure_attraction_catalog(db)
    for code, name, rank, can_lead, attendance_eligible in ROLE_DEFINITIONS:
        role = db.query(Role).filter(Role.code == code).first()
        if not role:
            role = Role(code=code, name=name, rank=rank)
            db.add(role)
        role.name = name
        role.rank = rank
        role.can_lead_group = can_lead
        role.attendance_eligible = attendance_eligible
        role.active = True
    db.flush()

    for code, name in PERMISSION_DEFINITIONS.items():
        permission = db.query(Permission).filter(Permission.code == code).first()
        if not permission:
            permission = Permission(code=code, name=name)
            db.add(permission)
        else:
            permission.name = name
    db.flush()
    for role_code, permission_codes in ROLE_PERMISSION_CODES.items():
        role = db.query(Role).filter(Role.code == role_code).one()
        configured = set(permission_codes)
        existing = db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
        for assignment in existing:
            permission = db.get(Permission, assignment.permission_id)
            if permission and permission.code not in configured:
                db.delete(assignment)
        for permission_code in permission_codes:
            permission = db.query(Permission).filter(Permission.code == permission_code).one()
            if not db.query(RolePermission).filter_by(role_id=role.id, permission_id=permission.id).first():
                db.add(RolePermission(role_id=role.id, permission_id=permission.id))

    for code, name in (
        ("SAFETY", "安全"),
        ("COURTESY", "礼仪"),
        ("INCLUSION", "包容"),
        ("EFFICIENCY", "效率"),
        ("SHOW", "演出"),
        ("MSP", "MSP"),
        ("COMMENDATION_LETTER", "表扬信"),
        ("POC", "POC特别贡献"),
        ("OTHER", "其他"),
    ):
        if not db.query(RecognitionType).filter(RecognitionType.code == code).first():
            db.add(RecognitionType(code=code, name=name, active=True))

    for role_code, score in DEFAULT_RECOGNIZER_SCORES.items():
        role = db.query(Role).filter(Role.code == role_code).one()
        if not db.query(RecognitionScoreRule).filter_by(role_id=role.id).first():
            db.add(
                RecognitionScoreRule(
                    role_id=role.id,
                    score=score,
                    effective_date=INITIAL_SCORE_RULE_EFFECTIVE_DATE,
                    active=True,
                )
            )

    if not db.query(AttendanceRule).first():
        db.add(
            AttendanceRule(
                base_score=Decimal("10.00"),
                perfect_bonus=Decimal("2.00"),
                sick_day_deduction=Decimal("0.45"),
                zero_threshold=Decimal("0.10"),
                effective_date=INITIAL_SCORE_RULE_EFFECTIVE_DATE,
                active=True,
            )
        )

    desired_deduction_types = (
        ("SAFETY", "安全"),
        ("AUDIT", "审计"),
        ("COURTESY", "礼仪"),
        ("INCLUSION", "包容"),
        ("SHOW", "演出"),
        ("EFFICIENCY", "效率"),
        ("MSP", "MSP"),
        ("COMPLAINT", "客诉"),
        ("SICK_LEAVE_VIOLATION", "违规病假"),
        ("OTHER", "其他"),
        ("ATT_EARLY_CLOCK", "考勤-早打卡"),
        ("ATT_LATE_CLOCK", "考勤-晚打卡"),
        ("ATT_LATE_WITHIN_30", "考勤-迟到30分钟内"),
        ("ATT_LATE_OVER_30", "考勤-迟到30分钟以上"),
        ("ATT_EARLY_LEAVE_WITHIN_30", "考勤-早退30分钟内"),
        ("ATT_EARLY_LEAVE_OVER_30", "考勤-早退30分钟以上"),
        ("ATT_MISSING_CLOCK", "考勤-未打卡"),
        ("ATT_REMOTE_CLOCK", "考勤-异地打卡"),
    )
    desired_deduction_codes = {code for code, _ in desired_deduction_types}
    db.query(DeductionType).filter(DeductionType.code.notin_(desired_deduction_codes)).update(
        {DeductionType.active: False}, synchronize_session=False
    )
    for code, name in desired_deduction_types:
        row = db.query(DeductionType).filter(DeductionType.code == code).first()
        if not row:
            db.add(DeductionType(code=code, name=name, active=True))
        else:
            row.name = name
            row.active = True
    for code, name, points in (
        ("STATEMENT", "声明", Decimal("1.00")),
        ("MEMO", "备忘录", Decimal("3.00")),
        ("WARNING_1", "一级警告", Decimal("5.00")),
        ("WARNING_2", "二级警告", Decimal("10.00")),
    ):
        if not db.query(DeductionLevel).filter(DeductionLevel.code == code).first():
            db.add(DeductionLevel(code=code, name=name, points=points, active=True))
    db.commit()
