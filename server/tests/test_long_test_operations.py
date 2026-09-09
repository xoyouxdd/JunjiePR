from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_actionable_api_failure_messages_and_safe_restore_runner_are_present():
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    restore_runner = (ROOT / "ops" / "Run-MonthlyRestoreRehearsal.ps1").read_text(encoding="utf-8")
    assert "function apiFallbackMessage" in script
    assert "网络连接失败，请检查网络后重试" in script
    assert "数据刚刚被其他操作更新" in script
    assert "Test-SqliteBackupRestore.ps1" in restore_runner
    assert "Sort-Object LastWriteTimeUtc -Descending" in restore_runner
    assert "-RehearsalRoot $RehearsalRoot" in restore_runner
