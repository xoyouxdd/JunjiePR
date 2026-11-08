"""Assignment validity and narrowly scoped recovery of orphaned upgrade tasks."""
from app.deduction_policy import UPGRADE_REVIEWER_CODES
from app.v2_models import Employee, UserAccount
from app.v2_services import role_at


def reviewer_available(db, employee_id):
    employee = db.get(Employee, employee_id)
    role = role_at(db, employee_id) if employee and employee.is_active else None
    return bool(role and role.code in UPGRADE_REVIEWER_CODES
                and db.query(UserAccount).filter_by(employee_id=employee_id, enabled=True).first())


def can_recover(db, user, work, second):
    return bool(second and ("SYSTEM_ADMIN" in user.permissions or
                ({"DEDUCTION_DIRECT","DEDUCTION_ALL"} & user.permissions
                 and user.id in {work.submitted_by, second.submitter_id})))


def can_recover_legacy(user, second):
    return bool("SYSTEM_ADMIN" in user.permissions or
                ({"DEDUCTION_DIRECT","DEDUCTION_ALL"} & user.permissions
                 and user.id in {second.submitter_id, second.material_uploaded_by}))
