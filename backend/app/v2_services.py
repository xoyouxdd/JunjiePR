"""Compatibility exports for services now owned by explicit business domains.

New domain modules import each other directly and never import this facade.
Legacy routes/tests/scripts may keep their imports while migrating gradually.
The attachment wrappers preserve FILE_DIR injection at this original boundary.
"""
from __future__ import annotations

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.v2_database import FILE_DIR
from app.v2_models import StoredFile
from app.services import attachments as _attachments

from app.role_constants import (
    FRONTLINE_CODES,
    LEADER_CODES,
    GSM_CODES,
    RECOGNIZER_CODES,
    SENIOR_RECOGNIZER_CODES,
    ACTING_TA_GSM_RECOGNIZER_CODES,
    RECOGNIZER_CIRCLE_ORDER,
    RECOGNIZER_ROLE_ORDER,
    RECOGNIZER_ELIGIBILITY_START,
    SCORE_UNIT,
    DUTY_ROLE_CODES,
    DUTY_BASE_CODES,
    SCORING_CATEGORY_BY_CODE,
    SCORED_BASE_CODES,
    SUPERVISOR_SCORING_START_MONTH,
)
from app.services.identity import (
    _date_value,
    scoring_category,
    duties_at,
    duties_at_bulk,
    acting_duty_periods,
    ACTING_NOTE_NAMES,
    acting_period_notes,
    acting_duty_summary,
    duty_code_at,
    role_at,
    roles_at,
    can_lead_on,
    identity_labels,
    identity_label,
    base_role_at,
    base_roles_at,
    role_permissions,
    active_frontline_employees,
    recognizer_role_for_date,
    earliest_roles,
    recognizer_options,
    recognition_score_for_role,
    employee_active_on,
    employed_on,
)
from app.services.organization import (
    gsm_candidates_for_attractions_bulk,
    ACTING_LEADER_FIRST,
    GROUP_SUPERVISOR_BASE_CODES,
    FORMAL_LEADER_BASE_CODES,
    group_code_for_index,
    group_code_index,
    group_code_sort_key,
    group_display_name,
    next_group_code,
    resequence_group_codes,
    group_leader_label,
    group_supervisor_eligible,
    group_acting_eligible,
    _active_leader_query,
    active_group_leader,
    group_leader_of_type,
    formal_leader_eligible,
    groups_assigned_to,
    groups_formally_led_by,
    active_group_memberships,
    groups_led_by,
    groups_led_by_bulk,
    active_group_leaders_bulk,
    active_group_memberships_bulk,
    direct_member_ids,
    current_group_for_employee,
    current_leader_for_employee,
    sync_pending_reviewers,
    group_leader_names,
    managed_attraction_ids,
)
from app.services.attendance import (
    attendance_scored_role,
    HALF_DAY_DEDUCTION,
    month_end,
    month_bounds,
    loa_periods_for_month,
    loa_excludes_month,
    full_month_loa,
    _sick_leave_date_values,
    attendance_day_totals,
    sick_leave_score_deduction,
    recalculate_attendance,
    ensure_month_attendance,
)
from app.services.audit import (
    AUDIT_EMPLOYEE_ENTITY_TYPES,
    audit_attraction_id,
    write_audit,
)
from app.services.role_lifecycle import (
    create_alert,
    process_role_expirations,
    resolve_acting_duty_migration_alerts,
)
from app.services.attachments import (
    BUSINESS_ATTACHMENT_EFFECTIVE_DATE,
    BUSINESS_ATTACHMENT_MAX_BYTES,
    UPLOAD_CHUNK_BYTES,
    _stream_upload_to_path,
    detect_image_type,
)


async def save_upload(
    db: Session,
    upload: UploadFile,
    uploader_id: int,
    *,
    allowed_extensions: set[str],
    max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES,
) -> StoredFile:
    return await _attachments.save_upload(
        db, upload, uploader_id, file_dir=FILE_DIR,
        allowed_extensions=allowed_extensions, max_bytes=max_bytes,
    )


async def save_image_upload(
    db: Session,
    upload: UploadFile,
    uploader_id: int,
    *,
    max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES,
    persist: bool = True,
) -> StoredFile:
    return await _attachments.save_image_upload(
        db, upload, uploader_id, file_dir=FILE_DIR,
        max_bytes=max_bytes, persist=persist,
    )


def remove_upload_file(file_row: StoredFile | None) -> None:
    _attachments.remove_upload_file(file_row, file_dir=FILE_DIR)
