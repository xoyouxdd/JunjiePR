"""Local-only historical preview/apply with a verified preview and SQLite backup."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime_environment import data_directory, environment_kind, validate_runtime_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not os.environ.get("RECOGNITION_V2_DATA_DIR") or environment_kind() not in {"local", "test"}:
        parser.error("必须明确配置独立本地/测试数据目录；此脚本禁止在正式环境运行")
    validate_runtime_paths()
    if args.apply and not args.expected_fingerprint:
        parser.error("入账必须提供预览的 --expected-fingerprint")
    data = data_directory()
    db_path = data / "recognition_v2.db"
    if not db_path.is_file():
        parser.error("本地数据库不存在")
    backup = data / "backups" / ("activity-points-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".db")
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("本地备份完整性检查失败")
    from app.v2_database import init_db, SessionLocal
    from app.services.activity_points import backfill_apply, backfill_preview
    from app.v2_models import AuditLog
    from sqlalchemy import text
    init_db()
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if args.apply:
            result = backfill_apply(db, args.expected_fingerprint)
            db.add(AuditLog(operator_name="本地积点补计工具", action="历史活动积点补计",
                entity_type="activity_points", after_json=json.dumps(result, ensure_ascii=False)))
            db.commit()
        else:
            result = backfill_preview(db)
            db.rollback()
    result.update(database=str(db_path), backup=str(backup), applied=args.apply)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"pending_review", "reasons"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
