"""Isolated monthly close/API regressions; never uses a local or online database."""
import os
import tempfile
from datetime import datetime
from decimal import Decimal

# Other application modules can initialize their configured database during
# collection; always point that configuration at this module's disposable data.
os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="recognition-self-ranks-")
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base, get_db
from app.database.reference_data import seed_reference_data
from app.routers import governance, statistics
from app.services.monthly_ranks import (
    backfill_closed_rankings, build_month_comment, capture_month_ranks, member_month_query_context, member_month_rank, rank_band,
    sealed_self_score,
)
from app.v2_auth import V2User, current_user
from app.v2_models import (
    Attraction, AttendanceMonthlyScore, Employee, EmployeeActingDuty,
    EmployeeLOAPeriod, EmployeeMonthOrganizationSnapshot, EmployeeMonthRankSnapshot,
    EmployeeRoleAssignment, GroupLeaderAssignment, GroupMembership, MonthClosure, MonthRankGeneration, RecognitionRecord, RecognitionType, Role, WorkGroup,
)


MONTH = "2026-10"


@pytest.fixture
def system(monkeypatch):
    from datetime import date
    class Clock(date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 1)
    monkeypatch.setattr("app.month_closure.date", Clock)
    monkeypatch.setenv("RECOGNITION_ENABLE_MONTHLY_RANKS", "1")
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    seed_reference_data(db)
    circles = [Attraction(name=f"段位测试圈{i}", active=True, employee_circle=True) for i in (1, 2)]
    db.add_all(circles)
    db.flush()
    roles = {role.code: role for role in db.query(Role).all()}

    def employee(name, role="CM", circle=0, points=None):
        person = Employee(employee_no=name, name=name, attraction_id=circles[circle].id,
                          is_active=True, hired_on="2026-01-01")
        db.add(person)
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=person.id, role_id=roles[role].id,
                                      starts_on="2026-01-01", status="active"))
        if points is not None:
            db.add(AttendanceMonthlyScore(employee_id=person.id, attendance_month=MONTH,
                                          eligible=True, final_score=Decimal(str(points))))
        db.commit()
        return person

    admin = employee("admin", "SYSTEM_ADMIN")
    holder = {"user": V2User(admin, None, roles["SYSTEM_ADMIN"], {"SYSTEM_ADMIN"})}

    def switch(person, base="CM", duty=None):
        holder["user"] = V2User(person, None, roles[duty or base], set(), base_role=roles[base])

    def session_dependency():
        try:
            yield db
        finally:
            db.rollback()

    api = FastAPI()
    api.include_router(governance.router, prefix="/api")
    api.include_router(statistics.router, prefix="/api")
    api.dependency_overrides[get_db] = session_dependency
    api.dependency_overrides[current_user] = lambda: holder["user"]
    with TestClient(api, raise_server_exceptions=False) as client:
        yield db, client, circles, roles, employee, switch, holder, admin
    db.close()
    engine.dispose()


@pytest.mark.parametrize("threshold", range(10, 100, 10))
def test_strict_percentile_boundary_without_rounding(threshold):
    assert rank_band(2 * threshold - 1, 200, 2) == threshold - 10
    assert rank_band(2 * threshold, 200, 2) == threshold


def test_midrank_ties_small_population_and_same_score():
    assert rank_band(0, 10, 8) == 30
    assert rank_band(8, 10) == 80
    assert rank_band(9, 10) == 90
    assert rank_band(0, 10, 10) is None
    assert rank_band(8, 9) is None
    assert rank_band(0, 1) is None


def close(client, circle_id, month=MONTH):
    response = client.post(f"/api/month-closes/{month}/close",
                           json={"attraction_id": circle_id, "reason": "段位隔离验证"})
    assert response.status_code == 200, response.text


