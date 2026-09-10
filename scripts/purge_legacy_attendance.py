"""Preview or apply one-off cleanup of the leftover ATTENDANCE deduction type.

Default is dry-run. Startup seeding never calls this.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or apply leftover ATTENDANCE catalog cleanup")
    parser.add_argument("--apply", action="store_true", help="actually delete; default is dry-run")
    parser.add_argument("--data-dir", help="override RECOGNITION_V2_DATA_DIR")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["RECOGNITION_V2_DATA_DIR"] = str(Path(args.data_dir).resolve())
    sys.path.insert(0, str(ROOT / "backend"))
    from app.v2_database import SessionLocal, purge_legacy_attendance

    with SessionLocal() as db:
        result = purge_legacy_attendance(db, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
