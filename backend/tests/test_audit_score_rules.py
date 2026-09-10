from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-score-audit-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.v2_database import DB_PATH, init_db  # noqa: E402

init_db()

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_score_rules.py"


def test_audit_score_rules_requires_existing_snapshot_and_emits_rule_id() -> None:
    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(TEST_DATA_DIR / "missing")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "data_dir_missing" in missing.stderr
    assert DB_PATH.is_file()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(DB_PATH.parent)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["readonly"] is True
    assert payload["count"] >= 1
    assert all("rule_id" in row for row in payload["rules"])
    assert "matches_seed_default" in payload["rules"][0]