def test_close_freezes_separate_populations_and_self_only_api(system):
    db, client, circles, roles, make, switch, _, _ = system
    people = [make(f"cm{i}", "CM" if i % 2 else "TR", points=i) for i in range(11)]
    supervisors = [make("sup0", "SUPERVISOR", points=0), make("sup1", "SUPERVISOR", points=100)]
    foreign = make("foreign", circle=1, points=300)
    db.add(EmployeeActingDuty(employee_id=people[9].id, role_id=roles["TA_SUPERVISOR"].id,
                              starts_on="2026-10-01", status="active"))
    db.commit()
    close(client, circles[0].id)
    own = sealed_self_score(db, MONTH, people[9].id)
    assert (own.cohort_size, own.lower_count, own.tier) == (11, 9, 80)
    assert sealed_self_score(db, MONTH, supervisors[1].id).cohort_size == 2
    assert sealed_self_score(db, MONTH, foreign.id) is None
    switch(people[9], duty="TA_SUPERVISOR")
    result = client.get("/api/dashboard", params={"month": MONTH, "employee_id": supervisors[1].id}).json()
    private = client.get("/api/self-month-rank", params={"month": MONTH, "employee_id": supervisors[1].id})
    assert private.headers["cache-control"] == "private, no-store" and private.headers["vary"] == "Cookie"
    rank = private.json()
    assert "month_rank" not in result
    assert result["monthly_rank_available"] is True
    assert rank["label"] == "傲世宗师"
    assert rank["population_label"] == "CM/TR"
    assert result["total_score"] == 9
    assert not ({"percentile", "lower_count", "equal_count", "cohort_size", "rank", "employee_id"} & rank.keys())
    # Moving, disabling a peer, changing base identity or source attendance
    # after close cannot change this sealed score or the comparison population.
    people[9].attraction_id = circles[1].id
    people[0].is_active = False
    db.query(AttendanceMonthlyScore).filter_by(employee_id=people[9].id).one().final_score = 999
    db.commit()
    switch(people[9], base="SUPERVISOR")
    again = client.get("/api/dashboard", params={"month": MONTH}).json()
    assert client.get("/api/self-month-rank", params={"month": MONTH}).json() == rank
    assert again["total_score"] == 9
    assert again["month_organization"]["attraction_name"] == circles[0].name
    assert again["month_organization"]["role_label"] == "CM · 代理TA主管"
    current = client.get("/api/dashboard", params={"month": "2026-11"}).json()
    assert current["month"] == "2026-11" and "month_rank" not in current
    assert current["month_closed"] is False


def test_zero_input_people_ties_loa_and_insufficient_population(system):
    db, client, circles, _, make, switch, _, _ = system
    a, b, loa = make("zeroA"), make("zeroB"), make("loa")
    db.add(EmployeeLOAPeriod(employee_id=loa.id, starts_on="2026-10-05", status="active",
                            created_by=system[-1].id, created_by_name="admin"))
    db.commit()
    close(client, circles[0].id)
    assert db.query(AttendanceMonthlyScore).filter(AttendanceMonthlyScore.employee_id.in_([a.id, b.id])).count() == 2
    for person in (a, b):
        snapshot = sealed_self_score(db, MONTH, person.id)
        assert (snapshot.cohort_size, snapshot.lower_count, snapshot.tier) == (2, 0, None)
    switch(loa)
    assert client.get("/api/self-month-rank", params={"month": MONTH}).json()["status"] == "excluded"
    assert sealed_self_score(db, MONTH, loa.id).total_score == 0
    later = member_month_rank(db, "2026-11", loa)
    assert later["status"] == "pending" and later["month"] == "2026-11"
    assert "tier" not in later


