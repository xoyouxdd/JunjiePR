"""Isolate test files and fix the clock for real-day rotation integrations."""
import sys

import pytest

from temp_storage import finish_test_workspace, test_log, test_workspace


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_cmdline_main(config):
    with test_workspace("pr-pytest-") as root:
        if config.getini("cache_dir") == ".pytest_cache":
            config.inicfg["cache_dir"] = str(root / "pytest-cache")
            config._inicache.pop("cache_dir", None)
        success = False
        try:
            with test_log(root):
                result = yield
                success = result == pytest.ExitCode.OK
            return result
        finally:
            connection = sys.modules.get("app.database.connection")
            if connection is not None and connection.DB_PATH.resolve().is_relative_to(root):
                connection.engine.dispose()
            finish_test_workspace(root, success)


@pytest.fixture(autouse=True)
def rotation_daytime_clock(request, monkeypatch):
    if request.module.__name__.rsplit('.', 1)[-1] in {
        'test_rotation_formal', 'test_rotation_temporary',
        'test_rotation_post_holds', 'test_rotation_roster_independence',
        'test_rotation_timeout_cases_api',
        'test_rotation_forecast_formal',
    }:
        from rotation_test_clock import install_rotation_test_clock
        install_rotation_test_clock(monkeypatch)
