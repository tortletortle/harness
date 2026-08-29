"""AcceptanceRunner deterministic checks."""

import subprocess
from pathlib import Path

from acceptance_runner import AcceptanceRunner


def test_file_exists_and_content_contains(tmp_path):
    path = tmp_path / "src" / "main.py"
    path.parent.mkdir()
    path.write_text("print('hello')\n", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([
        {"type": "file_exists", "path": "src/main.py", "kind": "file"},
        {"type": "content_contains", "path": "src/main.py", "text": "hello"},
    ])

    assert result.passed
    assert result.status == "passed"
    assert result.reward == 1.0
    assert all(check["passed"] for check in result.checks)
    assert result.checks[1]["evidence"]["sha256"]


def test_missing_file_and_wrong_content_fail_closed(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    result = runner.run([
        {"type": "file_exists", "path": "missing.txt"},
        {"type": "content_contains", "path": "missing.txt", "text": "x"},
    ])

    assert not result.passed
    assert result.status == "failed"
    assert result.reward == 0.0
    assert all(not check["passed"] for check in result.checks)


def test_file_kind_and_path_traversal(tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    assert runner.run({"type": "file_exists", "path": "folder", "kind": "directory"}).passed
    assert not runner.run({"type": "file_exists", "path": "file.txt", "kind": "directory"}).passed
    traversal = runner.run({"type": "file_exists", "path": "../outside"})
    assert not traversal.passed
    assert "fail-closed" in traversal.checks[0]["reason"]


def test_exit_code_uses_tool_result_without_rerunning():
    runner = AcceptanceRunner(workspace_root=Path.cwd())
    assert runner.run({"type": "exit_code", "equals": 0}, {"returncode": 0}).passed
    failed = runner.run({"type": "command_exit_code", "equals": 0}, {"returncode": 2})
    assert not failed.passed
    missing = runner.run({"type": "exit_code", "equals": 0}, {})
    assert not missing.passed
    assert "returncode" in missing.checks[0]["reason"]


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_diff_scope_includes_untracked_staged_and_unstaged(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Acceptance Test")
    (tmp_path / "tracked.txt").write_text("v1", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    (tmp_path / "tracked.txt").write_text("v2", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("staged", encoding="utf-8")
    _git(tmp_path, "add", "staged.txt")
    (tmp_path / "untracked.txt").write_text("new", encoding="utf-8")

    result = AcceptanceRunner(tmp_path).run({
        "type": "diff_scope",
        "allowed": ["tracked.txt", "staged.txt", "untracked.txt"],
    })
    assert result.passed
    assert result.checks[0]["evidence"]["actual"] == ["staged.txt", "tracked.txt", "untracked.txt"]

    extra = AcceptanceRunner(tmp_path).run({"type": "diff_scope", "allowed": ["tracked.txt"]})
    assert not extra.passed
    assert "staged.txt" in extra.checks[0]["evidence"]["unexpected"]


def test_unknown_and_malformed_specs_fail_closed(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    unknown = runner.run({"type": "run_arbitrary_code"})
    assert not unknown.passed
    assert "未知验收类型" in unknown.checks[0]["reason"]
    malformed = runner.run(["not-a-check"])
    assert not malformed.passed
    assert malformed.status == "failed"


def test_aliases_are_supported(tmp_path):
    (tmp_path / "a.txt").write_text("ok", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)
    result = runner.run([
        {"type": "artifact_exists", "path": "a.txt"},
        {"type": "content_contains", "path": "a.txt", "content": "ok"},
    ])
    assert result.passed
