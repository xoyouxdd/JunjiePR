"""Assignment validity and narrowly scoped recovery of orphaned upgrade tasks."""
from app.deduction_policy import statement_upgrade_reviewer


def reviewer_available(db, employee_id):
    return statement_upgrade_reviewer(db, employee_id) is not None


def upgrade_waits_for_reviewer(work, second):
    return bool(second and work.status == "awaiting_material"
                and second.material_status == "ready" and second.upgrade_state == "awaiting_reviewer")


def upgrade_work_status_name(work, second):
    if upgrade_waits_for_reviewer(work, second):
        return "材料已就绪 · 待重新指派"
    return {"awaiting_material": "待材料就绪", "pending": "待审核",
            "approved": "已升级", "rejected": "审核不通过"}.get(work.status, work.status)


def can_recover(db, user, work, second):
    if not second:
        return False
    owns_submission = user.id in {work.submitted_by, second.submitter_id}
    can_manage_materials = bool({"DEDUCTION_DIRECT", "DEDUCTION_ALL"} & user.permissions)
    if user.role.code == "TA_GSM":
        return work.status == "awaiting_material" and owns_submission and can_manage_materials
    return "SYSTEM_ADMIN" in user.permissions or (owns_submission and can_manage_materials)


def can_recover_legacy(user, second):
    return bool("SYSTEM_ADMIN" in user.permissions or
                ({"DEDUCTION_DIRECT","DEDUCTION_ALL"} & user.permissions
                 and user.id in {second.submitter_id, second.material_uploaded_by}))
