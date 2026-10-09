"""Live GSM organization scopes, independent from schema and startup orchestration."""
from __future__ import annotations

from datetime import date, timedelta


def synchronize_gsm_management_scope(db, employee, role_code: str | None, on_date: str | None = None) -> bool:
    """Reconcile one employee's live GSM/TA GSM tree scope.

    A management scope is only valid while the employee is active, currently a
    GSM/TA GSM, and belongs to an active employee circle.  Reconciliation must
    first end obsolete scopes, rather than returning early for an ineligible
    employee: otherwise a former GSM can remain in the organization tree after
    their role or circle changes.
    """
    from app.v2_models import Attraction, ManagementScope

    value = on_date or date.today().isoformat()
    if not employee:
        return False
    circle = db.get(Attraction, employee.attraction_id) if employee.attraction_id else None
    eligible = bool(
        employee
        and employee.is_active
        and role_code in {"GSM", "TA_GSM"}
        and circle
        and circle.active
        and circle.employee_circle
    )

    active_scopes = (
        db.query(ManagementScope)
        .filter(
            ManagementScope.employee_id == employee.id,
            ManagementScope.starts_on <= value,
            (ManagementScope.ends_on.is_(None) | (ManagementScope.ends_on >= value)),
        )
        .all()
    )
    changed = False
    target_scope = None
    if eligible:
        matching_scopes = [scope for scope in active_scopes if scope.attraction_id == circle.id]
        if matching_scopes:
            # Keep one current target scope.  The latest row is the one that
            # best represents an intentional same-day correction; all other
            # overlapping rows are stale duplicates and must be closed.
            target_scope = max(matching_scopes, key=lambda scope: (scope.starts_on, scope.id))
    for scope in active_scopes:
        if target_scope is scope:
            if scope.ends_on is not None:
                scope.ends_on = None
                changed = True
            continue

        # This includes all invalid cases: stale circle, removed circle,
        # inactive employee, changed role, and duplicate target rows.  A scope
        # created today has no historical interval to preserve, so delete it;
        # otherwise close it before today's effective organization state.
        if scope.starts_on >= value:
            db.delete(scope)
        else:
            scope.ends_on = (date.fromisoformat(value) - timedelta(days=1)).isoformat()
        changed = True
    if eligible and target_scope is None:
        db.add(ManagementScope(employee_id=employee.id, attraction_id=circle.id, starts_on=value))
        changed = True
    return changed


def ensure_gsm_management_scopes(db) -> None:
    """Reconcile every employee's GSM scope on startup, idempotently.

    Querying all employees (rather than only current GSM/TA GSM employees) is
    intentional: it removes scopes that became orphaned after a role, circle or
    employment-status change before this synchronization was available.
    """
    from app.v2_models import Employee
    from app.services.identity import roles_at

    today = date.today().isoformat()
    employees = db.query(Employee).all()
    current_roles = roles_at(db, [employee.id for employee in employees], today)
    changed = False
    for employee in employees:
        role = current_roles.get(employee.id)
        changed = synchronize_gsm_management_scope(db, employee, role.code if role else None, today) or changed
    if changed:
        db.commit()
