# -*- coding: utf-8 -*-
"""json_repair 修复器测试。"""
import pytest

from json_repair import escape_bare_control_chars, parse_loose, repair_json_text


def test_trailing_comma_fixed():
    src = '{"a": 1, "b": 2,}'
    assert parse_loose(src) == {"a": 1, "b": 2}


def test_bare_newline_inside_string_value_fixed():
    # 多行代码内容输出时的头号错误：字符串值里有真实换行
    src = '{"status": "completed", "output": {"content": "def f():\n    return 1\n"}}'
    result = parse_loose(src)
    assert result is not None
    assert result["output"]["content"] == "def f():\n    return 1\n"


def test_markdown_fence_plus_trailing_comma_combined():
    src = '```json\n{"status": "completed", "output": {"path": "a.py", "content": "x = 1\n",},}\n```'
    result = parse_loose(src)
    assert result is not None
    assert result["output"]["content"] == "x = 1\n"


def test_truncated_json_not_repaired():
    src = '{"status": "completed", "output": {"content": "def f():'
    assert parse_loose(src) is None


def test_single_quotes_not_touched():
    # 单引号不做危险修复（内容里合法单引号会被误伤）——失败返回 None
    src = "{'a': 1}"
    assert parse_loose(src) is None


def test_escape_state_machine_preserves_content():
    src = '{"k": "line1\\nline2\\tT"}'  # 已转义的保持不变
    result = parse_loose(src)
    assert result["k"] == "line1\nline2\tT"


def test_repair_json_text_no_change_on_clean():
    src = '{"a": 1}'
    assert repair_json_text(src) == src


def test_escape_bare_control_chars_multiple_strings():
    src = '{"a": "x\ny", "b": 1, "c": "p\tq"}'
    out = escape_bare_control_chars(src)
    assert "\\ny" in out
    assert "p\\tq" in out