def test_reopen_hides_old_band_and_reclose_replaces_it(system):
    db, client, circles, _, make, switch, holder, admin = system
    people = [make(f"re{i}", points=i) for i in range(11)]
    close(client, circles[0].id)
    assert sealed_self_score(db, MONTH, people[9].id).tier == 80
    response = client.post(f"/api/month-closes/{MONTH}/reopen",
                           json={"attraction_id": circles[0].id, "reason": "更正隔离验证"})
    assert response.status_code == 200, response.text
    assert sealed_self_score(db, MONTH, people[9].id) is None
    switch(people[9])
    result = client.get("/api/self-month-rank", params={"month": MONTH}).json()
    assert result["status"] == "reopened"
    assert "tier" not in result
    for person in people:
        db.query(AttendanceMonthlyScore).filter_by(employee_id=person.id, attendance_month=MONTH).one().final_score = (20 if person.id == people[9].id else 0)
    db.commit()
    holder["user"] = V2User(admin, None, db.query(Role).filter_by(code="SYSTEM_ADMIN").one(), {"SYSTEM_ADMIN"})
    close(client, circles[0].id)
    assert sealed_self_score(db, MONTH, people[9].id).tier == 90
    assert db.query(MonthRankGeneration).count() == 1
    assert db.query(EmployeeMonthRankSnapshot).count() == len(people)


def test_global_close_and_reopen_preserve_still_closed_circle(system):
    db, client, circles, _, make, _, _, _ = system
    a, b = make("globalA", points=12), make("globalB", points=0)
    other = make("globalOther", circle=1, points=12)
    close(client, circles[0].id)
    local = sealed_self_score(db, MONTH, a.id).generation_id
    close(client, None)
    assert sealed_self_score(db, MONTH, a.id).generation_id != local
    assert sealed_self_score(db, MONTH, other.id).status == "insufficient"
    reopened = client.post(f"/api/month-closes/{MONTH}/reopen",
                           json={"reason": "重新开放全局"})
    assert reopened.status_code == 200
    assert sealed_self_score(db, MONTH, a.id).generation_id == local
    assert member_month_rank(db, MONTH, other)["status"] == "reopened"


def test_close_failure_rolls_back_attendance_organization_and_rank_generation(system, monkeypatch):
    db, client, circles, _, make, _, _, _ = system
    person = make("rollback")
    monkeypatch.setattr(governance, "capture_month_ranks", lambda *args: (_ for _ in ()).throw(RuntimeError("模拟封存失败")))
    result = client.post(f"/api/month-closes/{MONTH}/close",
                         json={"attraction_id": circles[0].id, "reason": "回滚验证"})
    assert result.status_code == 500
    assert db.query(MonthClosure).count() == 0
    assert db.query(EmployeeMonthOrganizationSnapshot).count() == 0
    assert db.query(MonthRankGeneration).count() == 0
    assert db.query(AttendanceMonthlyScore).filter_by(employee_id=person.id).count() == 0


def test_legacy_backfill_uses_frozen_organization_and_is_idempotent(system):
    db, _, circles, _, make, _, _, _ = system
    people = [make("legacyA", points=12), *[make(f"legacyB{i}", points=0) for i in range(9)]]
    closure = MonthClosure(closure_month=MONTH, attraction_id=circles[0].id,
                           status="closed", closed_at=datetime(2026, 10, 31, 23, 0))
    db.add(closure)
    for person in people:
        db.add(EmployeeMonthOrganizationSnapshot(employee_id=person.id, score_month=MONTH,
               attraction_id=circles[0].id, attraction_name=circles[0].name, base_role_code="CM"))
        person.attraction_id = circles[1].id
    db.commit()
    before = [(r.id, r.final_score, r.calculated_at) for r in db.query(AttendanceMonthlyScore).all()]
    backfill_closed_rankings(db)
    first = sealed_self_score(db, MONTH, people[0].id)
    assert first.tier == 90 and first.attraction_id == circles[0].id
    backfill_closed_rankings(db)
    assert db.query(EmployeeMonthRankSnapshot).count() == 10
    assert before == [(r.id, r.final_score, r.calculated_at) for r in db.query(AttendanceMonthlyScore).all()]


