"""Stable migration exports; importing does not execute a patch."""

from .schema import (
    ensure_recognition_shared_evidence,
    ensure_void_audit_columns,
    ensure_credential_columns,
    ensure_account_status_password_timestamp,
    ensure_submission_payload_digest,
    ensure_login_account_archive_columns,
    ensure_recognition_same_day_duplicate_columns,
    ensure_deduction_statement_upgrade_columns,
    ensure_recognition_credit_cap_and_poc_columns,
    ensure_employee_number_history,
    ensure_deduction_photo_pdf_materials,
    ensure_material_job_claim_generation,
    ensure_collaborative_material_columns,
    ensure_attraction_dimensions,
    ensure_performance_indexes,
    ensure_second_audit_query_indexes,
    ensure_governance_case_indexes,
    backfill_audit_scope_and_remove_appeals,
)

from .sick_leave import (
    SICK_LEAVE_EXTRA_INDEX_STATEMENTS,
    ensure_sick_leave_import_columns,
    ensure_sick_leave_record_indexes,
    _foreign_key_violations,
    _rebuild_sick_leave_records,
)

from .organization import (
    ACTING_DUTY_BASE_CODES,
    ensure_acting_duty_columns_and_migrate,
    ensure_group_leader_types,
    legacy_group_display_name,
    group_name_sort_key,
    ensure_group_codes,
    resequence_all_group_codes,
    migrate_legacy_duty_assignments,
)

from .registry import MIGRATION_RUNNERS, SCHEMA_MIGRATION_STEPS, run_schema_migration_steps
