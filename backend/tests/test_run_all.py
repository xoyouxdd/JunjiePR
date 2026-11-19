"""Test validation orchestration without launching suites or deployment scripts."""

from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
import json

import run_all


def test_recent_playwright_fixtures_are_in_browser_suite():
    names = {path.name for path, kind in run_all.collect_tests("browser")}
    assert {"rotation_assignment_window_ui.cjs", "month_close_scope_ui.cjs"} <= names
    assert not ({"rotation_assignment_window_ui.cjs", "month_close_scope_ui.cjs"}
                & {path.name for path, kind in run_all.collect_tests("node")})


def test_every_playwright_fixture_is_classified_as_browser():
    for path, kind in run_all.collect_tests('all'):
        if path.suffix == '.cjs':
            source = path.read_text(encoding='utf-8')
            if "require('playwright')" in source or "require('./support/browser-runtime.cjs')" in source:
                assert kind == 'browser', path.name


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
    monkeypatch.setattr(run_all.subprocess, "Popen", fake_popen(calls))
    return calls


def fake_popen(calls, returncode=0):
    def launch(command, **kwargs):
        calls.append((command, kwargs))
        lines = []
        if command[1] == "tests/support/browser-suite.cjs":
            lines.append(run_all.BROWSER_RESULT_PREFIX + json.dumps([
                {"name": name, "passed": returncode == 0} for name in command[2:]
            ]))
        return nullcontext(SimpleNamespace(stdout=iter(lines), wait=lambda: returncode))
    return launch


def test_default_runs_python_and_node_separately_from_browser_domains(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, [
        "test_b.py", "test_a.py", "self_recognition_batch_fields.cjs",
        "frontend_api_errors.cjs", "deduction_type_picker_ui.cjs", "view_lifecycle.cjs",
    ])
    assert run_all.main([]) == 0
    assert calls[0][0][:2] == ["test-node", "-e"]
    assert "channel:'msedge'" in calls[0][0][2]
    commands = [command for command, _ in calls[1:]]
    assert [command[3] for command in commands[:2]] == ["tests/test_a.py", "tests/test_b.py"]
    assert all(command[:3] == [run_all.PYTHON, "-m", "pytest"] for command in commands[:2])
    assert {command[-1] for command in commands[2:-1]} == {
        "tests/self_recognition_batch_fields.cjs", "tests/frontend_api_errors.cjs",
        "tests/view_lifecycle.cjs",
    }
    assert commands[-1] == ["test-node", "tests/support/browser-suite.cjs", "deduction_type_picker_ui.cjs"]
    assert all(Path(kwargs["cwd"]) == tmp_path for _, kwargs in calls)
    assert all(command[1] == "--experimental-vm-modules" for command in commands[2:-1])


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


def test_browser_suite_groups_only_browser_fixtures_by_domain(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py", "fields.cjs", "deduction_type_picker_ui.cjs",
                                          "rotation_daily_ui.cjs", "rotation_formal_ui.cjs"])
    assert run_all.main(["--suite", "browser"]) == 0
    assert [command for command, _ in calls[1:]] == [
        ["test-node", "tests/support/browser-suite.cjs", "deduction_type_picker_ui.cjs"],
        ["test-node", "tests/support/browser-suite.cjs", "rotation_daily_ui.cjs", "rotation_formal_ui.cjs"],
    ]


def test_browser_suite_preserves_fixture_failure_and_continues_other_domains(monkeypatch, tmp_path, capsys):
    calls = prepare(monkeypatch, tmp_path, ["activities_ui.cjs", "rotation_daily_ui.cjs", "rotation_formal_ui.cjs"])

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        results = [{"name": name, "passed": name != "rotation_daily_ui.cjs"} for name in command[2:]]
        lines = ["Fixture output\n", run_all.BROWSER_RESULT_PREFIX + json.dumps(results)]
        return nullcontext(SimpleNamespace(stdout=iter(lines), wait=lambda: int(not all(r["passed"] for r in results))))

    monkeypatch.setattr(run_all.subprocess, "Popen", launch)
    assert run_all.main(["--suite", "browser"]) == 1
    output = capsys.readouterr().out
    assert "2 passed, 1 failed" in output
    assert "failed: rotation_daily_ui.cjs" in output
    assert "Fixture output" in output


def test_browser_suite_fails_closed_without_complete_results(monkeypatch, tmp_path, capsys):
    prepare(monkeypatch, tmp_path, ["rotation_daily_ui.cjs", "rotation_formal_ui.cjs"])
    monkeypatch.setattr(run_all.subprocess, "Popen", lambda *args, **kwargs:
                        nullcontext(SimpleNamespace(stdout=iter(()), wait=lambda: 0)))
    assert run_all.main(["--suite", "browser"]) == 1
    output = capsys.readouterr()
    assert "0 passed, 2 failed" in output.out
    assert "expected one fixture result summary" in output.err


def test_nonzero_test_result_is_not_hidden(monkeypatch, tmp_path, capsys):
    calls = prepare(monkeypatch, tmp_path, ["a.cjs", "b.cjs"])
    monkeypatch.setattr(run_all.subprocess, "Popen", fake_popen(calls, returncode=1))
    assert run_all.main(["--suite", "node"]) == 1
    assert "0 passed, 2 failed" in capsys.readouterr().out


def test_launch_error_is_reported_as_failure(monkeypatch, tmp_path, capsys):
    prepare(monkeypatch, tmp_path, ["test_a.py"])

    def fail(*args, **kwargs):
        raise OSError("runtime removed")

    monkeypatch.setattr(run_all.subprocess, "Popen", fail)
    assert run_all.main(["--suite", "python"]) == 1
    assert "runtime removed" in capsys.readouterr().err


def test_empty_suite_cannot_pass(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["test_a.py"])
    assert run_all.main(["--suite", "node"]) == 2
    assert not calls


def test_esm_browser_entry_is_a_browser_case_and_support_is_not_a_test(monkeypatch, tmp_path):
    calls = prepare(monkeypatch, tmp_path, ["frontend_modules.cjs", "view_lifecycle.cjs"])
    support = run_all.HERE / "support"
    support.mkdir()
    (support / "browser-suite.cjs").touch()
    assert run_all.main(["--suite", "browser"]) == 0
    assert len(calls) == 2
    assert calls[-1][0] == ["test-node", "tests/support/browser-suite.cjs", "frontend_modules.cjs"]