@pytest.mark.parametrize("role", ["CM", "SUPERVISOR"])
def test_legacy_backfill_orders_months_before_generating_score_change_comments(system, role):
    db, _, circles, _, make, _, _, _ = system
    person = make("legacy-comment-person", role, points=10)
    peers = [make(f"legacy-comment-peer{i}", role, points=0) for i in range(9)]
    for item, value in [(person, 30), *[(peer, 0) for peer in peers]]:
        db.add(AttendanceMonthlyScore(employee_id=item.id, attendance_month="2026-11", eligible=True, final_score=value))
    # Older systems may store closure rows in a different order from score months.
    for month, closed_at in (("2026-11", datetime(2026, 11, 30, 23)), (MONTH, datetime(2026, 10, 31, 23))):
        db.add(MonthClosure(closure_month=month, attraction_id=circles[0].id, status="closed", closed_at=closed_at))
        for item in (person, *peers):
            db.add(EmployeeMonthOrganizationSnapshot(employee_id=item.id, score_month=month,
                attraction_id=circles[0].id, attraction_name=circles[0].name, base_role_code=role))
    db.commit()
    backfill_closed_rankings(db)
    message = member_month_rank(db, "2026-11", person)["message"]
    assert "比上月增加20分" in message
    backfill_closed_rankings(db)
    assert member_month_rank(db, "2026-11", person)["message"] == message


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "malformed"])
def test_dashboard_rejects_invalid_month(system, month):
    _, client, _, _, make, switch, _, _ = system
    switch(make("invalid-month"))
    assert client.get("/api/dashboard", params={"month": month}).status_code == 400


def test_legacy_missing_organization_never_invents_a_band(system):
    db, _, circles, _, make, _, _, _ = system
    person = make("missing-history", points=12)
    db.add(MonthClosure(closure_month=MONTH, attraction_id=circles[0].id,
                       status="closed", closed_at=datetime(2026, 10, 31, 23, 0)))
    db.commit()
    backfill_closed_rankings(db)
    assert member_month_rank(db, MONTH, person)["status"] == "unavailable"
    assert db.query(EmployeeMonthRankSnapshot).count() == 0


def test_query_months_include_only_own_history_and_frozen_circle_status(system):
    db, client, circles, _, make, switch, _, _ = system
    person, other = make("history-owner"), make("history-other")
    db.add_all([
        AttendanceMonthlyScore(employee_id=person.id, attendance_month="2025-12", final_score=0),
        AttendanceMonthlyScore(employee_id=other.id, attendance_month="2025-11", final_score=0),
        EmployeeMonthOrganizationSnapshot(employee_id=person.id, score_month="2025-12",
            attraction_id=circles[0].id, attraction_name="原景点圈", base_role_code="TR",
            group_name="原小组", leader_name="原主管"),
        MonthClosure(closure_month="2025-12", attraction_id=circles[0].id, status="closed"),
    ])
    person.attraction_id = circles[1].id
    db.commit()
    switch(person)
    context = client.get("/api/dashboard", params={"month": "2025-12"}).json()
    months = {item["month"]: item for item in context["query_months"]}
    assert "2025-11" not in months
    assert months["2025-12"]["status"] == "closed"
    assert context["month_organization"] == {
        "attraction_name": "原景点圈", "base_role_code": "TR", "role_label": "TR",
        "group_name": "原小组", "leader_name": "原主管",
    }
    assert context["month_closed"] is True
    assert client.get("/api/self-month-rank", params={"month": "2025-12"}).json()["status"] == "unavailable"


def test_query_status_matches_effective_global_or_circle_close(system):
    db, _, circles, _, make, _, _, _ = system
    person = make("status-owner")
    local = MonthClosure(closure_month=MONTH, attraction_id=circles[0].id,
        status="closed", closed_at=datetime(2026, 10, 14))
    global_scope = MonthClosure(closure_month=MONTH, attraction_id=None,
        status="open", reopened_at=datetime(2026, 10, 14))
    db.add_all([local, global_scope])
    db.commit()

    def status():
        context = member_month_query_context(db, MONTH, person)
        return next(item["status"] for item in context["query_months"] if item["month"] == MONTH)

    assert status() == "closed"
    local.status = "open"
    local.reopened_at = datetime(2026, 10, 14)
    db.commit()
    assert status() == "reopened"
    global_scope.status = "closed"
    db.commit()
    assert status() == "closed"
    assert member_month_rank(db, MONTH, person)["month_closed"] is True


