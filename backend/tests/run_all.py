"""Run isolated regressions, reusing browser processes within each UI domain.

Each test file sets its own isolated RECOGNITION_V2_DATA_DIR at module level
and imports `app.main`, so a single-process `pytest tests` run makes only the
first imported module's data directory win. Running each file in a separate
process keeps the directories truly isolated, matching the per-module
regression flow recorded in operation-records.

Usage (from the repository root, matching docs/getting-started.md):
    backend/.venv/Scripts/python.exe backend/tests/run_all.py

The default includes Python, Node.js, and Playwright/Edge DOM fixtures. Use
--suite python, --suite node, or --suite browser for focused checks. Browser
fixtures need Playwright resolvable from backend/ and an installed Edge. Most
serve synthetic API responses; video learning starts its own isolated local
API and FFmpeg fixture. None connects to a deployed application by default.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from temp_storage import finish_test_workspace, test_log, test_workspace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PYTHON = sys.executable
ESM_VM_TESTS = {"frontend_api_errors.cjs", "self_recognition_batch_fields.cjs", "view_lifecycle.cjs"}
BROWSER_DOMAINS = {
    "rotation": ("rotation_", "supervisor_roster_"),
    "communication": ("announcement_", "learning_", "video_"),
    "personal": ("appearance_", "avatar_", "activities_", "activity_", "monthly_", "attendance_medal", "legacy_points", "personal_"),
}
BROWSER_RESULT_PREFIX = "BROWSER_RESULTS "


def collect_tests(suite: str) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    if suite in {"all", "python"}:
        files.extend((path, "python") for path in sorted(HERE.glob("test_*.py")))
    for path in sorted(HERE.glob("*.cjs")):
        kind = "browser" if path.name.endswith("_ui.cjs") or path.name == "frontend_modules.cjs" else "node"
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


def run_command(command, on_line=print) -> int:
    with subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    ) as child:
        try:
            for line in child.stdout:
                on_line(line, end="", flush=True)
            return child.wait()
        except BaseException:
            if child.poll() is None:
                child.terminate()
            raise


def run_browser_domain(node, domain, names) -> list[tuple[str, bool]]:
    print(f"\n=== browser suite: {domain} ({len(names)} fixtures) ===", flush=True)
    summaries = []

    def report(line, **kwargs):
        if line.startswith(BROWSER_RESULT_PREFIX):
            summaries.append(line[len(BROWSER_RESULT_PREFIX):])
        else:
            print(line, **kwargs)

    try:
        returncode = run_command([node, "tests/support/browser-suite.cjs", *names], report)
        if len(summaries) != 1:
            raise ValueError("expected one fixture result summary")
        results = json.loads(summaries[0])
        if not isinstance(results, list) or [item["name"] for item in results] != names:
            raise ValueError("fixture result summary does not match the requested suite")
        if any(type(item["passed"]) is not bool for item in results) or (returncode == 0) != all(item["passed"] for item in results):
            raise ValueError("fixture results disagree with suite exit status")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"cannot complete browser suite {domain}: {error}", file=sys.stderr)
        return [(name, False) for name in names]
    return [(item["name"], item["passed"]) for item in results]


def run_suite(suite: str) -> int:
    files = collect_tests(suite)
    if not files:
        print(f"no tests found for suite {suite}")
        return 2
    try:
        node = check_frontend_runtime(files)
    except (OSError, RuntimeError) as error:
        print(f"test runtime unavailable: {error}", file=sys.stderr)
        return 2
    passed_modules: list[str] = []
    failed_modules: list[str] = []
    browser_groups: dict[str, list[str]] = {}
    for path, kind in files:
        if kind == "browser":
            domain = next((name for name, prefixes in BROWSER_DOMAINS.items() if path.name.startswith(prefixes)), "workbench")
            browser_groups.setdefault(domain, []).append(path.name)
            continue
        relative = path.relative_to(ROOT).as_posix()
        print(f"\n=== {path.name} ({kind}) ===", flush=True)
        command = ([PYTHON, "-m", "pytest", relative, "-q", "-p", "no:warnings"]
                   if kind == "python" else [node, *(["--experimental-vm-modules"] if path.name in ESM_VM_TESTS else []), relative])
        try:
            passed = run_command(command) == 0
        except OSError as error:
            print(f"cannot execute {path.name}: {error}", file=sys.stderr)
            passed = False
        (passed_modules if passed else failed_modules).append(path.name)
    for domain, names in browser_groups.items():
        for name, passed in run_browser_domain(node, domain, names):
            (passed_modules if passed else failed_modules).append(name)
    print(f"\nmodules: {len(passed_modules)} passed, {len(failed_modules)} failed")
    if passed_modules:
        print("passed:", ", ".join(passed_modules))
    if failed_modules:
        print("failed:", ", ".join(failed_modules))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "python", "node", "browser"), default="all")
    args = parser.parse_args(argv)
    with test_workspace("pr-suite-") as root:
        success = False
        try:
            with test_log(root):
                result = run_suite(args.suite)
                success = result == 0
            return result
        finally:
            finish_test_workspace(root, success)


if __name__ == "__main__":
    raise SystemExit(main())
