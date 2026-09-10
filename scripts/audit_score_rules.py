"""Read-only listing of recognition score rules. Never deletes or rewrites scores."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Copied from app.v2_database so this script never opens a writable engine
# or creates directories.
INITIAL_SCORE_RULE_EFFECTIVE_DATE = "2020-01-01"
DEFAULT_RECOGNIZER_SCORES = {
    "TA_SUPERVISOR": "0.50",
    "SUPERVISOR": "0.50",
    "TA_GSM": "1.00",
    "GSM": "1.00",
    "AM": "1.50",
    "OM": "1.50",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="List recognition_score_rules for a pre-release dry-run")
    parser.add_argument(
        "--data-dir",
        required=True,
        help="existing isolated snapshot directory that already contains recognition_v2.db",
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    db_path = data_dir / "recognition_v2.db"
    if not data_dir.is_dir() or not db_path.is_file():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "data_dir_missing",
                    "message": "必须指定已存在的隔离快照目录，且其中已有 recognition_v2.db",
                    "data_dir": str(data_dir),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        print(
            json.dumps({"ok": False, "error": "readonly_open_failed", "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT r.id, roles.code AS role, roles.name AS role_name, r.score, r.effective_date, r.active "
            "FROM recognition_score_rules r LEFT JOIN roles ON roles.id = r.role_id "
            "ORDER BY r.role_id, r.effective_date, r.id"
        ).fetchall()
    finally:
        connection.close()
    payload = []
    for row in rows:
        score = str(row["score"])
        role = row["role"]
        default = DEFAULT_RECOGNIZER_SCORES.get(role) if role else None
        payload.append(
            {
                "rule_id": row["id"],
                "role": role,
                "role_name": row["role_name"],
                "score": score,
                "effective_date": row["effective_date"],
                "active": bool(row["active"]),
                "matches_seed_default": bool(
                    default is not None and score == default and row["effective_date"] != INITIAL_SCORE_RULE_EFFECTIVE_DATE
                ),
            }
        )
    print(
        json.dumps(
            {
                "mode": "dry-run",
                "readonly": True,
                "data_dir": str(data_dir),
                "count": len(payload),
                "rules": payload,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
