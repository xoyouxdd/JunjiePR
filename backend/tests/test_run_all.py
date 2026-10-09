"""Test validation orchestration without launching suites or deployment scripts."""

from pathlib import Path
from types import SimpleNamespace

import run_all


def prepare(monkeypatch, tmp_path: Path, names: list[str]):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in names:
        (tests / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(run_all, "HERE", tests)
    monkeypatch.setattr(run_all, "ROOT", tmp_path)
    monkeypatch.setattr(run_all.shutil, "which", lambda name: "test-node")
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_all.subprocess, "run", execute)
    return calls


def test_default_runs_python_and_all_frontend_tests_separately(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, [
        "test_b.py", "test_a.py", "self_recognition_batch_fields.cjs",
        "pr_uncapped_score_display.cjs", "deduction_type_picker_ui.cjs", "view_lifecycle.cjs",
    ])
    assert run_all.main([]) == 0
    assert calls[0][0][:2] == ["test-node", "-e"]
    assert "channel:'msedge'" in calls[0][0][2]
    commands = [command for command, _ in calls[1:]]
    assert [command[3] for command in commands[:2]] == ["tests/test_a.py", "tests/test_b.py"]
    assert all(command[:3] == [run_all.PYTHON, "-m", "pytest"] for command in commands[:2])
    assert {command[-1] for command in commands[2:]} == {
        "tests/self_recognition_batch_fields.cjs", "tests/pr_uncapped_score_display.cjs",
        "tests/deduction_type_picker_ui.cjs", "tests/view_lifecycle.cjs",
    }
    assert all(kwargs["cwd"] == str(tmp_path) for _, kwargs in calls)
    assert ["test-node", "--experimental-vm-modules", "tests/view_lifecycle.cjs"] in commands


def test_missing_node_blocks_default_before_python(monkeypatch, tmp_path, capsys):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py", "fields.cjs"])
    monkeypatch.setattr(run_all.shutil, "which", lambda name: None)
    assert run_all.main([]) == 2
    assert not calls
    assert "Node.js is required" in capsys.readouterr().err


def test_browser_runtime_failure_blocks_default(monkeypatch, tmp_path, capsys):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py", "deduction_type_picker_ui.cjs"])

    def fail(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr="Playwright not found")

    monkeypatch.setattr(run_all.subprocess, "run", fail)
    assert run_all.main([]) == 2
    assert len(calls) == 1
    assert "Playwright not found" in capsys.readouterr().err


def test_python_suite_needs_no_node(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py", "fields.cjs", "deduction_type_picker_ui.cjs"])
    monkeypatch.setattr(run_all.shutil, "which", lambda name: None)
    assert run_all.main(["--suite", "python"]) == 0
    assert len(calls) == 1
    assert calls[0][0][1:3] == ["-m", "pytest"]


def test_node_suite_runs_no_browser_or_python(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py", "fields.cjs", "deduction_type_picker_ui.cjs"])
    assert run_all.main(["--suite", "node"]) == 0
    assert [command for command, _ in calls] == [["test-node", "tests/fields.cjs"]]


def test_browser_suite_runs_only_local_fixture(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py", "fields.cjs", "deduction_type_picker_ui.cjs"])
    assert run_all.main(["--suite", "browser"]) == 0
    assert len(calls) == 2
    assert calls[1][0] == ["test-node", "tests/deduction_type_picker_ui.cjs"]


def test_nonzero_test_result_is_not_hidden(monkeypatch, tmp_path, capsys):
    prepare(monkeypatch, tmp_path, ["a.cjs", "b.cjs"])
    monkeypatch.setattr(run_all.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    assert run_all.main(["--suite", "node"]) == 1
    assert "0 passed, 2 failed" in capsys.readouterr().out


def test_launch_error_is_reported_as_failure(monkeypatch, tmp_path, capsys):
    prepare(monkeypatch, tmp_path, ["test_a.py"])

    def fail(*args, **kwargs):
        raise OSError("runtime removed")

    monkeypatch.setattr(run_all.subprocess, "run", fail)
    assert run_all.main(["--suite", "python"]) == 1
    assert "runtime removed" in capsys.readouterr().err


def test_empty_suite_cannot_pass(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py"])
    assert run_all.main(["--suite", "node"]) == 2
    assert not calls


def test_esm_browser_entry_is_a_browser_case_and_source_helper_is_not_a_test(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["frontend_modules.cjs", "frontend_source.js", "view_lifecycle.cjs"])
    assert run_all.main(["--suite", "browser"]) == 0
    assert len(calls) == 2
    assert calls[-1][0] == ["test-node", "tests/frontend_modules.cjs"]
