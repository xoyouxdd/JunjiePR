"""Compatibility score view, recreated per startup independently from one-shot patches."""
from __future__ import annotations

from sqlalchemy import text


def create_score_view(db) -> None:
    db.execute(text("DROP VIEW IF EXISTS v_employee_month_scores"))
    db.execute(
        text(
            """
            CREATE VIEW v_employee_month_scores AS
            WITH months AS (
                SELECT employee_id, recognition_month AS score_month FROM recognition_records
                UNION SELECT employee_id, attendance_month FROM attendance_monthly_scores
                UNION SELECT employee_id, deduction_month FROM deduction_records
            ), recognition_totals AS (
                SELECT employee_id, recognition_month AS score_month,
                       ROUND(SUM(CASE WHEN status = 'confirmed' THEN CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END ELSE 0 END), 2) AS recognition_score
                FROM recognition_records GROUP BY employee_id, recognition_month
            ), attendance_totals AS (
                SELECT employee_id, attendance_month AS score_month,
                       ROUND(CASE WHEN eligible = 1 THEN final_score ELSE 0 END, 2) AS attendance_score
                FROM attendance_monthly_scores
            ), deduction_totals AS (
                SELECT employee_id, deduction_month AS score_month,
                       ROUND(SUM(CASE WHEN status = 'active' AND (upgrade_role IS NULL OR upgrade_role != 'source_second') THEN points ELSE 0 END), 2) AS deduction_score
                FROM deduction_records GROUP BY employee_id, deduction_month
            )
            SELECT e.id AS employee_id, e.employee_no, e.name AS employee_name, m.score_month,
                   COALESCE(r.recognition_score, 0) AS recognition_score,
                   COALESCE(a.attendance_score, 0) AS attendance_score,
                   COALESCE(d.deduction_score, 0) AS deduction_score,
                   ROUND(COALESCE(r.recognition_score, 0) + COALESCE(a.attendance_score, 0) - COALESCE(d.deduction_score, 0), 2) AS total_score
            FROM months m
            JOIN employees e ON e.id = m.employee_id
            LEFT JOIN recognition_totals r ON r.employee_id = m.employee_id AND r.score_month = m.score_month
            LEFT JOIN attendance_totals a ON a.employee_id = m.employee_id AND a.score_month = m.score_month
            LEFT JOIN deduction_totals d ON d.employee_id = m.employee_id AND d.score_month = m.score_month
            """
        )
    )
    db.commit()
