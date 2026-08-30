# -*- coding: utf-8 -*-
"""
lint_gate 工具化回归：独立检查函数 + 工具层包装 + 注册/推断接线。
全部离线（纯 compile 检查，无子进程、无网络）。
"""
import pytest

from tools.lint_gate import LintGateTool, lint_file_content
from tools import get_registry
from executor import DefaultExecutor

VALID_PY = "def ok():\n    return 1\n"
INVALID_PY = "def broken(:\n    return 1\n"


# ===== lint_file_content 单元（独立检查无旧内容豁免语义） =====

def test_non_python_file_skipped():
    gate = lint_file_content("a.md", INVALID_PY)
    assert gate.allowed is True
    assert "非 .py" in gate.detail


def test_valid_content_passes():
    gate = lint_file_content("a.py", VALID_PY)
    assert gate.allowed is True and gate.detail is None


def test_invalid_content_fails_even_without_old():
    """与写入门禁 lint_python 的关键差异：独立检查没有旧内容豁免。"""
    gate = lint_file_content("a.py", INVALID_PY)
    assert gate.allowed is False
    assert "语法检查未通过" in gate.detail
    assert "第 1 行" in gate.detail


# ===== 工具层 =====

def test_metadata_complete():
    meta = LintGateTool.get_metadata()
    assert meta["tool_id"] == "lint_gate"
    assert meta["category"] == "test"
    assert meta["version"]
    instructions = meta["capabilities"]["instructions"]
    assert {i["name"] for i in instructions} == {"lint_file"}
    for item in instructions:
        assert item["description"]
        assert item["parameters"]
        assert all(str(v).strip() for v in item["parameters"].values())


def test_lint_good_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "good.py").write_text(VALID_PY, encoding="utf-8")
    result = LintGateTool.execute("lint_file", {"path": "good.py"})
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["path"] == "good.py"


def test_lint_broken_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "bad.py").write_text(INVALID_PY, encoding="utf-8")
    result = LintGateTool.execute("lint_file", {"path": "bad.py"})
    assert result["success"] is False
    assert result["exit_code"] == 1
    assert "语法检查未通过" in result["message"]
    # blocked 语义留给"不该执行"（缺文件/缺参数），坏语法是"执行了但失败"
    assert not result.get("blocked")


def test_lint_non_python_file_reports_skip(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "note.md").write_text(INVALID_PY, encoding="utf-8")
    result = LintGateTool.execute("lint_file", {"path": "note.md"})
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert "非 .py" in (result.get("detail") or "")


def test_missing_file_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    result = LintGateTool.execute("lint_file", {"path": "nope.py"})
    assert result["success"] is False
    assert result.get("blocked") is True
    assert result["exit_code"] == 1


def test_missing_path_blocked():
    result = LintGateTool.execute("lint_file", {})
    assert result["success"] is False
    assert result.get("blocked") is True


def test_unknown_instruction_rejected():
    result = LintGateTool.execute("compile_all", {"path": "a.py"})
    assert result["success"] is False
    assert result["error"] == "Unknown instruction"


# ===== 注册与推断接线 =====

def test_registered_in_tool_registry():
    registry = get_registry()
    assert registry.get_tool("lint_gate") is LintGateTool
    result = registry.execute_tool("lint_gate", "lint_file", {"path": "also_missing.py"})
    assert result["success"] is False
    assert result.get("blocked") is True


def test_inferred_instruction_accepted():
    """推断出 lint_file，且确实被工具 execute 接受（对照
    test_instruction_inference.py 的口径：推断名必须是工具真支持的指令）。"""
    executor = DefaultExecutor(None, {})
    assert executor._infer_instruction("lint_gate", {"path": "a.py"}) == "lint_file"
