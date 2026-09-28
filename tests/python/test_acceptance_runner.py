"""AcceptanceRunner deterministic checks."""

import json
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


# ===== 简写验收规格归一化（2026-09-28，B 标准 S4 回归）=====
# 背景：replan 生成的修复步骤输出 {"file_exists": "..."}（缺 "type" 键），
# 旧行为判为未知类型 fail-closed，现象是「工具成功、文件已落盘、验收却失败」。


def test_shorthand_file_exists_is_normalised(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "a_test.py").write_text("x", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"file_exists": "output/a_test.py"}])

    assert result.passed
    assert result.checks[0]["type"] == "file_exists"


def test_shorthand_missing_file_still_fails(tmp_path):
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"file_exists": "output/nope.py"}])

    assert not result.passed
    assert result.checks[0]["type"] == "file_exists"
    assert "未知验收类型" not in json.dumps(result.checks[0], ensure_ascii=False)


def test_shorthand_dict_value_is_merged(tmp_path):
    (tmp_path / "m.txt").write_text("needle-here", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"content_contains": {"path": "m.txt", "text": "needle"}}])

    assert result.passed
    assert result.checks[0]["type"] == "content_contains"


def test_shorthand_alias_is_resolved(tmp_path):
    (tmp_path / "art.bin").write_text("x", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"artifact_exists": "art.bin"}])

    assert result.passed
    assert result.checks[0]["type"] == "file_exists"


def test_shorthand_exit_code_scalar(tmp_path):
    runner = AcceptanceRunner(tmp_path)

    assert runner.run([{"exit_code": 0}], {"returncode": 0}).passed
    assert not runner.run([{"exit_code": 0}], {"returncode": 1}).passed


def test_shorthand_diff_scope_list(tmp_path):
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"diff_scope": ["a.txt"]}])

    assert result.checks[0]["type"] == "diff_scope"


def test_typo_type_name_is_not_silently_fixed(tmp_path):
    """拼错的类型名不能被"修好"，必须继续 fail-closed。"""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"file_exist": "a.txt"}])

    assert not result.passed
    assert "未知验收类型" in json.dumps(result.checks[0], ensure_ascii=False)


def test_multi_key_shorthand_is_not_expanded(tmp_path):
    """多键且无 type 的对象形状不明确，不做猜测，继续 fail-closed。"""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"file_exists": "a.txt", "kind": "file"}])

    assert not result.passed


def test_explicit_type_takes_precedence(tmp_path):
    """已经带 type 的规格不受归一化影响。"""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    runner = AcceptanceRunner(tmp_path)

    result = runner.run([{"type": "file_exists", "path": "a.txt", "kind": "file"}])

    assert result.passed

