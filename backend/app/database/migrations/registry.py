"""Ordered one-shot patches, committed and recorded under their original stable keys."""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.migrations.schema import (
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

from app.database.migrations.sick_leave import (
    ensure_sick_leave_import_columns,
    ensure_sick_leave_record_indexes,
)

from app.database.migrations.organization import (
    ensure_acting_duty_columns_and_migrate,
    ensure_group_leader_types,
    ensure_group_codes,
    resequence_all_group_codes,
)

# Execution uses explicit callables; legacy key/name metadata remains public.
MIGRATION_RUNNERS: tuple[tuple[str, Callable[[Session], None]], ...] = (
    ('2026-08-void-audit-columns', ensure_void_audit_columns),
    ('2026-08-credential-columns', ensure_credential_columns),
    ('2026-08-attraction-dimensions', ensure_attraction_dimensions),
    ('2026-08-performance-indexes', ensure_performance_indexes),
    ('2026-08-governance-case-indexes', ensure_governance_case_indexes),
    ('2026-08-recognition-same-day-duplicate', ensure_recognition_same_day_duplicate_columns),
    ('2026-08-deduction-statement-upgrade', ensure_deduction_statement_upgrade_columns),
    ('2026-09-recognition-credit-cap-and-poc', ensure_recognition_credit_cap_and_poc_columns),
    ('2026-08-employee-number-history', ensure_employee_number_history),
    ('2026-09-deduction-photo-pdf-materials', ensure_deduction_photo_pdf_materials),
    ('2026-09-collaborative-materials-and-violation-absence', ensure_collaborative_material_columns),
    ('2026-09-account-status-password-timestamp', ensure_account_status_password_timestamp),
    ('2026-09-login-account-archive', ensure_login_account_archive_columns),
    ('2026-09-submission-payload-digest', ensure_submission_payload_digest),
    ('2026-09-second-audit-query-indexes', ensure_second_audit_query_indexes),
    ('2026-09-material-job-claim-generation', ensure_material_job_claim_generation),
    ('2026-09-sick-leave-import', ensure_sick_leave_import_columns),
    ('2026-10-acting-duties', ensure_acting_duty_columns_and_migrate),
    ('2026-10-recognition-shared-evidence', ensure_recognition_shared_evidence),
    ('2026-10-audit-scope-and-appeal-removal', backfill_audit_scope_and_remove_appeals),
    ('2026-10-group-leader-types', ensure_group_leader_types),
    ('2026-10-group-codes', ensure_group_codes),
    ('2026-10-group-codes-resequence', resequence_all_group_codes),
    ('2026-09-sick-leave-index-repair', ensure_sick_leave_record_indexes),
)
SCHEMA_MIGRATION_STEPS = [(key, runner.__name__) for key, runner in MIGRATION_RUNNERS]


def run_schema_migration_steps(db) -> None:
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migration_steps ("
            "step VARCHAR(100) PRIMARY KEY, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    db.commit()
    applied = {row[0] for row in db.execute(text("SELECT step FROM schema_migration_steps"))}
    for step_name, runner in MIGRATION_RUNNERS:
        if step_name in applied:
            continue
        runner(db)
        db.execute(text("INSERT INTO schema_migration_steps (step) VALUES (:step)"), {"step": step_name})
        db.commit()
