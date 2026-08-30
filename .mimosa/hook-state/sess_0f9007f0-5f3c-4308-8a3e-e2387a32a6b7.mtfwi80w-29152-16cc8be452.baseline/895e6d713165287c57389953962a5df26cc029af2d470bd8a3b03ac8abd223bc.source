"""
拒绝→反馈→修订→计划生成 的反馈传递链路测试。

缺陷背景：用户拒绝设计书时给出的具体要求（如文件名、相对路径）只用于
修订设计书结构；设计书结构（组件/难度/工具评估）没有该反馈的落点，
而计划生成只看原始需求 → 反馈在计划环节蒸发。

修复契约：
1. revise_design 返回的修订设计书必须携带 user_comments = 反馈原文
2. 同 design_id 的旧计划缓存必须失效（反馈改变计划约束）
3. generate_plan 的提示词必须包含审批反馈（若存在）
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from designer import DefaultDesigner
from cache import DesignCache, PlanCache
from models import (
    DesignDocument,
    DesignPart1,
    Architecture,
    Difficulty,
    ToolAssessment,
)

MODEL_CONFIG = {"primary": {"provider": "stub", "model": "stub-model", "temperature": 0.1}}

REVISION_JSON = json.dumps({
    "architecture": {
        "components": ["文件创建模块", "文件验证模块"],
        "interactions": ["创建后立即验证"],
    },
    "difficulty": {"overall": "easy", "breakdown": {"文件": "easy"}},
    "required_tools": [
        {"tool_id": "file_creator", "availability": "available",
         "quality": "high", "limitations": "相对路径基于工作区根目录解析", "impact": "直接使用"}
    ],
    "overall_tool_completeness": 1.0,
    "risks": [],
})

PLAN_JSON = json.dumps({
    "segments": [{
        "description": "创建文件",
        "steps": [{
            "description": "创建反馈指定文件",
            "tool": "file_creator",
            "inputs": {"path": "llm_ws_test.txt", "content": "workspace-ok"}
        }]
    }],
    "implementation_list": ["llm_ws_test.txt"],
})

FEEDBACK = "文件名固定为 llm_ws_test.txt，内容写 workspace-ok，路径用相对路径。"


class _ScriptedLLM:
    """按顺序返回预设响应的 LLM 桩，记录全部调用消息。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, *, provider_name, messages, model, temperature=0.7, max_tokens=4000, **kwargs):
        self.calls.append(messages)
        content = self.responses.pop(0)
        return SimpleNamespace(content=content, model=model, finish_reason="stop")


class SimpleNamespace:
    pass


def _make_designer(llm, cache_dir: Path):
    from types import SimpleNamespace as _SN
    designer = DefaultDesigner(llm, dict(MODEL_CONFIG), dispatcher=None)
    designer.design_cache = DesignCache(cache_file=str(cache_dir / "design_cache.json"))
    designer.plan_cache = PlanCache(cache_file=str(cache_dir / "plan_cache.json"))
    return designer


def _make_design() -> DesignDocument:
    return DesignDocument(
        design_id=uuid4(),
        user_request="创建一个测试用的文本文件，写入当前日期",
        timestamp=datetime.now(),
        part1_design=DesignPart1(
            architecture=Architecture(components=["文件创建模块"], interactions=["写入文件"]),
            difficulty=Difficulty(overall="easy", breakdown={}),
        ),
        part2_tool_assessment=ToolAssessment(
            required_tools=[], overall_tool_completeness=1.0, risks=[]
        ),
    )


def test_revise_attaches_feedback_and_invalidates_plan_cache(tmp_path):
    from types import SimpleNamespace as _SN

    class _Resp:
        content = REVISION_JSON
        model = "stub"
        finish_reason = "stop"

    class _LLM:
        async def chat(self, **kwargs):
            return _Resp()

    designer = _make_designer(_LLM(), tmp_path)
    design = _make_design()
    # 预置一个旧计划缓存，模拟"已批准过一次"的场景
    designer.plan_cache.set(str(design.design_id), {"stale": True})

    revised = asyncio.run(designer.revise_design(design, FEEDBACK))

    assert revised.user_comments == FEEDBACK, "反馈必须写入修订后的设计书"
    assert designer.plan_cache.get(str(design.design_id)) is None, "同 design_id 的旧计划缓存必须失效"


def test_generate_plan_prompt_includes_feedback(tmp_path):
    from types import SimpleNamespace as _SN

    class _Resp:
        content = PLAN_JSON
        model = "stub"
        finish_reason = "stop"

    captured = {}

    class _LLM:
        async def chat(self, *, messages, **kwargs):
            captured["user"] = messages[-1].content
            return _Resp()

    designer = _make_designer(_LLM(), tmp_path)
    design = _make_design()
    design.user_comments = FEEDBACK

    plan = asyncio.run(designer.generate_plan(design))

    assert FEEDBACK in captured["user"], "计划提示词必须包含审批反馈"
    # LLM 桩返回的计划里含反馈指定的文件名 → 全链路可携带用户约束
    assert any("llm_ws_test" in json.dumps(s.inputs) for seg in plan.segments for s in seg.steps)


def test_generate_plan_prompt_without_feedback_unchanged(tmp_path):
    class _Resp:
        content = PLAN_JSON
        model = "stub"
        finish_reason = "stop"

    captured = {}

    class _LLM:
        async def chat(self, *, messages, **kwargs):
            captured["user"] = messages[-1].content
            return _Resp()

    designer = _make_designer(_LLM(), tmp_path)
    design = _make_design()

    asyncio.run(designer.generate_plan(design))
    assert "用户审批反馈" not in captured["user"]
