"""Read-only listing of recognition score rules. Never deletes or rewrites scores."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="List recognition_score_rules for a pre-release dry-run")
    parser.add_argument("--data-dir", help="override RECOGNITION_V2_DATA_DIR")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["RECOGNITION_V2_DATA_DIR"] = str(Path(args.data_dir).resolve())
    sys.path.insert(0, str(ROOT / "backend"))
    from app.v2_database import DEFAULT_RECOGNIZER_SCORES, INITIAL_SCORE_RULE_EFFECTIVE_DATE, SessionLocal
    from app.v2_models import RecognitionScoreRule, Role

    rows = []
    with SessionLocal() as db:
        for rule in db.query(RecognitionScoreRule).order_by(RecognitionScoreRule.role_id, RecognitionScoreRule.effective_date, RecognitionScoreRule.id).all():
            role = db.get(Role, rule.role_id)
            default = DEFAULT_RECOGNIZER_SCORES.get(role.code) if role else None
            rows.append(
                {
                    "role": role.code if role else None,
                    "role_name": role.name if role else None,
                    "score": str(rule.score),
                    "effective_date": rule.effective_date,
                    "active": bool(rule.active),
                    "matches_seed_default": bool(default is not None and str(rule.score) == str(default) and rule.effective_date != INITIAL_SCORE_RULE_EFFECTIVE_DATE),
                }
            )
    print(json.dumps({"mode": "dry-run", "count": len(rows), "rules": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
