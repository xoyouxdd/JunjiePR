"""One-time, audited recovery for testing accounts that have never logged in.

This runner is deliberately not an HTTP endpoint. It only changes enabled,
active frontline/operations accounts with no successful-login timestamp and
requires --apply. It never outputs passwords, names, employee numbers, or
password hashes.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.v2_crypto import default_initial_password, hash_password
from app.v2_database import SessionLocal
from app.v2_models import Employee, UserAccount, UserSession
from app.v2_services import role_at, write_audit


TARGET_ROLE_CODES = {
    "CM",
    "TR",
    "TA_SUPERVISOR",
    "SUPERVISOR",
    "TA_GSM",
    "GSM",
    "AM",
    "OM",
}
MIGRATION_MARKER = "2026-08-v22348-never-logged-in-initial-password-reset"


def eligible_accounts(db):
    """Return current enabled accounts meeting the explicit one-time scope."""
    result = []
    rows = (
        db.query(UserAccount)
        .join(Employee, Employee.id == UserAccount.employee_id)
        .filter(
            UserAccount.enabled.is_(True),
            UserAccount.last_login_at.is_(None),
            Employee.is_active.is_(True),
        )
        .all()
    )
    for account in rows:
        # The operator-approved fallback only applies to the standard seven-digit
        # employee-number login rule. Service and legacy-format accounts are out
        # of scope even if they carry one of the listed roles.
        if len(account.employee.employee_no) != 7 or not account.employee.employee_no.isdigit():
            continue
        role = role_at(db, account.employee_id)
        if role and role.code in TARGET_ROLE_CODES:
            result.append((account, role.code))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset never-logged-in testing accounts to employee-number suffixes.")
    parser.add_argument("--apply", action="store_true", help="Perform the reset. Omit for a read-only eligibility count.")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        already_applied = db.execute(
            text("SELECT 1 FROM schema_migration_steps WHERE step = :step"),
            {"step": MIGRATION_MARKER},
        ).first()
        if already_applied:
            print(json.dumps({"apply": bool(args.apply), "already_applied": True, "reset": 0}, ensure_ascii=False))
            return
        targets = eligible_accounts(db)
        by_role = Counter(role_code for _account, role_code in targets)
        if not args.apply:
            print(json.dumps({"apply": False, "eligible": len(targets), "by_role": dict(sorted(by_role.items()))}, ensure_ascii=False))
            return
        for account, role_code in targets:
            employee = account.employee
            account.password_hash = hash_password(default_initial_password(employee.employee_no))
            account.failed_attempts = 0
            account.locked_until = None
            account.must_change_password = True
            account.credential_initialized = True
            db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
            write_audit(
                db,
                None,
                "批量重置从未成功登录账号初始密码",
                "user_account",
                account.id,
                after={
                    "role_code": role_code,
                    "password_rule": "员工号后四位",
                    "must_change_password": True,
                    "sessions_revoked": True,
                    "selection_rule": "账号启用、员工在职、从未成功登录、当前角色在授权测试范围",
                },
                reason="系统负责人授权：长期测试账号恢复",
            )
        db.execute(
            text("INSERT INTO schema_migration_steps (step) VALUES (:step)"),
            {"step": MIGRATION_MARKER},
        )
        db.commit()
        print(json.dumps({"apply": True, "reset": len(targets), "by_role": dict(sorted(by_role.items()))}, ensure_ascii=False))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
