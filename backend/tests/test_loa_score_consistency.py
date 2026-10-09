"""Cross-entry performance regressions, using an isolated in-memory database."""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.v2_database import Base
from app.v2_auth import V2User
from app.v2_models import (
    Attraction, AttendanceMonthlyScore, AttendanceRule, DeductionLevel,
    DeductionRecord, DeductionType, Employee, EmployeeActingDuty,
    EmployeeLOAPeriod, EmployeeRoleAssignment, GroupLeaderAssignment,
    GroupMembership, RecognitionRecord, RecognitionType, Role, StoredFile, WorkGroup,
)
from app.score_queries import employee_month_scores
from app.routers import statistics
from app.v2_services import acting_period_notes


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        session.add_all([
            Role(id=1, code="CM", name="CM", rank=10),
            Role(id=2, code="GSM", name="GSM", rank=30),
            Role(id=3, code="SUPERVISOR", name="主管", rank=20),
            Role(id=4, code="TA_SUPERVISOR", name="TA主管", rank=20),
            Attraction(id=1, name="测试圈", employee_circle=True),
            Employee(id=1, employee_no="1000001", name="测试CM", attraction_id=1, hired_on="2020-01-01"),
            Employee(id=2, employee_no="1000002", name="测试GSM", attraction_id=1),
            Employee(id=3, employee_no="1000003", name="测试主管", attraction_id=1, hired_on="2020-01-01"),
            AttendanceRule(effective_date="2020-01-01"),
            RecognitionType(id=1, code="SAFETY", name="安全"),
            DeductionType(id=1, code="SAFETY", name="安全"),
            DeductionLevel(id=1, code="STATEMENT", name="声明", points=1),
            StoredFile(id=1, storage_key="probe", original_filename="probe", extension=".none", file_size=0, sha256="0" * 64, uploaded_by=2, status="not_required"),
            WorkGroup(id=1, name="测试圈A组", code="A", attraction_id=1),
            GroupLeaderAssignment(group_id=1, leader_employee_id=3, starts_on="2020-01-01", leader_type="formal"),
            GroupMembership(group_id=1, employee_id=1, starts_on="2020-01-01"),
        ])
        session.add_all([
            EmployeeRoleAssignment(employee_id=i, role_id=i, starts_on="2020-01-01")
            for i in (1, 2, 3)
        ])
        session.commit()
        yield session
    engine.dispose()


def user(db, employee_id):
    return V2User(db.get(Employee, employee_id), None, db.get(Role, employee_id), {"DATA_VIEW", "DATA_EXPORT", "MEMBER_RECORDS"})


def add_scores(db, month, employee_id=1):
    occurred_on = min(month + "-15", date.today().isoformat())
    employee = db.get(Employee, employee_id)
    db.add(RecognitionRecord(
        employee_id=employee_id, employee_no=employee.employee_no, employee_name=employee.name,
        employee_role_snapshot="CM" if employee_id == 1 else "主管",
        home_attraction_id=1, home_attraction_name="测试圈", occurred_attraction_id=1,
        recognition_date=occurred_on, recognition_month=month,
        recognition_type_id=1, recognition_type_name="安全", content="测试认可",
        recognizer_employee_id=2, recognizer_name="测试GSM", recognizer_role_snapshot="GSM",
        operator_employee_id=2, operator_name="测试GSM", source="direct", fraction=3, credited_fraction=3, status="confirmed",
    ))
    db.add(DeductionRecord(
        employee_id=employee_id, employee_no=employee.employee_no, employee_name=employee.name,
        employee_role_snapshot="CM" if employee_id == 1 else "主管", attraction_id_snapshot=1,
        deduction_type_id=1, deduction_type_name="安全", deduction_level_id=1, deduction_level_name="声明",
        points=1, occurred_on=occurred_on, deduction_month=month, description="测试扣分",
        document_file_id=1, submitter_id=2, submitter_name="测试GSM", submitter_role_snapshot="GSM", permission_scope_snapshot="测试", status="active",
    ))
    # Deliberately stale eligibility: the score policy must exclude even this.
    db.add(AttendanceMonthlyScore(employee_id=employee_id, attendance_month=month, eligible=True, final_score=12))
    db.commit()