def test_supervisor_and_ta_gsm_compare_all_circles_only_after_all_close(system):
    db, client, circles, roles, make, switch, _, _ = system
    a = make("cross-sup", "SUPERVISOR", points=30)
    b = make("cross-ta-gsm", "SUPERVISOR", circle=1, points=10)
    for i in range(8):
        make(f"cross-peer{i}", "SUPERVISOR", points=20)
    x, y = make("cross-cm-high", points=100), make("cross-cm-low", points=0)
    for i in range(8):
        make(f"cross-cm-peer{i}", points=10)
    db.add(EmployeeActingDuty(employee_id=b.id, role_id=roles["TA_GSM"].id,
                             starts_on="2026-10-01", status="active"))
    db.commit()
    close(client, circles[0].id)
    waiting = member_month_rank(db, MONTH, a)
    assert waiting["status"] == "waiting_global" and "tier" not in waiting
    assert member_month_rank(db, MONTH, x)["status"] == "ranked"
    close(client, circles[1].id)
    for person, tier in ((a, 90), (b, 0)):
        rank = member_month_rank(db, MONTH, person)
        assert (rank["status"], rank["tier"], rank["comparison_label"]) == ("ranked", tier, "全部景点圈")
        assert sealed_self_score(db, MONTH, person.id).cohort_size == 10
    switch(b, base="SUPERVISOR", duty="TA_GSM")
    rank = client.get("/api/self-month-rank", params={"month": MONTH}).json()
    assert rank["population_label"] == "主管"
    assert rank["label"] == "启航新星"
    assert client.get("/api/self-month-rank", params={"month": "2026-09"}).json()["status"] == "unavailable"


def test_reopening_one_circle_invalidates_all_supervisor_bands_only(system):
    db, client, circles, _, make, _, _, _ = system
    a = make("reopen-sup-a", "SUPERVISOR", points=30)
    b = make("reopen-sup-b", "SUPERVISOR", circle=1, points=10)
    for i in range(8):
        make(f"reopen-sup-peer{i}", "SUPERVISOR", points=35)
    x, y = make("reopen-cm-a", points=20), make("reopen-cm-b", points=0)
    close(client, circles[0].id)
    close(client, circles[1].id)
    original = member_month_rank(db, MONTH, x)
    assert member_month_rank(db, MONTH, a)["tier"] == 10
    reopened = client.post(f"/api/month-closes/{MONTH}/reopen", json={"attraction_id": circles[1].id, "reason": "跨圈复核"})
    assert reopened.status_code == 200
    assert member_month_rank(db, MONTH, a)["status"] == "reopened"
    assert "tier" not in member_month_rank(db, MONTH, a)
    assert member_month_rank(db, MONTH, x) == original
    db.query(AttendanceMonthlyScore).filter_by(employee_id=b.id, attendance_month=MONTH).one().final_score = 40
    db.commit()
    close(client, circles[1].id)
    assert member_month_rank(db, MONTH, a)["tier"] == 0
    assert member_month_rank(db, MONTH, b)["tier"] == 90


@pytest.mark.parametrize("before,after,expected", [(10, 30, "增加20分"), (30, 10, "减少20分"), (10, 10, "与上月持平")])
def test_seasonal_comments_use_consecutive_sealed_scores_and_stay_frozen(system, before, after, expected):
    db, client, circles, _, make, _, _, _ = system
    person = make("comment-person", points=after)
    peers = [make(f"comment-peer{i}", points=0) for i in range(9)]
    for item, value in [(person, before), *[(peer, 0) for peer in peers]]:
        db.add(AttendanceMonthlyScore(employee_id=item.id, attendance_month="2026-09", eligible=True, final_score=value))
    db.commit()
    close(client, circles[0].id, "2026-09")
    close(client, circles[0].id)
    message = member_month_rank(db, MONTH, person)["message"]
    assert message.startswith("秋意渐浓") and expected in message
    sealed_self_score(db, "2026-09", person.id).total_score = 999
    db.commit()
    backfill_closed_rankings(db)
    assert member_month_rank(db, MONTH, person)["message"] == message


