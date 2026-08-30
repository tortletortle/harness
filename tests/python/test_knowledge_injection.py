# -*- coding: utf-8 -*-
"""
知识注入层（knowledge/injection.py）回归测试 + Designer/coding_agent 接线验证。
全部离线；临时工作区。
"""
import json

import pytest

from llm_client import LLMClient
from tools.code_context import CodeContext


@pytest.fixture()
def tmp_ws(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.workspace.resolve_workspace_root", lambda: tmp_path)
    return tmp_path


# ===== build_design_memory_block =====

def test_memory_block_contains_note(tmp_ws):
    from knowledge.injection import build_design_memory_block

    CodeContext.remember("部署前必须跑 release-checklist 回归，避免坏构建上线。", title="发布检查单")
    block = build_design_memory_block("部署 发布 上线 检查")
    assert block is not None
    assert "data/knowledge/notes" in block
    assert len(block) <= 2000


def test_memory_block_none_on_empty_workspace(tmp_ws):
    from knowledge.injection import build_design_memory_block

    assert build_design_memory_block("随便写点什么东西") is None


def test_memory_block_fail_open_on_search_error(tmp_ws, monkeypatch):
    from knowledge.injection import build_design_memory_block

    def boom(*a, **kw):
        raise RuntimeError("index broken")

    monkeypatch.setattr(CodeContext, "search", staticmethod(boom))
    assert build_design_memory_block("任何需求") is None


def test_memory_block_none_on_empty_request(tmp_ws):
    from knowledge.injection import build_design_memory_block

    assert build_design_memory_block("") is None
    assert build_design_memory_block("   ") is None


def test_memory_block_budget_respected(tmp_ws):
    from knowledge.injection import build_design_memory_block

    for i in range(3):
        CodeContext.remember(f"keyword-marker-{i} 的说明内容，用来撑爆预算的一些文字" * 10, title=f"笔记{i}")
    block = build_design_memory_block("keyword-marker", max_chars=300)
    assert block is not None
    assert len(block) <= 300


# ===== build_repo_map_block =====

def test_repo_map_block_contains_symbols(tmp_ws):
    app = tmp_ws / "app"
    app.mkdir()
    (app / "models.py").write_text("class Invoice:\n    def total(self):\n        return 0\n", encoding="utf-8")
    from knowledge.injection import build_repo_map_block

    block = build_repo_map_block(2500)
    assert block is not None
    assert "app/models.py" in block
    assert "Invoice" in block


def test_repo_map_block_none_when_no_files(tmp_ws):
    from knowledge.injection import build_repo_map_block

    assert build_repo_map_block() is None


# ===== Designer 接线：generate_design 提示词包含记忆块 =====

class _CapturingProvider:
    def __init__(self, response):
        self._name = "deterministic"
        self._response = response
        self.calls = []

    @property
    def name(self):
        return self._name

    async def chat(self, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
        self.calls.append(list(messages))
        from llm_client import LLMResponse

        return LLMResponse(content=self._response, model=model, usage=None,
                           finish_reason="stop", tool_calls=None)

    async def health_check(self):
        return True


_DESIGN_JSON = json.dumps({
    "architecture": {"components": ["app"], "interactions": ["无"]},
    "difficulty": {"overall": "低", "breakdown": {}},
    "required_tools": [{"tool_id": "coding_agent", "availability": "available"}],
    "overall_tool_completeness": 1.0,
    "risks": [],
}, ensure_ascii=False)


class _MemCache:
    """内存版设计书缓存：隔离仓库根的 data/design_cache.json（落盘缓存会让重跑命中旧设计）"""

    def __init__(self):
        self._d = {}

    def get(self, user_request, fingerprint=None):
        return self._d.get((user_request, fingerprint))

    def set(self, user_request, data, fingerprint=None):
        self._d[(user_request, fingerprint)] = data


def _make_designer_with_cache(provider):
    llm = LLMClient()
    llm.register_provider(provider)
    designer = DefaultDesigner(llm, {"primary": {"provider": "deterministic", "model": "m", "temperature": 0.3}})
    designer.design_cache = _MemCache()
    return designer


def test_designer_generate_design_injects_memory(tmp_ws):
    import asyncio
    from designer import DefaultDesigner

    CodeContext.remember("部署前必须跑 release-checklist 回归。", title="发布检查单")

    provider = _CapturingProvider(_DESIGN_JSON)
    designer = _make_designer_with_cache(provider)

    design = asyncio.run(designer.generate_design("部署 发布 检查 相关的需求"))
    assert design is not None

    system_prompt = provider.calls[0][0].content
    assert "相关历史经验与代码检索" in system_prompt
    assert "data/knowledge/notes" in system_prompt


def test_designer_generate_design_no_memory_no_section(tmp_ws):
    import asyncio

    provider = _CapturingProvider(_DESIGN_JSON)
    designer = _make_designer_with_cache(provider)

    asyncio.run(designer.generate_design("一个全新的从未见过的需求"))

    system_prompt = provider.calls[0][0].content
    assert "相关历史经验与代码检索" not in system_prompt


# ===== coding_agent 接线：_build_prompt 附仓库地图 =====

def test_coding_agent_prompt_includes_repo_map(tmp_ws):
    from capabilities import CapabilityRequest
    from coding_agent import CodingAgentTool

    app = tmp_ws / "src"
    app.mkdir()
    (app / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    request = CapabilityRequest(
        work_item_id="w1",
        goal="修改 src/main.py",
        inputs={"path": "src/main.py", "instruction": "把返回值改成 2", "allowed_tools": ["text_editor"]},
    )
    prompt = CodingAgentTool._build_prompt(request, "src/main.py", "def run():\n    return 1\n", "", "【仓库地图】\nsrc/main.py:")
    assert "仓库地图" in prompt
    assert "src/main.py" in prompt


def test_coding_agent_prompt_without_map(tmp_ws):
    from capabilities import CapabilityRequest
    from coding_agent import CodingAgentTool

    request = CapabilityRequest(
        work_item_id="w1",
        goal="修改 src/main.py",
        inputs={"path": "src/main.py", "instruction": "x"},
    )
    prompt = CodingAgentTool._build_prompt(request, "src/main.py", "content", "")
    assert "仓库地图" not in prompt
