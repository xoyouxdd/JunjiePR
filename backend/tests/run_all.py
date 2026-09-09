"""Run every test module in its own process.

Each test file sets its own isolated RECOGNITION_V2_DATA_DIR at module level
and imports `app.main`, so a single-process `pytest tests` run makes only the
first imported module's data directory win. Running each file in a separate
process keeps the directories truly isolated, matching the per-module
regression flow recorded in operation-records.

Usage (from the candidate root):
    python tests/run_all.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PYTHON = sys.executable


def main() -> int:
    files = sorted(HERE.glob("test_*.py"))
    if not files:
        print("no test_*.py files found under tests/")
        return 2
    passed_modules: list[str] = []
    failed_modules: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        print(f"\n=== {path.name} ===")
        result = subprocess.run(
            [PYTHON, "-m", "pytest", relative, "-q", "-p", "no:warnings"],
            cwd=str(ROOT),
        )
        if result.returncode == 0:
            passed_modules.append(path.name)
        else:
            failed_modules.append(path.name)
    print(f"\nmodules: {len(passed_modules)} passed, {len(failed_modules)} failed")
    if passed_modules:
        print("passed:", ", ".join(passed_modules))
    if failed_modules:
        print("failed:", ", ".join(failed_modules))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
