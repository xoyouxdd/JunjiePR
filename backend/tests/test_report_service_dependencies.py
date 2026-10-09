"""Reports can load their query services without registering HTTP routes."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", [
    "app.services.declaration_statistics",
    "app.hr_monthly_report",
])
def test_report_queries_import_without_http_routers(module):
    # Use a new interpreter so prior app.main imports cannot conceal a cycle.
    # Block route imports rather than merely inspecting direct import syntax.
    probe = """
import importlib
import importlib.abc
import sys

class RejectHttpRouters(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.routers" or fullname.startswith("app.routers."):
            raise AssertionError(f"Report query depends on HTTP router: {fullname}")
        return None

sys.meta_path.insert(0, RejectHttpRouters())
importlib.import_module(sys.argv[1])
assert not any(name == "app.routers" or name.startswith("app.routers.") for name in sys.modules)
"""
    # Query imports should not initialize storage. A unique absent path both
    # isolates them from application data and verifies that no DB is created.
    data_dir = BACKEND.parent / "output" / f"report-query-import-{uuid4().hex}"
    env = {**os.environ, "RECOGNITION_V2_DATA_DIR": str(data_dir)}
    result = subprocess.run(
        [sys.executable, "-c", probe, module], cwd=BACKEND, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not data_dir.exists(), "Importing report queries initialized application storage"
