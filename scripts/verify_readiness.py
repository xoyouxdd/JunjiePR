"""Read-only release readiness checks for an explicitly supplied SQLite copy.

This tool intentionally opens the database in SQLite ``mode=ro``. Its JSON
report contains aggregate counts and issue codes only, so it can be retained
with deployment evidence without exposing employees, accounts, attachments,
or credentials.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


FRONTLINE_ROLES = ("CM", "TR")
VALID_MONTH_CLOSE_STATUS = ("open", "closed")


def scalar(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    return int(connection.execute(query, params).fetchone()[0])


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def add_issue(issues: list[dict], code: str, count: int, message: str, severity: str = "blocking") -> None:
    if count:
        issues.append({"code": code, "count": int(count), "severity": severity, "message": message})


def report_for(database: Path) -> dict:
    if not database.is_file():
        raise ValueError("database copy does not exist")
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=60)
    try:
        connection.execute("PRAGMA busy_timeout=60000")
        quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        issues: list[dict] = []
        add_issue(issues, "sqlite_quick_check_failed", 1 if quick_check != ["ok"] else 0, "SQLite quick_check did not return ok.")
        add_issue(issues, "foreign_key_check_failed", len(foreign_keys), "SQLite foreign_key_check returned rows.")

        required = ("employees", "user_accounts", "roles", "employee_role_assignments", "attractions", "work_groups", "group_memberships", "month_closures")
        missing = [name for name in required if not table_exists(connection, name)]
        add_issue(issues, "required_table_missing", len(missing), "Required readiness tables are missing.")
        if missing:
            return {"ok": False, "schema_ok": False, "quick_check": quick_check, "foreign_key_issues": len(foreign_keys), "issues": issues, "counts": {}}

        today = connection.execute("SELECT date('now','localtime')").fetchone()[0]
        counts = {
            "employees": scalar(connection, "SELECT COUNT(*) FROM employees"),
            "active_employees": scalar(connection, "SELECT COUNT(*) FROM employees WHERE is_active=1"),
            "accounts": scalar(connection, "SELECT COUNT(*) FROM user_accounts"),
            "active_work_groups": scalar(connection, "SELECT COUNT(*) FROM work_groups WHERE status='active'"),
            "employee_circles": scalar(connection, "SELECT COUNT(*) FROM attractions WHERE active=1 AND employee_circle=1"),
            "month_closures": scalar(connection, "SELECT COUNT(*) FROM month_closures"),
        }
        add_issue(issues, "duplicate_login_account", scalar(connection, "SELECT COUNT(*) FROM (SELECT login_account FROM user_accounts GROUP BY login_account HAVING COUNT(*) > 1)"), "Duplicate login accounts exist.")
        add_issue(issues, "duplicate_employee_account", scalar(connection, "SELECT COUNT(*) FROM (SELECT employee_id FROM user_accounts GROUP BY employee_id HAVING COUNT(*) > 1)"), "More than one account is linked to an employee.")
        add_issue(issues, "active_employee_without_account", scalar(connection, "SELECT COUNT(*) FROM employees e LEFT JOIN user_accounts a ON a.employee_id=e.id WHERE e.is_active=1 AND a.id IS NULL"), "Active employees without accounts exist.")
        add_issue(issues, "enabled_account_not_active_employee", scalar(connection, "SELECT COUNT(*) FROM user_accounts a LEFT JOIN employees e ON e.id=a.employee_id WHERE a.enabled=1 AND (e.id IS NULL OR e.is_active<>1)"), "Enabled accounts are linked to inactive or missing employees.")
        add_issue(issues, "active_employee_without_current_role", scalar(connection, "SELECT COUNT(*) FROM employees e WHERE e.is_active=1 AND NOT EXISTS (SELECT 1 FROM employee_role_assignments ra WHERE ra.employee_id=e.id AND ra.status<>'cancelled' AND ra.starts_on<=? AND (ra.ends_on IS NULL OR ra.ends_on>=?))", (today, today)), "Active employees without a current role assignment exist.")
        add_issue(issues, "frontline_without_circle", scalar(connection, "SELECT COUNT(*) FROM employees e WHERE e.is_active=1 AND e.attraction_id IS NULL AND EXISTS (SELECT 1 FROM employee_role_assignments ra JOIN roles r ON r.id=ra.role_id WHERE ra.employee_id=e.id AND ra.status<>'cancelled' AND ra.starts_on<=? AND (ra.ends_on IS NULL OR ra.ends_on>=?) AND r.code IN ('CM','TR'))", (today, today)), "Active CM/TR employees without an employee circle exist.")
        add_issue(issues, "frontline_without_group", scalar(connection, "SELECT COUNT(*) FROM employees e WHERE e.is_active=1 AND EXISTS (SELECT 1 FROM employee_role_assignments ra JOIN roles r ON r.id=ra.role_id WHERE ra.employee_id=e.id AND ra.status<>'cancelled' AND ra.starts_on<=? AND (ra.ends_on IS NULL OR ra.ends_on>=?) AND r.code IN ('CM','TR')) AND NOT EXISTS (SELECT 1 FROM group_memberships gm WHERE gm.employee_id=e.id AND gm.status='active' AND gm.starts_on<=? AND (gm.ends_on IS NULL OR gm.ends_on>=?))", (today, today, today, today)), "Active CM/TR employees without a current work group exist.", "warning")
        add_issue(issues, "employee_multiple_active_groups", scalar(connection, "SELECT COUNT(*) FROM (SELECT employee_id FROM group_memberships WHERE status='active' AND starts_on<=? AND (ends_on IS NULL OR ends_on>=?) GROUP BY employee_id HAVING COUNT(*)>1)", (today, today)), "Employees are in more than one active group.")
        add_issue(issues, "invalid_month_close_status", scalar(connection, "SELECT COUNT(*) FROM month_closures WHERE status NOT IN ('open','closed')"), "Month closures have unsupported states.")
        add_issue(issues, "month_close_invalid_circle", scalar(connection, "SELECT COUNT(*) FROM month_closures mc LEFT JOIN attractions a ON a.id=mc.attraction_id WHERE mc.attraction_id IS NOT NULL AND (a.id IS NULL OR a.employee_circle<>1)"), "Month closures reference invalid employee circles.")
        if table_exists(connection, "employee_loa_periods"):
            add_issue(issues, "loa_invalid_dates", scalar(connection, "SELECT COUNT(*) FROM employee_loa_periods WHERE ends_on IS NOT NULL AND ends_on<starts_on"), "LOA periods have an end before the start.")
        if table_exists(connection, "management_scopes"):
            add_issue(issues, "management_scope_invalid_circle", scalar(connection, "SELECT COUNT(*) FROM management_scopes ms LEFT JOIN attractions a ON a.id=ms.attraction_id WHERE a.id IS NULL OR a.employee_circle<>1"), "Management scopes reference invalid employee circles.")
        blocking_issues = [issue for issue in issues if issue["severity"] == "blocking"]
        return {
            "ok": not blocking_issues,
            "schema_ok": True,
            "quick_check": quick_check,
            "foreign_key_issues": len(foreign_keys),
            "issues": issues,
            "blocking_issues": len(blocking_issues),
            "counts": counts,
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only recognition release readiness check")
    parser.add_argument("--database", required=True, help="Explicit SQLite copy to inspect read-only")
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    try:
        report = report_for(Path(args.database))
    except (OSError, ValueError, sqlite3.Error) as exc:
        report = {"ok": False, "schema_ok": False, "issues": [{"code": "readiness_check_error", "count": 1, "message": str(exc)}], "counts": {}}
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
