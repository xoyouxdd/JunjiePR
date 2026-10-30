"""Only real-day formal-rotation integrations need a fixed daytime wall clock."""
import pytest


@pytest.fixture(autouse=True)
def rotation_daytime_clock(request, monkeypatch):
    if request.module.__name__.rsplit('.', 1)[-1] in {
        'test_rotation_formal', 'test_rotation_temporary',
        'test_rotation_post_holds', 'test_rotation_roster_independence',
        'test_rotation_timeout_cases_api',
    }:
        from rotation_test_clock import install_rotation_test_clock
        install_rotation_test_clock(monkeypatch)