def add_loa(db, starts_on, ends_on, status="ended", employee_id=1):
    db.add(EmployeeLOAPeriod(employee_id=employee_id, starts_on=starts_on, ends_on=ends_on, status=status, created_by=2, created_by_name="测试GSM"))
    db.commit()


@pytest.mark.parametrize("start,end,status,excluded", [
    ("2026-09-01", "2026-09-01", "ended", True),
    ("2026-09-30", "2026-10-01", "ended", True),
    ("2026-08-31", "2026-09-01", "ended", True),
    ("2026-08-31", None, "active", True),
    ("2026-09-01", "2026-09-30", "cancelled", False),
    ("2026-08-01", "2026-08-31", "ended", False),
    ("2026-10-01", None, "active", False),
])
def test_monthly_scores_apply_calendar_overlap_and_keep_sources(db, start, end, status, excluded):
    add_scores(db, "2026-09")
    add_loa(db, start, end, status)
    row = employee_month_scores(db, "2026-09", [1])[0]
    assert row["loa_excluded"] is excluded
    assert [row[k] for k in ("recognition_score", "attendance_score", "deduction_score", "total_score")] == ([0, 0, 0, 0] if excluded else [3, 12, 1, 14])
    assert db.query(RecognitionRecord).one().credited_fraction == 3
    assert db.query(DeductionRecord).one().points == 1


def test_home_members_statistics_and_details_agree_for_one_day_loa(db):
    add_scores(db, "2026-09")
    add_loa(db, "2026-09-20", "2026-09-20")
    home = statistics.dashboard("2026-09", db, user(db, 1))
    members = statistics.member_score_summary("2026-09", None, db, user(db, 3))
    stats = statistics.statistics_payload(db, "2026-09", 1, user=user(db, 2), include_hierarchy=False)
    details = statistics.statistics_details_payload(db, "2026-09", [1])["1"]
    assert home["total_score"] == members["rows"][0]["total_score"] == details["total_score"] == stats["loa_rows"][0]["total_score"] == 0
    assert home["category_scores"] == {"安全": 0}
    assert len(home["records"]) == 1
    assert all(not r["included"] for r in members["rows"][0]["details"]["all_records"])


def test_range_ranking_excludes_only_the_touched_month_and_keeps_type_rankings(db):
    for month in ("2026-08", "2026-09"):
        add_scores(db, month)
    add_loa(db, "2026-09-20", "2026-09-20")
    def ranking(category):
        return statistics.pr_ranking_payload(db, user(db, 2), "2026-08-01", "2026-09-30", category, None, "score", "", 1, 20, 1)["rows"][0]
    row = ranking("overall")
    assert [row[k] for k in ("recognition_score", "attendance_score", "deduction_score", "total_score")] == [3, 12, 1, 14]
    assert ranking("recognition")["score"] == 6
    assert ranking("deduction")["score"] == 2


def test_leap_month_excludes_last_day_but_not_the_following_month(db):
    add_scores(db, "2024-02")
    add_scores(db, "2024-03")
    add_loa(db, "2024-02-29", "2024-02-29")
    assert employee_month_scores(db, "2024-02", [1])[0]["total_score"] == 0
    assert employee_month_scores(db, "2024-03", [1])[0]["total_score"] == 14


def test_supervisor_and_acting_notes_use_the_same_month_exclusion(db):
    month = date.today().strftime("%Y-%m")
    day = date.today().isoformat()
    add_scores(db, month, employee_id=3)
    add_loa(db, day, day, employee_id=3)
    row = statistics.pr_ranking_payload(db, user(db, 2), month + "-01", day, "overall", None, "score", "", 1, 20, 1, population="supervisor")["rows"][0]
    assert row["total_score"] == 0
    add_scores(db, "2026-09")
    add_loa(db, "2026-09-20", "2026-09-20")
    db.add(EmployeeActingDuty(employee_id=1, role_id=4, starts_on="2026-09-01", ends_on="2026-09-30"))
    db.commit()
    assert acting_period_notes(db, [1], "2026-09-01", "2026-09-30")[1].endswith("得分 0.00")
