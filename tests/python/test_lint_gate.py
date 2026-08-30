# -*- coding: utf-8 -*-
"""
lint_gate（SWE-agent 式语法门禁）+ text_editor/file_creator 接线 +
command_runner 空输出确认与 Docker 沙箱 回归测试。全部离线。
"""
import subprocess
import sys
import types

import pytest

from tools.lint_gate import lint_python
from tools.text_editor import TextEditor
from tools.file_creator import FileCreator
from tools import command_runner as cr_mod
from tools.command_runner import CommandRunner

VALID_PY = "def ok():\n    return 1\n"
INVALID_PY = "def broken(:\n    return 1\n"


# ===== lint_python 单元 =====

def test_non_python_file_always_allowed(tmp_path):
    assert lint_python(str(tmp_path / "a.md"), INVALID_PY, None).allowed is True


def test_valid_new_content_allowed(tmp_path):
    gate = lint_python(str(tmp_path / "a.py"), VALID_PY, None)
    assert gate.allowed is True and gate.detail is None


def test_invalid_new_content_rejected_when_file_absent(tmp_path):
    gate = lint_python(str(tmp_path / "a.py"), INVALID_PY, None)
    assert gate.allowed is False
    assert "语法检查未通过" in gate.detail


def test_invalid_new_content_rejected_when_old_valid(tmp_path):
    gate = lint_python(str(tmp_path / "a.py"), INVALID_PY, VALID_PY)
    assert gate.allowed is False


def test_invalid_new_content_allowed_when_old_broken(tmp_path):
    gate = lint_python(str(tmp_path / "a.py"), INVALID_PY, INVALID_PY)
    assert gate.allowed is True
    assert "语法错误" in gate.detail


# ===== text_editor 接线 =====

def test_text_editor_append_rejects_invalid_python(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(VALID_PY, encoding="utf-8")
    result = TextEditor.execute("append_to_file", {
        "path": str(target), "content": "\ndef broken(:\n",
    })
    assert result["success"] is False
    assert result["blocked"] is True
    assert "语法" in result["message"]
    # 文件内容未被破坏
    assert target.read_text(encoding="utf-8") == VALID_PY


def test_text_editor_append_force_write_bypasses_gate(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(VALID_PY, encoding="utf-8")
    result = TextEditor.execute("append_to_file", {
        "path": str(target), "content": "\ndef broken(:\n", "force_write": True,
    })
    assert result["success"] is True


def test_text_editor_append_valid_python_ok(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(VALID_PY, encoding="utf-8")
    result = TextEditor.execute("append_to_file", {
        "path": str(target), "content": "\ndef also_ok():\n    return 2\n",
    })
    assert result["success"] is True
    assert "also_ok" in target.read_text(encoding="utf-8")


def test_text_editor_replace_making_file_invalid_is_blocked(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(VALID_PY, encoding="utf-8")
    result = TextEditor.execute("replace_in_file", {
        "path": str(target), "old_text": "return 1", "new_text": "return (",
    })
    assert result["success"] is False
    assert result["blocked"] is True
    assert target.read_text(encoding="utf-8") == VALID_PY


def test_text_editor_append_to_broken_file_warns_but_writes(tmp_path):
    target = tmp_path / "mod.py"
    target.write_text(INVALID_PY, encoding="utf-8")
    result = TextEditor.execute("append_to_file", {
        "path": str(target), "content": "\n# note\n",
    })
    assert result["success"] is True
    assert "syntax_warning" in result


def test_text_editor_write_file_new_invalid_py_blocked(tmp_path):
    result = TextEditor.execute("write_file", {
        "path": str(tmp_path / "new.py"), "content": INVALID_PY,
    })
    assert result["success"] is False
    assert result["blocked"] is True


def test_text_editor_md_file_never_gated(tmp_path):
    result = TextEditor.execute("write_file", {
        "path": str(tmp_path / "note.md"), "content": INVALID_PY,  # md 不检查
    })
    assert result["success"] is True


# ===== file_creator 接线 =====

def test_file_creator_rejects_invalid_python(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    result = FileCreator.execute("create_file", {
        "path": "broken.py", "content": INVALID_PY,
    })
    assert result["success"] is False
    assert result["blocked"] is True
    assert not (tmp_path / "broken.py").exists()


def test_file_creator_valid_python_ok(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    result = FileCreator.execute("create_file", {
        "path": "good.py", "content": VALID_PY,
    })
    assert result["success"] is True


# ===== command_runner：空输出确认 =====

def test_command_runner_empty_output_explicit():
    result = CommandRunner.run_command(command="exit 0", shell=True, timeout=30)
    assert result["success"] is True
    assert result.get("empty_output") is True
    assert "无输出" in result["message"]


def test_command_runner_nonempty_output_no_flag():
    marker = "EAS-ACI-OUT-777"
    cmd = f"echo {marker}" if sys.platform != "win32" else f"echo {marker}"
    result = CommandRunner.run_command(command=cmd, shell=True, timeout=30)
    assert result["success"] is True
    assert result.get("empty_output") is False


# ===== command_runner：Docker 沙箱 =====

@pytest.fixture()
def sandbox_ws(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    return tmp_path


def test_sandbox_disabled_by_default(sandbox_ws, monkeypatch):
    monkeypatch.delenv("EAS_SANDBOX", raising=False)
    captured = {}
    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(cr_mod.subprocess, "run", fake_run)
    result = CommandRunner.run_command(command="echo hi", shell=True, timeout=10)
    # 未启用沙箱：走原生路径，argv 不是 docker 前缀
    assert result["success"] is True
    assert "sandbox" not in result
    assert "docker" not in str(captured["argv"][:1])
    _ = real_run


def test_sandbox_docker_wraps_command(sandbox_ws, monkeypatch):
    monkeypatch.setenv("EAS_SANDBOX", "docker")
    monkeypatch.setenv("EAS_SANDBOX_IMAGE", "py-img:1")
    monkeypatch.setenv("EAS_SANDBOX_NETWORK", "none")
    monkeypatch.setattr(cr_mod.shutil, "which", lambda name: "C:/docker/docker.exe")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout=b"sandbox-ok", stderr=b"")

    monkeypatch.setattr(cr_mod.subprocess, "run", fake_run)
    result = CommandRunner.run_command(command="pytest -q", shell=True, timeout=10)
    assert result["success"] is True
    assert result["sandbox"] == "docker"
    argv = captured["argv"]
    assert argv[0] == "C:/docker/docker.exe"
    assert "--network=none" in argv
    assert f"{sandbox_ws}:/workspace" in argv
    assert "/workspace" in argv
    assert "py-img:1" in argv
    assert argv[-3:] == ["sh", "-lc", "pytest -q"]


def test_sandbox_fails_closed_without_docker(sandbox_ws, monkeypatch):
    monkeypatch.setenv("EAS_SANDBOX", "docker")
    monkeypatch.setattr(cr_mod.shutil, "which", lambda name: None)
    result = CommandRunner.run_command(command="echo hi", shell=True, timeout=10)
    assert result["success"] is False
    assert result["blocked"] is True
    assert "Docker" in result["error"]
