# -*- coding: utf-8 -*-
"""test_passes 验收类型（动作 B 第一级：测试即验收）单元测试。

覆盖：通过 / 失败 / 未登记模板 / 缺 suite / 超时 / args 追加 / 别名 /
timeout 上限 / 非法 args。subprocess 全部 mock，不真跑测试进程。
"""

import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, "python")

from acceptance_runner import AcceptanceRunner


def _completed(rc=0, stdout="", stderr=""):
    proc = mock.Mock()
    proc.returncode = rc
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_test_passes_green(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    with mock.patch("acceptance_runner.subprocess.run", return_value=_completed(0)) as run_mock:
        result = runner.run({"type": "test_passes", "suite": "pytest", "test_path": "tests/test_x.py"})

    assert result.passed
    check = result.checks[0]
    assert check["type"] == "test_passes"
    assert check["evidence"]["exit_code"] == 0
    assert Path(check["evidence"]["cwd"]) == tmp_path
    argv = check["evidence"]["argv"]
    assert "-m" in argv and "pytest" in argv and "tests/test_x.py" in argv
    run_mock.assert_called_once()
    kwargs = run_mock.call_args.kwargs
    assert kwargs["shell"] is False
    assert Path(kwargs["cwd"]) == tmp_path


def test_test_passes_red(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    with mock.patch(
        "acceptance_runner.subprocess.run",
        return_value=_completed(1, stdout="short test summary info\n1 failed", stderr="AssertionError: boom"),
    ):
        result = runner.run({"type": "test_passes", "suite": "pytest"})

    assert not result.passed
    check = result.checks[0]
    assert "测试运行失败" in check["reason"]
    assert check["evidence"]["exit_code"] == 1
    assert "1 failed" in check["evidence"]["stdout_tail"]
    assert "AssertionError" in check["evidence"]["stderr_tail"]


def test_test_passes_unknown_suite_fail_closed(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    result = runner.run({"type": "test_passes", "suite": "rm -rf /"})
    assert not result.passed
    assert "未登记的测试模板" in result.checks[0]["reason"]
    assert "pytest" in result.checks[0]["reason"]


def test_test_passes_missing_suite_fail_closed(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    result = runner.run({"type": "test_passes"})
    assert not result.passed
    assert "缺少 suite" in result.checks[0]["reason"]


def test_test_passes_timeout_fail_closed(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    with mock.patch(
        "acceptance_runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=5),
    ):
        result = runner.run({"type": "test_passes", "suite": "pytest", "timeout": 5})
    assert not result.passed
    assert "超时" in result.checks[0]["reason"]


def test_test_passes_args_appended_as_argv(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    with mock.patch("acceptance_runner.subprocess.run", return_value=_completed(0)) as run_mock:
        runner.run({"type": "test_passes", "suite": "pytest", "args": ["-k", "ok"]})
    argv = run_mock.call_args.args[0]
    assert argv[-2:] == ["-k", "ok"]


def test_test_passes_aliases(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    with mock.patch("acceptance_runner.subprocess.run", return_value=_completed(0)):
        assert runner.run({"type": "test_pass", "suite": "pytest"}).passed
        assert runner.run({"type": "tests_pass", "suite": "pytest"}).passed


def test_test_passes_invalid_args_fail_closed(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    result = runner.run({"type": "test_passes", "suite": "pytest", "args": "not-a-list"})
    assert not result.passed
    assert "args 必须是列表" in result.checks[0]["reason"]


def test_test_passes_timeout_capped(tmp_path):
    runner = AcceptanceRunner(tmp_path)
    with mock.patch("acceptance_runner.subprocess.run", return_value=_completed(0)) as run_mock:
        runner.run({"type": "test_passes", "suite": "pytest", "timeout": 99999})
    assert run_mock.call_args.kwargs["timeout"] == 900.0
