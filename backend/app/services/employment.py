"""Employment cycles retain identity and historical evidence across re-registration."""
from __future__ import annotations

import json
from datetime import date, timedelta

from app.v2_models import EmployeeRoleAssignment, EmployeeActingDuty, GroupMembership, GroupLeaderAssignment, ManagementScope


def employment_history(employee) -> list[dict]:
    return json.loads(getattr(employee, "employment_history", None) or "[]")


def retain_deleted_employment(employee) -> None:
    if not employee.account_deleted_at:
        return
    history = employment_history(employee)
    deleted_at = employee.account_deleted_at.isoformat()
    if any(period.get("deleted_at") == deleted_at for period in history):
        return
    history.append({"hired_on": employee.hired_on, "terminated_on": employee.terminated_on or employee.account_deleted_at.date().isoformat(),
                    "deleted_at": deleted_at, "deleted_by_name": employee.account_deleted_by_name or "",
                    "employee_no": employee.employee_no, "name": employee.name, "attraction_id": employee.attraction_id})
    employee.employment_history = json.dumps(history, ensure_ascii=False)


def restart_employment(db, employee, attraction_id: int | None, starts_on: str) -> None:
    retain_deleted_employment(employee)  # Also supports archives made before cycle tracking existed.
    history = employment_history(employee)
    history[-1]["rehired_on"] = starts_on
    employee.employment_history = json.dumps(history, ensure_ascii=False)
    last_day = (date.fromisoformat(starts_on) - timedelta(days=1)).isoformat()
    # Date-bound former organization/identities; never delete historical rows.
    for model, owner in ((EmployeeRoleAssignment, "employee_id"), (EmployeeActingDuty, "employee_id"),
                         (GroupMembership, "employee_id"), (GroupLeaderAssignment, "leader_employee_id")):
        rows = db.query(model).filter(getattr(model, owner) == employee.id, model.status != "cancelled",
                                     (model.ends_on.is_(None) | (model.ends_on >= starts_on))).all()
        for row in rows:
            if row.starts_on >= starts_on:
                row.status = "cancelled"
            else:
                row.ends_on = last_day
    for scope in db.query(ManagementScope).filter(ManagementScope.employee_id == employee.id,
            ManagementScope.ends_on.is_(None) | (ManagementScope.ends_on >= starts_on)).all():
        if scope.starts_on >= starts_on:
            db.delete(scope)  # Scheduled access never became historical employment.
        else:
            scope.ends_on = last_day
    employee.attraction_id = attraction_id
    employee.hired_on = starts_on
    employee.terminated_on = None
    employee.is_active = True
    employee.account_deleted_at = None
    employee.account_deleted_by_name = None


def previously_employed_on(employee, on_date: str) -> bool:
    return any((not period.get("hired_on") or period["hired_on"] <= on_date)
               and period.get("terminated_on") and on_date < period["terminated_on"]
               for period in employment_history(employee))
