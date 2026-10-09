"""Run Python and frontend regressions in their own processes.

Each test file sets its own isolated RECOGNITION_V2_DATA_DIR at module level
and imports `app.main`, so a single-process `pytest tests` run makes only the
first imported module's data directory win. Running each file in a separate
process keeps the directories truly isolated, matching the per-module
regression flow recorded in operation-records.

Usage (from the repository root, matching docs/getting-started.md):
    backend/.venv/Scripts/python.exe backend/tests/run_all.py

The default includes Python, Node.js, and Playwright/Edge DOM fixtures. Use
--suite python, --suite node, or --suite browser for focused checks. Browser
fixtures need Playwright resolvable from backend/ and an installed Edge; they
do not start an application server or connect to a deployed application.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PYTHON = sys.executable
BROWSER_TESTS = {"deduction_type_picker_ui.cjs", "frontend_modules.cjs"}
ESM_VM_TESTS = {"view_lifecycle.cjs"}


def collect_tests(suite: str) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    if suite in {"all", "python"}:
        files.extend((path, "python") for path in sorted(HERE.glob("test_*.py")))
    for path in sorted(HERE.glob("*.cjs")):
        kind = "browser" if path.name in BROWSER_TESTS else "node"
        if suite in {"all", kind}:
            files.append((path, kind))
    return files


def check_frontend_runtime(files: list[tuple[Path, str]]) -> str | None:
    if not any(kind != "python" for _, kind in files):
        return None
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for frontend tests; add node to PATH")
    if any(kind == "browser" for _, kind in files):
        # Probe the same module resolution, channel and mode used by the fixture.
        # Launching and closing a blank local browser verifies the actual runtime.
        probe = subprocess.run(
            [node, "-e", (
                "(async()=>{const {chromium}=require('playwright');"
                "const browser=await chromium.launch({channel:'msedge',headless:true});"
                "await browser.close();})().catch(error=>{console.error(error);process.exitCode=1;});"
            )],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        if probe.returncode != 0:
            detail = probe.stderr.strip() or probe.stdout.strip()
            raise RuntimeError(
                "Browser tests require Playwright resolvable from backend/ and installed Microsoft Edge. "
                "No test was run. " + detail
            )
    return node


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "python", "node", "browser"), default="all")
    args = parser.parse_args(argv)
    files = collect_tests(args.suite)
    if not files:
        print(f"no tests found for suite {args.suite}")
        return 2
    try:
        node = check_frontend_runtime(files)
    except (OSError, RuntimeError) as error:
        print(f"test runtime unavailable: {error}", file=sys.stderr)
        return 2
    passed_modules: list[str] = []
    failed_modules: list[str] = []
    for path, kind in files:
        relative = path.relative_to(ROOT).as_posix()
        print(f"\n=== {path.name} ({kind}) ===", flush=True)
        command = ([PYTHON, "-m", "pytest", relative, "-q", "-p", "no:warnings"]
                   if kind == "python" else [node, *(["--experimental-vm-modules"] if path.name in ESM_VM_TESTS else []), relative])
        try:
            result = subprocess.run(command, cwd=str(ROOT))
            passed = result.returncode == 0
        except OSError as error:
            print(f"cannot execute {path.name}: {error}", file=sys.stderr)
            passed = False
        if passed:
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
