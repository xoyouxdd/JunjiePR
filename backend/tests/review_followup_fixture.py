"""Fictional signing cards for follow-up regression and local preview."""
from datetime import datetime, timedelta

from app.database.connection import SessionLocal
from app.v2_models import Employee, GroupLeaderAssignment, RecognitionRecord, RecognitionType, WorkGroup


def seed_review_followup_cases(now: datetime) -> dict[str, int]:
    with SessionLocal() as db:
        people = {person.employee_no: person for person in db.query(Employee)}
        supervisor = people["SUPTEST01"]
        fallback = people["TAGSMTEST01"]
        group = db.query(WorkGroup).filter_by(name="热力追踪B组").one()
        db.add(GroupLeaderAssignment(
            group_id=group.id, leader_employee_id=fallback.id, starts_on=now.date().isoformat(),
            status="active", leader_type="formal",
        ))
        archived = Employee(
            employee_no="REVIEW-DEMO-OLD", name="样例归档员工", attraction_id=supervisor.attraction_id,
            is_active=False, account_deleted_at=now, hired_on=(now.date() - timedelta(days=90)).isoformat(),
        )
        db.add(archived)
        db.flush()
        kind = db.query(RecognitionType).filter_by(code="SAFETY").one()
        cases = {
            "direct_old": (people["CMTEST02"], supervisor, 230, "CM", None),
            "direct_late": (people["CMTEST02"], supervisor, 75, "CM", None),
            "direct_recent": (people["CMTEST02"], supervisor, 20, "CM", None),
            "acting_late": (people["CMTEST01"], people["TATEST01"], 60, "CM", None),
            "acting_old": (people["TRTEST01"], people["TATEST01"], 180, "TR", None),
            "shared": (supervisor, None, 65, "SUPERVISOR", None),
            "shared_acting": (fallback, None, 195, "SUPERVISOR", "TA_GSM"),
            "unassigned": (archived, None, 12, "CM", None),
        }
        result = {}
        for key, (person, reviewer, age, role, duty) in cases.items():
            submitted = now - timedelta(hours=age)
            record = RecognitionRecord(
                employee_id=person.id, employee_no=person.employee_no, employee_name=person.name,
                employee_role_snapshot=role, employee_role_code_snapshot=role, employee_acting_duty_code=duty,
                home_attraction_id=person.attraction_id, home_attraction_name="热力追踪",
                occurred_attraction_id=person.attraction_id,
                recognition_date=submitted.date().isoformat(), recognition_month=submitted.strftime("%Y-%m"),
                recognition_type_id=kind.id, recognition_type_name=kind.name,
                content="本地预览用合成签卡", recognizer_employee_id=supervisor.id,
                recognizer_name=supervisor.name, recognizer_role_snapshot="主管", operator_employee_id=person.id,
                operator_name=person.name, source="self", fraction=1, submitted_at=submitted,
                assigned_reviewer_id=reviewer.id if reviewer else None,
            )
            db.add(record)
            db.flush()
            result[key] = record.id
        db.commit()
    return result