def test_formal_and_acting_leaders_keep_records_but_never_see_member_bands(system):
    db, client, circles, roles, make, switch, holder, _ = system
    leader = make("band-formal", "SUPERVISOR", points=12)
    acting, member, outsider = make("band-acting", points=5), make("band-member", points=10), make("band-outsider", circle=1, points=20)
    db.add(EmployeeActingDuty(employee_id=acting.id, role_id=roles["TA_SUPERVISOR"].id, starts_on="2026-01-01", status="active"))
    group = WorkGroup(name="段位成员组", attraction_id=circles[0].id)
    db.add(group)
    db.flush()
    db.add_all([
        GroupLeaderAssignment(group_id=group.id, leader_employee_id=leader.id, starts_on="2026-01-01", leader_type="formal"),
        GroupLeaderAssignment(group_id=group.id, leader_employee_id=acting.id, starts_on="2026-01-01", leader_type="acting"),
        GroupMembership(group_id=group.id, employee_id=acting.id, starts_on="2026-01-01"),
        GroupMembership(group_id=group.id, employee_id=member.id, starts_on="2026-01-01"),
    ])
    for person, value in ((acting, 5), (member, 10)):
        db.add(AttendanceMonthlyScore(employee_id=person.id, attendance_month="2026-09", eligible=True, final_score=value))
    db.commit()
    close(client, circles[0].id, "2026-09")
    for person, base, duty in ((leader, "SUPERVISOR", None), (acting, "CM", "TA_SUPERVISOR")):
        switch(person, base=base, duty=duty)
        holder["user"].permissions.add("MEMBER_RECORDS")
        result = client.get("/api/member-score-summary", params={"month": "2026-09", "employee_id": outsider.id}).json()
        rows = {row["employee_id"]: row for row in result["rows"]}
        assert member.id in rows and outsider.id not in rows
        assert all("month_rank" not in row for row in rows.values())
    db.query(GroupMembership).filter_by(employee_id=member.id).one().status = "cancelled"
    db.commit()
    assert member.id not in {row["employee_id"] for row in client.get("/api/member-score-summary", params={"month": "2026-09"}).json()["rows"]}
    switch(outsider)
    assert client.get("/api/member-score-summary", params={"month": "2026-09"}).status_code == 403


def test_assigned_leader_without_record_permission_cannot_access_member_bands(system):
    db, client, circles, _, make, switch, _, _ = system
    leader = make("band-only-leader", "GSM")
    member, peer = make("band-only-member", points=10), make("band-only-peer", points=0)
    group = WorkGroup(name="只看段位组", attraction_id=circles[0].id)
    db.add(group)
    db.flush()
    db.add_all([
        GroupLeaderAssignment(group_id=group.id, leader_employee_id=leader.id, starts_on="2026-01-01"),
        GroupMembership(group_id=group.id, employee_id=member.id, starts_on="2026-01-01"),
    ])
    db.commit()
    close(client, circles[0].id)
    switch(leader, base="GSM")
    assert client.get("/api/member-score-summary", params={"month": MONTH}).status_code == 403
    assert client.get("/api/member-records", params={"month": MONTH}).status_code == 403
    db.query(GroupLeaderAssignment).filter_by(leader_employee_id=leader.id).one().status = "cancelled"
    db.commit()
    assert client.get("/api/member-score-summary", params={"month": MONTH}).status_code == 403


