# -*- coding: utf-8 -*-
"""
接线完整性守卫（抗并行重写）。

背景：仓库存在并行开发会话，曾出现"已接线的代码块被重写抹掉、运行时才炸"的情况
（designer f-string 占位符在、计算块消失 → NameError）。本文件把接线不变量固化为
断言：任何一次重写破坏接线，下一次回归立刻红灯，而不是等运行时。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_tools_registry_contains_new_tools():
    text = _read("python/tools/__init__.py")
    assert 'self.register_tool("super_search", SuperSearch)' in text
    assert 'self.register_tool("code_context", CodeContext)' in text
    assert "from .super_search import SuperSearch" in text
    assert "from .code_context import CodeContext" in text


def test_designer_memory_injection_complete():
    """计算块与 f-string 引用必须成对存在——半个接线会在运行时 NameError。"""
    text = _read("python/designer/__init__.py")
    assert "build_design_memory_block" in text, "记忆注入计算块被移除"
    assert "memory_section = \"\"" in text, "memory_section 初始化被移除"
    assert "memory_section = \"\\n\" + memory_block + \"\\n\"" in text, "memory_section 赋值被移除"
    assert "{memory_section}" in text or 'locals().get("memory_section"' in text, \
        "system_prompt 中的记忆块引用被移除"


def test_coding_agent_repo_map_wiring_complete():
    """签名、方法体插入、调用点必须一致——缺一处就是运行时 TypeError。"""
    text = _read("python/coding_agent.py")
    assert "repo_map_block: str = \"\"" in text, "_build_prompt 签名缺 repo_map_block"
    assert 'map_block = f"\\n{repo_map_block}\\n"' in text, "map_block 插入逻辑被移除"
    assert 'f"{map_block}"' in text, "map_block 未拼进提示词"
    assert "build_repo_map_block" in text, "run() 不再调用仓库地图构建"


def test_executor_infers_new_tool_instructions():
    text = _read("python/executor/__init__.py")
    assert 'elif tool_id == "super_search":' in text
    assert 'elif tool_id == "code_context":' in text


def test_budget_gate_and_usage_hook_in_place():
    server = _read("python/api/server.py")
    assert "budget_max_tokens_per_plan" in server, "预算闸门被移除"
    assert "/api/usage" in server, "用量端点被移除"
    llm = _read("python/llm_client/__init__.py")
    assert "record_usage" in llm, "LLM 用量钩子被移除"
    gate = _read("python/tools/lint_gate.py")
    assert "def lint_python" in gate
    tracker = _read("python/utils/usage_tracker.py")
    assert "def record_usage" in tracker


def test_search_assistant_prefers_super_search():
    text = _read("python/tools/__init__.py")
    assert "super_search" in text, "search_assistant 的 super_search 优先逻辑被移除"


def test_injection_module_fail_open_contract():
    text = _read("python/knowledge/injection.py")
    assert "return None" in text, "注入层必须 fail-open（返回 None）"
    assert "except Exception" in text, "注入层必须有兜底异常处理"
