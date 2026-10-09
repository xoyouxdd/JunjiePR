"""Shared predicates for scores that participate in monthly performance."""
from __future__ import annotations

from sqlalchemy import exists, func, or_

from app.v2_models import EmployeeLOAPeriod


def loa_month_exclusion(employee_id, month):
    """Exclude a whole calendar month if a non-cancelled LOA touches it.

    Both arguments may be SQL columns, allowing range rankings to check each
    record's own month without excluding unaffected months in the range.
    """
    month_start = month + "-01"
    return exists().where(
        EmployeeLOAPeriod.employee_id == employee_id,
        EmployeeLOAPeriod.status != "cancelled",
        EmployeeLOAPeriod.starts_on <= func.date(month_start, "+1 month", "-1 day"),
        or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= month_start),
    ).correlate_except(EmployeeLOAPeriod)