@pytest.mark.parametrize("change", ["circle", "identity", "missing_month", "reopened_previous"])
def test_comments_do_not_compare_incompatible_or_unsealed_months(system, change):
    db, client, circles, _, make, _, _, _ = system
    person = make("no-false-change", points=30)
    peers = [make(f"no-false-change-peer{i}", points=0) for i in range(9)]
    for item, value in [(person, 10), *[(peer, 0) for peer in peers]]:
        db.add(AttendanceMonthlyScore(employee_id=item.id, attendance_month="2026-09", eligible=True, final_score=value))
    db.commit()
    close(client, circles[0].id, "2026-09")
    close(client, circles[0].id)
    snapshot = sealed_self_score(db, MONTH, person.id)
    if change == "circle":
        snapshot.attraction_id = circles[1].id
    elif change == "identity":
        snapshot.base_role_code = "TR"
    elif change == "missing_month":
        snapshot.score_month = "2026-12"
    else:
        assert client.post("/api/month-closes/2026-09/reopen", json={"attraction_id": circles[0].id, "reason": "更正上月"}).status_code == 200
    message = build_month_comment(db, snapshot)
    assert "增加" not in message and "减少" not in message and "持平" not in message


def test_comment_schema_upgrade_preserves_rows_and_is_idempotent():
    from sqlalchemy import text
    from app.database.migrations.schema import ensure_month_rank_comment_column
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE employee_month_rank_snapshots (id INTEGER PRIMARY KEY, total_score NUMERIC)"))
        connection.execute(text("INSERT INTO employee_month_rank_snapshots VALUES (1, 12.5)"))
        ensure_month_rank_comment_column(connection)
        ensure_month_rank_comment_column(connection)
        assert connection.execute(text("SELECT id, total_score, comment_text, comparison_scope_json FROM employee_month_rank_snapshots")).one() == (1, 12.5, "", "")
    engine.dispose()


def test_recognition_summary_excludes_pending_rejected_and_zero_credit(system):
    db, client, circles, _, make, switch, _, admin = system
    person = make("summary-person")
    kind = db.query(RecognitionType).first()
    for status, credit in (("confirmed", 1.5), ("confirmed", 0), ("pending", 1.5), ("rejected", 1.5), ("void", 1.5)):
        db.add(RecognitionRecord(employee_id=person.id, employee_no=person.employee_no, employee_name=person.name,
            employee_role_snapshot="CM", home_attraction_id=circles[0].id, occurred_attraction_id=circles[0].id,
            recognition_date="2026-10-01", recognition_month=MONTH, recognition_type_id=kind.id,
            recognition_type_name=kind.name, content="汇总测试", recognizer_employee_id=admin.id,
            recognizer_name=admin.name, recognizer_role_snapshot="主管", operator_employee_id=person.id,
            operator_name=person.name, source="self", fraction=1.5, credited_fraction=credit, status=status))
    db.commit()
    switch(person)
    result = client.get("/api/dashboard", params={"month": MONTH}).json()
    assert result["recognition_summary"] == {"credited": 1, "uncredited": 1, "pending": 1, "rejected": 1}
    db.add(EmployeeLOAPeriod(employee_id=person.id, starts_on="2026-10-01", status="active", created_by=admin.id, created_by_name=admin.name))
    db.commit()
    assert client.get("/api/dashboard", params={"month": MONTH}).json()["recognition_summary"] == {
        "credited": 0, "uncredited": 2, "pending": 1, "rejected": 1,
    }


@pytest.mark.parametrize("count,expected", [(9, "insufficient"), (10, "same_scores"), (20, "same_scores")])
def test_small_or_identical_cohorts_keep_scores_without_competitive_rank(system, count, expected):
    db, client, circles, _, make, switch, _, _ = system
    people = [make(f"neutral-{i}", points=12) for i in range(count)]
    close(client, circles[0].id)
    switch(people[0])
    result = client.get("/api/self-month-rank", params={"month": MONTH}).json()
    assert result["status"] == expected and "tier" not in result
    assert client.get("/api/dashboard", params={"month": MONTH}).json()["total_score"] == 12
    assert all(sealed_self_score(db, MONTH, person.id).tier is None for person in people)


