"""Shared detail assembly keeps scope/output while batching metadata lookups."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.v2_database import Base
from app.v2_models import (
    Attraction, AttendanceMonthlyScore, AttendanceRule, DeductionLevel,
    DeductionRecord, DeductionType, Employee, EmployeeLOAPeriod,
    EmployeeRoleAssignment, RecognitionAttachment, RecognitionRecord, RecognitionType, Role,
    SickLeaveRecord, StoredFile,
)
from app.services.performance_details import (
    load_monthly_records, member_score_summary_payload,
    statistics_details_payload,
)


MONTH = "2026-09"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        session.add_all([
            Role(id=1, code="CM", name="CM", rank=10),
            Attraction(id=1, name="测试圈", employee_circle=True),
            AttendanceRule(id=1, effective_date="2020-01-01"),
            RecognitionType(id=1, code="SAFETY", name="安全"),
            DeductionType(id=1, code="SAFETY", name="安全"),
            DeductionLevel(id=1, code="STATEMENT", name="声明", points=1),
        ])
        for employee_id in range(1, 7):
            number, name = f"100000{employee_id}", f"测试员工{employee_id}"
            session.add_all([
                Employee(id=employee_id, employee_no=number, name=name,
                         attraction_id=1, hired_on="2020-01-01"),
                EmployeeRoleAssignment(employee_id=employee_id, role_id=1,
                                       starts_on="2020-01-01"),
                StoredFile(id=employee_id, storage_key=f"proof-{employee_id}",
                           original_filename=f"proof-{employee_id}.jpg", extension=".jpg",
                           file_size=1, sha256=str(employee_id) * 64, uploaded_by=employee_id),
                RecognitionRecord(
                    id=employee_id, employee_id=employee_id, employee_no=number, employee_name=name,
                    employee_role_snapshot="CM", home_attraction_id=1,
                    occurred_attraction_id=1, recognition_date=f"{MONTH}-15",
                    recognition_month=MONTH, recognition_type_id=1,
                    recognition_type_name="安全", content="测试认可",
                    recognizer_employee_id=employee_id, recognizer_name=name,
                    recognizer_role_snapshot="CM", operator_employee_id=employee_id,
                    operator_name=name, source="direct", fraction=employee_id,
                    credited_fraction=employee_id, status="confirmed",
                ),
                RecognitionAttachment(recognition_id=employee_id, file_id=employee_id),
                DeductionRecord(
                    employee_id=employee_id, employee_no=number, employee_name=name,
                    employee_role_snapshot="CM", attraction_id_snapshot=1,
                    deduction_type_id=1, deduction_type_name="安全",
                    deduction_level_id=1, deduction_level_name="声明", points=1,
                    occurred_on=f"{MONTH}-16", deduction_month=MONTH,
                    description="测试扣分", document_file_id=employee_id,
                    submitter_id=employee_id, submitter_name=name,
                    submitter_role_snapshot="CM", permission_scope_snapshot="测试",
                ),
                SickLeaveRecord(
                    employee_id=employee_id, attendance_month=MONTH,
                    leave_start_date=f"{MONTH}-17", leave_end_date=f"{MONTH}-17",
                    leave_days=1, charged_days=1, proof_file_id=employee_id,
                    submitted_by=employee_id, submitted_by_name=name,
                ),
                AttendanceMonthlyScore(
                    employee_id=employee_id, attendance_month=MONTH, eligible=True,
                    base_score=10, sick_deduction=0.45, final_score=9.55,
                    calculated_at=datetime(2026, 9, 30, 12),
                ),
            ])
        session.commit()
        yield session
    engine.dispose()


def test_summary_and_statistics_details_share_employee_scoped_output(db):
    selected_ids = [3, 1, 2]
    summary = member_score_summary_payload(db, MONTH, selected_ids)
    details = statistics_details_payload(db, MONTH, selected_ids)
    assert [row["employee_id"] for row in summary["rows"]] == [3, 2, 1]
    assert set(details) == {"1", "2", "3"}
    for row in summary["rows"]:
        detail = details[str(row["employee_id"])]
        for key in ("employee_id", "employee_no", "employee_name", "role_name", "acting_note"):
            assert detail[key] == row[key]
        for key, value in row["details"].items():
            assert detail[key] == value
        assert detail["sick_leaves"][0]["proof_is_previewable"] is True
        assert detail["recognitions"][0]["image_is_previewable"] is True
        assert {record["record_type"] for record in detail["all_records"]} == {
            "recognition", "deduction", "sick_leave", "attendance",
        }
    filtered = member_score_summary_payload(db, MONTH, selected_ids, keyword="1000002")
    assert [row["employee_id"] for row in filtered["rows"]] == [2]
    records = load_monthly_records(db, MONTH, selected_ids)
    assert {row.employee_id for row in records.recognitions} == set(selected_ids)
    assert {row.employee_id for row in records.deductions} == set(selected_ids)
    assert {row.employee_id for row in records.sick_leaves} == set(selected_ids)
    empty = load_monthly_records(db, "2026-08", selected_ids)
    assert not empty.recognitions and not empty.deductions and not empty.sick_leaves


def test_shared_details_keep_loa_records_but_exclude_their_scores(db):
    db.add(EmployeeLOAPeriod(
        employee_id=2, starts_on=f"{MONTH}-20", ends_on=f"{MONTH}-20",
        status="ended", created_by=1, created_by_name="测试员工1",
    ))
    db.commit()
    summary = member_score_summary_payload(db, MONTH, [1, 2])
    loa_summary = next(row for row in summary["rows"] if row["employee_id"] == 2)
    loa_detail = statistics_details_payload(db, MONTH, [1, 2])["2"]
    assert loa_summary["total_score"] == loa_detail["total_score"] == 0
    assert loa_detail["loa_excluded"] is True
    assert loa_detail["all_records"] == loa_summary["details"]["all_records"]
    assert all(not record["included"] for record in loa_detail["all_records"])


def test_metadata_query_growth_is_bounded_when_the_employee_batch_grows(db):
    def counted(employee_ids):
        queries = []

        def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                queries.append(statement)

        # Fresh identity map avoids making the second run pass through ORM cache.
        db.expunge_all()
        event.listen(db.bind, "before_cursor_execute", capture)
        try:
            rows = statistics_details_payload(db, MONTH, employee_ids)
        finally:
            event.remove(db.bind, "before_cursor_execute", capture)
        assert len(rows) == len(employee_ids)
        assert all(row["sick_leaves"][0]["proof_is_previewable"] for row in rows.values())
        assert all(row["recognitions"][0]["image_is_previewable"] for row in rows.values())
        return len(queries)

    single = counted([1])
    multiple = counted([1, 2, 3, 4, 5])
    # Room for a bounded population-dependent query; per-employee proof and
    # identity queries would grow by many more requests for this batch.
    assert multiple <= single + 3, (single, multiple)
