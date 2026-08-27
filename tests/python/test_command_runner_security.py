# -*- coding: utf-8 -*-
"""
阶段1.2回归测试：command_runner 危险命令拦截 + 工作目录白名单
"""
import os

import pytest

from tools.command_runner import CommandRunner, _resolve_workspace_root


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf ~",
        "sudo shutdown now",
        "reboot",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        "del /s /q C:\\",
        "rd /s /q C:\\",
        "diskpart",
        "reg delete HKLM\\Software /f",
    ],
)
def test_dangerous_commands_blocked(cmd):
    result = CommandRunner.run_command(command=cmd, timeout=5)
    assert result.get("success") is False
    assert result.get("error") == "Dangerous command blocked"


def test_legit_echo_command_runs():
    result = CommandRunner.run_command(command="echo hello", timeout=10)
    assert result.get("success") is True


def test_legit_dir_or_ls_command_runs():
    result = CommandRunner.run_command(command="dir" if os.name == "nt" else "ls", timeout=10)
    assert result.get("success") is True


def test_cwd_inside_workspace_allowed():
    workspace_root = _resolve_workspace_root()
    result = CommandRunner.run_command(command="echo test", cwd=str(workspace_root), timeout=10)
    assert result.get("success") is True


def test_cwd_outside_workspace_rejected():
    outside_dir = "C:\\Windows\\System32" if os.name == "nt" else "/etc"
    result = CommandRunner.run_command(command="echo test", cwd=outside_dir, timeout=10)
    assert result.get("success") is False
    assert result.get("error") == "Workdir not allowed"


@pytest.mark.parametrize("cmd", ["", "   "])
def test_empty_command_rejected(cmd):
    result = CommandRunner.run_command(command=cmd, timeout=5)
    assert result.get("success") is False
    assert result.get("error") == "Missing parameter"


def test_command_in_workspace_subdir_runs(tmp_path):
    result = CommandRunner.run_command(
        command="echo hello > _cmdrunner_test_tmp.txt",
        cwd=str(tmp_path),
        timeout=10,
    )
    # tmp_path is outside the workspace root, so this should be rejected by the
    # cwd allowlist. This test documents that behavior explicitly.
    assert result.get("success") is False
    assert result.get("error") == "Workdir not allowed"


def test_command_in_real_workspace_subdir_runs():
    workspace_root = _resolve_workspace_root()
    test_subdir = workspace_root / "python" / "data"
    if not test_subdir.exists():
        pytest.skip("python/data directory does not exist in this environment")

    tmp_file = test_subdir / "_cmdrunner_test_tmp.txt"
    try:
        result = CommandRunner.run_command(
            command="echo hello > _cmdrunner_test_tmp.txt",
            cwd=str(test_subdir),
            timeout=10,
        )
        assert result.get("success") is True
    finally:
        if tmp_file.exists():
            tmp_file.unlink()