def test_decimal_ties_negative_scores_and_skewed_distribution_remain_exact(system):
    db, client, circles, _, make, _, _, _ = system
    people = [make(f"decimal-{i}", points=value) for i, value in enumerate(
        [Decimal("0.10")] * 8 + [Decimal("0.11"), Decimal("0.12")])]
    close(client, circles[0].id)
    assert {sealed_self_score(db, MONTH, person.id).tier for person in people[:8]} == {30}
    assert sealed_self_score(db, MONTH, people[8].id).tier == 80
    assert sealed_self_score(db, MONTH, people[9].id).tier == 90
    row = sealed_self_score(db, MONTH, people[0].id)
    assert (row.lower_count, row.equal_count, row.cohort_size, row.rule_version) == (0, 8, 10, "midrank-v2-min10")
    assert row.total_score == Decimal("0.10")
    # A negative total or a large outlier does not redistribute people or split ties.
    from bisect import bisect_left, bisect_right
    for values in ([Decimal("-3.25")] * 8 + [Decimal("-1.00"), Decimal("0.00")],
                   [Decimal("0.00")] * 8 + [Decimal("0.01"), Decimal("99999.99")]):
        assert [rank_band(bisect_left(values, value), len(values),
                         bisect_right(values, value) - bisect_left(values, value)) for value in values] == [30] * 8 + [80, 90]


def test_entry_is_available_by_default_but_results_require_personal_request(system, monkeypatch):
    _, client, _, _, make, switch, _, _ = system
    switch(make("switch-person", points=12))
    monkeypatch.delenv("RECOGNITION_ENABLE_MONTHLY_RANKS", raising=False)
    data = client.get("/api/dashboard", params={"month": MONTH}).json()
    assert data["monthly_rank_available"] is True and "month_rank" not in data
    assert client.get("/api/self-month-rank", params={"month": MONTH}).status_code == 200
    monkeypatch.setenv("RECOGNITION_ENABLE_MONTHLY_RANKS", "0")
    assert client.get("/api/self-month-rank", params={"month": MONTH}).status_code == 404
    data = client.get("/api/dashboard", params={"month": MONTH}).json()
    assert data["monthly_rank_available"] is False and "month_rank" not in data
    monkeypatch.setenv("RECOGNITION_ENABLE_MONTHLY_RANKS", "true")
    assert client.get("/api/self-month-rank", params={"month": MONTH}).status_code == 404
    monkeypatch.setenv("RECOGNITION_ENABLE_MONTHLY_RANKS", "1")
    assert client.get("/api/self-month-rank", params={"month": "2026-13"}).status_code == 400
    assert client.get("/api/self-month-rank", params={"month": "9999-12"}).status_code == 400
    switch(system[-1], base="SYSTEM_ADMIN")
    assert client.get("/api/self-month-rank", params={"month": MONTH}).status_code == 403


def test_old_rule_results_are_hidden_without_rewriting_existing_snapshots(system):
    db, client, circles, _, make, _, _, _ = system
    people = [make(f"old-rule-{i}", "SUPERVISOR", points=i) for i in range(10)]
    close(client, circles[0].id)
    for person in people:
        sealed_self_score(db, MONTH, person.id).rule_version = "strict-lower-v1"
    db.commit()
    before = [(r.id, r.tier, r.comment_text, r.rule_version) for r in db.query(EmployeeMonthRankSnapshot).all()]
    assert member_month_rank(db, MONTH, people[-1])["status"] == "unavailable"
    backfill_closed_rankings(db)
    assert before == [(r.id, r.tier, r.comment_text, r.rule_version) for r in db.query(EmployeeMonthRankSnapshot).all()]


def test_rank_policy_migration_preserves_existing_rows():
    from sqlalchemy import text
    from app.database.migrations.schema import ensure_month_rank_rule_columns
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE employee_month_rank_snapshots (id INTEGER PRIMARY KEY, total_score NUMERIC)"))
        connection.execute(text("INSERT INTO employee_month_rank_snapshots VALUES (1, 12.5)"))
        ensure_month_rank_rule_columns(connection)
        ensure_month_rank_rule_columns(connection)
        assert connection.execute(text("SELECT id, total_score, equal_count, rule_version FROM employee_month_rank_snapshots")).one() == (1, 12.5, 0, "strict-lower-v1")
    engine.dispose()
