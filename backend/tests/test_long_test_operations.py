from frontend_source import read_frontend_source
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent


def test_actionable_api_failure_messages_and_safe_restore_runner_are_present():
    script = read_frontend_source()
    restore_runner = (REPOSITORY_ROOT / "scripts" / "Run-MonthlyRestoreRehearsal.ps1").read_text(encoding="utf-8")
    assert "function apiFallbackMessage" in script
    assert "网络连接失败，请检查网络后重试" in script
    assert "数据刚刚被其他操作更新" in script
    assert "Test-SqliteBackupRestore.ps1" in restore_runner
    assert "Sort-Object LastWriteTimeUtc -Descending" in restore_runner
    assert "-RehearsalRoot $RehearsalRoot" in restore_runner
