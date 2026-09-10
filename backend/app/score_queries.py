from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session


def _employee_filter(employee_ids: Iterable[int]) -> tuple[str, dict[str, object]]:
    unique_ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
    if not unique_ids:
        return "", {}
    params = {f"score_employee_{index}": employee_id for index, employee_id in enumerate(unique_ids)}
    return f" AND employee_id IN ({','.join(':' + key for key in params)})", params


def month_score_employee_ids(db: Session, month: str) -> list[int]:
    """Return employees with score inputs for one month without scanning other months."""
    rows = db.execute(
        text(
            """
            SELECT employee_id FROM recognition_records WHERE recognition_month=:month
            UNION SELECT employee_id FROM attendance_monthly_scores WHERE attendance_month=:month
            UNION SELECT employee_id FROM deduction_records WHERE deduction_month=:month
            """
        ),
        {"month": month},
    ).all()
    return [int(row[0]) for row in rows]


def employee_month_scores(db: Session, month: str, employee_ids: Iterable[int]) -> list[dict]:
    """Calculate monthly scores after pushing month and employee scope into each source."""
    employee_filter, id_params = _employee_filter(employee_ids)
    if not id_params:
        return []
    sql = f"""
        WITH months AS (
            SELECT employee_id FROM recognition_records WHERE recognition_month=:month{employee_filter}
            UNION SELECT employee_id FROM attendance_monthly_scores WHERE attendance_month=:month{employee_filter}
            UNION SELECT employee_id FROM deduction_records WHERE deduction_month=:month{employee_filter}
        ), recognition_totals AS (
            SELECT employee_id,
                   ROUND(SUM(CASE WHEN status='confirmed' THEN
                       CASE WHEN recognition_date<'2026-09-01' THEN fraction ELSE credited_fraction END
                       ELSE 0 END), 2) AS recognition_score
            FROM recognition_records
            WHERE recognition_month=:month{employee_filter}
            GROUP BY employee_id
        ), attendance_totals AS (
            SELECT employee_id,
                   ROUND(CASE WHEN eligible=1 THEN final_score ELSE 0 END, 2) AS attendance_score
            FROM attendance_monthly_scores
            WHERE attendance_month=:month{employee_filter}
        ), deduction_totals AS (
            SELECT employee_id,
                   ROUND(SUM(CASE WHEN status='active' THEN points ELSE 0 END), 2) AS deduction_score
            FROM deduction_records
            WHERE deduction_month=:month{employee_filter}
            GROUP BY employee_id
        )
        SELECT e.id AS employee_id, e.employee_no, e.name AS employee_name, :month AS score_month,
               COALESCE(r.recognition_score,0) AS recognition_score,
               COALESCE(a.attendance_score,0) AS attendance_score,
               COALESCE(d.deduction_score,0) AS deduction_score,
               ROUND(COALESCE(r.recognition_score,0)+COALESCE(a.attendance_score,0)-COALESCE(d.deduction_score,0),2) AS total_score
        FROM months m
        JOIN employees e ON e.id=m.employee_id
        LEFT JOIN recognition_totals r ON r.employee_id=m.employee_id
        LEFT JOIN attendance_totals a ON a.employee_id=m.employee_id
        LEFT JOIN deduction_totals d ON d.employee_id=m.employee_id
    """
    return [dict(row) for row in db.execute(text(sql), {"month": month, **id_params}).mappings().all()]
