"""Employee-edit command coordination; the HTTP caller owns the transaction.

Profile/status, identity and group changes have separate owners. One context
keeps their month-close gates, effective day and audit actor consistent; a
failure in any owner is rolled back together by the calling route.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.services import loa_commands
from app.services.audit import write_audit
from app.v2_models import Employee, Role

if TYPE_CHECKING:
    from app.v2_auth import V2User


@dataclass
class EmployeeEditContext:
    db: Session
    employee: Employee
    payload: dict
    user: V2User
    existing_role: Role | None
    existing_base_role: Role | None
    today: date
    loa_today: date
    month_gate: loa_commands.MonthGate
    ip_address: str | None = None
    warnings: list[str] = field(default_factory=list, init=False)
    changed_months: set[str] = field(default_factory=set, init=False)
    attendance_state_changed: bool = field(default=False, init=False)
    original_attraction_id: int | None = field(init=False)

    def __post_init__(self) -> None:
        self.original_attraction_id = self.employee.attraction_id

    @property
    def today_text(self) -> str:
        return self.today.isoformat()

    @property
    def reason(self) -> str:
        return str(self.payload.get("reason") or "")

    def ensure_current_month_open(self, operation: str) -> None:
        self.ensure_affected_month_open(
            self.db, self.today.strftime("%Y-%m"), self.employee.attraction_id, operation,
        )

    def ensure_affected_month_open(
        self, db: Session, month: str, attraction_id: int | None, operation: str,
    ) -> None:
        # A cross-circle edit must respect both the old and new month closure.
        self.month_gate(db, month, self.original_attraction_id, operation)
        if attraction_id != self.original_attraction_id:
            self.month_gate(db, month, attraction_id, operation)

    def audit(self, action: str, entity_type: str, entity_id=None, **details) -> None:
        details.setdefault("reason", self.reason)
        write_audit(
            self.db, self.user.employee, action, entity_type, entity_id,
            ip_address=self.ip_address, **details,
        )


def apply_employee_edit(context: EmployeeEditContext) -> list[str]:
    """Apply one form without committing; return any leader/reviewer warnings."""
    from app.services.employee_groups import apply_group_membership
    from app.services.employee_identity_commands import apply_identity_changes
    from app.services.employee_status import apply_profile_and_status
    from app.services.management_scopes import synchronize_gsm_management_scope

    apply_profile_and_status(context)
    resulting_role, resulting_base_role = apply_identity_changes(context)
    synchronize_gsm_management_scope(
        context.db, context.employee,
        resulting_role.code if resulting_role else None, context.today_text,
    )
    apply_group_membership(context, resulting_role, resulting_base_role)
    context.employee.updated_at = datetime.now()
    context.db.flush()
    if context.attendance_state_changed:
        context.changed_months.add(context.today.strftime("%Y-%m"))
    loa_commands.recalculate_changed_months(context.db, context.employee, context.changed_months)
    return context.warnings
